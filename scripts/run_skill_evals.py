#!/usr/bin/env python3
"""Execute skill evals against a live model and grade them pass/fail.

`.claude/skills/*/evals/evals.json` records, per skill, a set of prompts plus
the `expected_output` behaviour. `tests/test_skill_evals.py` validates the
SCHEMA of those files but never RUNS them — so the evals are documentation,
not a regression signal. This harness runs them:

  1. CANDIDATE — send the skill's SKILL.md as system context plus the eval
     `prompt`, capture the model's response.
  2. JUDGE — ask the model whether the candidate response demonstrates the
     eval's `expected_output` (structured pass/fail + reason).

It is an APPROXIMATION: a skill in production runs inside a Claude Code session
with tools, not a bare API call. But it converts the prose specs into an
executable check that catches a skill whose guidance has drifted away from its
own evals.

COST / OPT-IN. Real model calls cost money, so this is deliberately NOT wired
into CI and never runs by default:

  * the `anthropic` SDK is imported lazily and is intentionally NOT in
    requirements.txt (the frozen app bundle must not carry it);
  * with no SDK or no ANTHROPIC_API_KEY it prints how to enable it and exits 0;
  * it prints the call count up front so the cost is visible before you run it.

Usage:
    pip install anthropic && export ANTHROPIC_API_KEY=...
    python scripts/run_skill_evals.py                  # all skills
    python scripts/run_skill_evals.py --skill release-cut
    python scripts/run_skill_evals.py --model claude-opus-4-8
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
DEFAULT_MODEL = "claude-opus-4-8"

_JUDGE_SYSTEM = (
    "You are a strict grader for an AI 'skill' (a runbook an assistant "
    "follows). You are given the behaviour the skill is supposed to produce "
    "for a prompt (EXPECTED) and what an assistant actually produced "
    "(CANDIDATE). Decide whether the candidate demonstrates the expected "
    "behaviour. Judge intent and key steps, not exact wording. Be strict: if "
    "the candidate omits a load-bearing step or contradicts the expected "
    "behaviour, it fails."
)

_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["passed", "reason"],
    "additionalProperties": False,
}


def discover_skill_evals(only: Optional[str] = None) -> list[tuple[str, Path, list[dict]]]:
    """Return (skill_name, skill_md_path, evals) for every skill with evals."""
    out: list[tuple[str, Path, list[dict]]] = []
    for evals_file in sorted(SKILLS_DIR.glob("*/evals/evals.json")):
        skill_name = evals_file.parent.parent.name
        if only and skill_name != only:
            continue
        skill_md = evals_file.parent.parent / "SKILL.md"
        if not skill_md.exists():
            continue
        data = json.loads(evals_file.read_text(encoding="utf-8"))
        evals = data.get("evals", [])
        if evals:
            out.append((skill_name, skill_md, evals))
    return out


def _extract_text(response: Any) -> str:
    """Join the text blocks of a Messages API response."""
    return "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )


def _candidate(client: Any, skill_md: str, prompt: str, model: str) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=skill_md,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_text(response)


def grade(client: Any, expected: str, candidate: str, model: str) -> tuple[bool, str]:
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=_JUDGE_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"EXPECTED:\n{expected}\n\nCANDIDATE:\n{candidate}",
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": _JUDGE_SCHEMA}},
    )
    raw = _extract_text(response)
    try:
        verdict = json.loads(raw)
        return bool(verdict["passed"]), str(verdict.get("reason", ""))
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        # A malformed verdict shouldn't crash the run — count it as a failure
        # so it's visible rather than silently swallowed.
        return False, f"could not parse judge verdict ({exc}): {raw[:200]}"


def run_eval(
    client: Any, skill_md: str, eval_case: dict, model: str
) -> dict:
    candidate = _candidate(client, skill_md, eval_case["prompt"], model)
    passed, reason = grade(client, eval_case["expected_output"], candidate, model)
    return {
        "id": eval_case.get("id"),
        "name": eval_case.get("name", ""),
        "passed": passed,
        "reason": reason,
    }


def _make_client() -> Optional[Any]:
    """Build an anthropic client, or None if the SDK / key is unavailable."""
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "run_skill_evals: ANTHROPIC_API_KEY is not set — skipping (this is "
            "an opt-in, metered tool). Set the key and re-run to grade evals.",
            file=sys.stderr,
        )
        return None
    try:
        import anthropic  # lazy: not a project dependency
    except ImportError:
        print(
            "run_skill_evals: the 'anthropic' SDK is not installed — skipping. "
            "Run `pip install anthropic` to enable eval execution.",
            file=sys.stderr,
        )
        return None
    return anthropic.Anthropic()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run skill evals against a model.")
    parser.add_argument("--skill", help="grade only this skill (default: all)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)

    suites = discover_skill_evals(args.skill)
    if not suites:
        print("No skill evals found.", file=sys.stderr)
        return 0

    total_evals = sum(len(evals) for _, _, evals in suites)
    print(
        f"About to grade {total_evals} eval(s) across {len(suites)} skill(s) "
        f"with {args.model}. Each eval makes 2 model calls (candidate + judge) "
        f"— this costs money."
    )

    client = _make_client()
    if client is None:
        return 0  # opt-in skip — not a failure

    failures = 0
    for skill_name, skill_md_path, evals in suites:
        skill_md = skill_md_path.read_text(encoding="utf-8")
        print(f"\n## {skill_name}")
        for case in evals:
            try:
                result = run_eval(client, skill_md, case, args.model)
            except Exception as exc:  # noqa: BLE001 - one model error shouldn't abort the run
                result = {
                    "name": case.get("name", ""),
                    "passed": False,
                    "reason": f"error running eval: {exc}",
                }
            mark = "PASS" if result["passed"] else "FAIL"
            print(f"  [{mark}] {result['name']}: {result['reason']}")
            if not result["passed"]:
                failures += 1

    print(f"\n{total_evals - failures}/{total_evals} evals passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
