#!/usr/bin/env python3
"""Lightweight grep pass for a subset of the codegen-smell checklist.

This is the CI-runnable subset of the longer ``ai-codegen-smell-audit``
review. The full review needs an LLM to apply calibration rules
(trust boundaries, documented intent, house style, etc.) — pure grep
cannot do that. So this script only runs the four checks whose
greppable form has the lowest false-positive rate, and even then
splits them into a *gating* tier (fails the job when the count is
non-zero) and a *warning* tier (printed but never fails the job).

Tiers
-----

Gating (exit non-zero on any hit):

* ``phantom-todos`` — ``# TODO`` / ``# FIXME`` / ``# XXX`` / ``# HACK``
  without an owner tag like ``TODO(name, date)`` and without an issue
  link like ``#123``. These are easy to write, hard to action, and the
  repo's policy is zero of them in tracked source. False positives are
  near zero because the owner-or-issue exception covers every
  legitimate use.
* ``swallowed-errors`` — bare ``except:`` and ``except BaseException:``
  followed by ``pass`` or ``return None``. The broader pattern
  ``except Exception: pass`` is intentionally NOT gated because the
  repo has many legitimate uses of it for best-effort cleanup, GUI
  teardown, and optional imports — flagging those would be a wall of
  false positives. Bare excepts, in contrast, catch ``KeyboardInterrupt``
  and ``SystemExit`` too and are almost never what the author wanted.

Warning (printed in the summary, does not fail the job):

* ``defensive-checks-for-impossible-cases`` — a function whose first
  body line is ``if <param> is None`` where the parameter's type
  annotation does not include ``None`` / ``Optional``. Grep cannot
  reliably parse Python type expressions, so this check emits
  occurrences and lets a human triage. Known limitation: the check
  only fires when ``if x is None`` is the literal first line of the
  function body. A docstring or any other statement before the guard
  escapes detection — this is the price of not parsing the AST.
* ``over-typed-primitives`` — counts uses of ``NewType(``, ``Literal[``,
  and ``TypedDict`` in src/. A handful is fine; a sudden spike is
  worth a look. Explicitly excluded: the ``typing`` import line itself
  (``from typing import ... NewType ...``).

Why these four and not all ten
------------------------------

The other six checks in the skill (``stylistic-drift-within-file``,
``paraphrase-comments``, ``single-use-helpers``,
``generic-names-in-domain-context``, ``mirror-tests``,
``duplicated-helpers``) need semantic reasoning that grep does not
provide — "is this name generic *for the domain of this file*",
"is this helper called more than once *in this module*", "does this
test assert behaviour or just restate the implementation". Running
those checks without a model would produce so many false positives
that the human reviewer would learn to ignore the report.

Usage
-----

    python scripts/check_codegen_smells.py [--root src] [--summary PATH]

Exits 0 on success, 1 if any gated check has hits. Always writes the
markdown report to stdout, and to ``--summary`` if given (CI uses this
to populate ``$GITHUB_STEP_SUMMARY``).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


# --- Patterns -------------------------------------------------------

# Phantom TODO: a TODO/FIXME/XXX/HACK comment without either an owner
# tag (`(name, date)` or `@name`) or an issue link (`#123` after a
# space or paren) on the same line. Examples:
#   "# TODO: handle unicode"            → phantom (no owner, no link)
#   "# TODO(numminen, 2026-Q3): drop"   → OK (paren-style owner tag)
#   "# TODO @numminen: drop"            → OK (@-style owner tag)
#   "# TODO drop legacy alias (#42)"    → OK (issue link present)
# The `#42` link regex requires whitespace or `(` before the `#` so
# incidental hash-number tokens in prose ("step #1 first") do not
# silently mark the TODO as legitimate.
_PHANTOM_TODO_KEYWORD = re.compile(
    r"(?P<prefix>#\s*)(?P<kw>TODO|FIXME|XXX|HACK)\b(?P<rest>[^\n]*)",
)
_OWNER_TAG = re.compile(r"^\s*(\([^)]+\)|@\w+)")
_ISSUE_LINK = re.compile(r"(?:^|[\s(])#\d+\b")


# Bare except + immediate swallow. Matches:
#     except:
#         pass
#     except BaseException:
#         return None
# Also tolerates blank lines and comment lines between the handler
# header and the swallowing statement, so the obvious bypass
# `except:\n    # silence\n    pass` is still caught.
# Does NOT match ``except Exception:`` — see module docstring for why.
_BARE_EXCEPT_SWALLOW = re.compile(
    r"^(?P<indent>\s*)except(\s+BaseException(\s+as\s+\w+)?)?\s*:\s*\n"
    r"(?:(?P=indent)[ \t]+\#[^\n]*\n|[ \t]*\n)*"
    r"(?P=indent)\s+(pass|return(\s+None)?)\s*(\#[^\n]*)?$",
    re.MULTILINE,
)


# Defensive ``is None`` check on a parameter whose annotation does NOT
# include ``None`` / ``Optional``. The parameter list is captured by
# balancing brackets manually (see ``_extract_param_annotation``)
# because a regex like ``[^,]+`` breaks on annotations that contain
# commas (``Dict[str, int]``, ``Tuple[int, ...]``,
# ``Callable[[int, int], None]``).
# False positives this still emits:
#   * type aliases that resolve to Optional but don't say "None" in the
#     annotation (rare in this repo)
#   * defensive checks at documented trust boundaries (the SKILL says
#     to immunise these with a comment — grep can't see the comment
#     cheaply, so we let the warning fire and let a human dismiss it)
_DEFENSIVE_NONE_CHECK = re.compile(
    r"^def\s+\w+\s*\((?P<params>[^)]*(?:\([^)]*\)[^)]*)*)\)\s*(->\s*[^:]+)?:\s*\n"
    r"\s+if\s+(?P<arg>\w+)\s+is\s+None\b",
    re.MULTILINE,
)


# Over-typed primitives. Word-boundary-anchored so ``class MyTypedDict:``
# does NOT trip the ``TypedDict`` rule, and inline comments containing
# ``NewType(`` style references are stripped before matching. String
# literals containing these names will still false-positive — that is
# a documented limitation; the warning tier never gates so the cost is
# only a noisier step summary.
_OVERTYPE_PATTERNS = (
    re.compile(r"\bNewType\("),
    re.compile(r"\bLiteral\["),
    re.compile(r"\bTypedDict\b"),
)
_TYPING_IMPORT_LINE = re.compile(r"^\s*(from\s+typing\s+import|import\s+typing)\b")


# --- Data -----------------------------------------------------------


@dataclass
class Finding:
    check: str
    path: Path
    line: int
    snippet: str


@dataclass
class CheckResult:
    name: str
    findings: list[Finding] = field(default_factory=list)
    gating: bool = False


# --- Per-check scanners --------------------------------------------


def scan_phantom_todos(text: str, path: Path) -> list[Finding]:
    hits: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = _PHANTOM_TODO_KEYWORD.search(line)
        if not m:
            continue
        rest = m.group("rest")
        if _OWNER_TAG.match(rest):
            continue
        if _ISSUE_LINK.search(line):
            continue
        hits.append(
            Finding(
                check="phantom-todos",
                path=path,
                line=lineno,
                snippet=line.strip()[:120],
            )
        )
    return hits


def scan_swallowed_errors(text: str, path: Path) -> list[Finding]:
    hits: list[Finding] = []
    for m in _BARE_EXCEPT_SWALLOW.finditer(text):
        lineno = text.count("\n", 0, m.start()) + 1
        snippet = text[m.start():m.end()].splitlines()[0].strip()[:120]
        hits.append(
            Finding(
                check="swallowed-errors",
                path=path,
                line=lineno,
                snippet=snippet,
            )
        )
    return hits


def _extract_param_annotation(params: str, arg: str) -> str | None:
    """Return the type annotation of ``arg`` inside a parameter list,
    or ``None`` if the parameter has no annotation.

    The naive ``\\b{arg}\\s*:\\s*([^,]+)`` regex fails on annotations
    that themselves contain commas (``Dict[str, int]``,
    ``Callable[[int, int], None]``, ``Tuple[int, ...]``) — it stops at
    the first comma. This helper walks the string with a manual
    bracket counter so the annotation is captured up to the next
    top-level comma (or the end of the parameter list).
    """
    pat = re.compile(rf"\b{re.escape(arg)}\s*:\s*")
    m = pat.search(params)
    if not m:
        return None
    start = m.end()
    depth = 0
    for i in range(start, len(params)):
        ch = params[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return params[start:i]
            depth -= 1
        elif ch == "," and depth == 0:
            return params[start:i]
    return params[start:]


def scan_defensive_none(text: str, path: Path) -> list[Finding]:
    hits: list[Finding] = []
    for m in _DEFENSIVE_NONE_CHECK.finditer(text):
        params = m.group("params")
        arg = m.group("arg")
        annotation = _extract_param_annotation(params, arg)
        if annotation is None:
            # Parameter has no type annotation at all — not a smell
            # we can confirm by grep. Skip.
            continue
        if "None" in annotation or "Optional" in annotation:
            # Annotation already admits None — the guard is legitimate.
            continue
        lineno = text.count("\n", 0, m.start()) + 1
        snippet = text[m.start():m.end()].splitlines()[0].strip()[:120]
        hits.append(
            Finding(
                check="defensive-checks-for-impossible-cases",
                path=path,
                line=lineno,
                snippet=snippet,
            )
        )
    return hits


def scan_over_typed(text: str, path: Path) -> list[Finding]:
    hits: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _TYPING_IMPORT_LINE.match(line):
            continue
        # Strip inline comments. A `#` inside a string literal will be
        # truncated too — documented as a known false-negative shape;
        # the warning tier never gates, so the cost is bounded.
        code, _, _ = line.partition("#")
        for pat in _OVERTYPE_PATTERNS:
            if pat.search(code):
                hits.append(
                    Finding(
                        check="over-typed-primitives",
                        path=path,
                        line=lineno,
                        snippet=line.strip()[:120],
                    )
                )
                break
    return hits


# --- Driver ---------------------------------------------------------


def iter_python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def run_checks(root: Path) -> list[CheckResult]:
    results = [
        CheckResult(name="phantom-todos", gating=True),
        CheckResult(name="swallowed-errors", gating=True),
        CheckResult(name="defensive-checks-for-impossible-cases", gating=False),
        CheckResult(name="over-typed-primitives", gating=False),
    ]
    by_name = {r.name: r for r in results}

    for path in iter_python_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        by_name["phantom-todos"].findings.extend(scan_phantom_todos(text, path))
        by_name["swallowed-errors"].findings.extend(scan_swallowed_errors(text, path))
        by_name["defensive-checks-for-impossible-cases"].findings.extend(
            scan_defensive_none(text, path)
        )
        by_name["over-typed-primitives"].findings.extend(scan_over_typed(text, path))
    return results


def render_report(root: Path, results: list[CheckResult]) -> str:
    lines: list[str] = []
    lines.append("# Codegen smell — lightweight grep pass")
    lines.append("")
    lines.append(f"Scope: `{root}` (recursive `.py` files).")
    lines.append("")
    lines.append(
        "This is a grep-only subset of the full `ai-codegen-smell-audit` "
        "skill. It runs four pattern checks; six other checks in that "
        "skill need semantic reasoning and are not included here."
    )
    lines.append("")

    gating_total = sum(len(r.findings) for r in results if r.gating)
    warning_total = sum(len(r.findings) for r in results if not r.gating)
    lines.append(
        f"**Totals:** {gating_total} gating hit(s), {warning_total} "
        f"warning hit(s)."
    )
    lines.append("")

    for r in results:
        tier = "gating" if r.gating else "warning"
        lines.append(f"## `{r.name}` ({tier}) — {len(r.findings)} hit(s)")
        lines.append("")
        if not r.findings:
            lines.append("_None._")
            lines.append("")
            continue
        lines.append("| Location | Snippet |")
        lines.append("|---|---|")
        for f in r.findings[:50]:
            try:
                rel = f.path.relative_to(root.parent)
            except ValueError:
                rel = f.path
            snippet = f.snippet.replace("|", "\\|")
            lines.append(f"| `{rel.as_posix()}:{f.line}` | `{snippet}` |")
        if len(r.findings) > 50:
            lines.append(f"| ... | _{len(r.findings) - 50} more truncated_ |")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("src"),
        help="Directory to scan (default: src).",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="If given, also write the markdown report to this path "
        "(used by CI to populate $GITHUB_STEP_SUMMARY).",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if not root.is_dir():
        print(f"error: --root {root} is not a directory", file=sys.stderr)
        return 2

    results = run_checks(root)
    report = render_report(root, results)
    print(report)

    if args.summary is not None:
        args.summary.write_text(report, encoding="utf-8")

    gating_hits = sum(len(r.findings) for r in results if r.gating)
    return 1 if gating_hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
