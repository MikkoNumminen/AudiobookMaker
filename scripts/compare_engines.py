#!/usr/bin/env python
"""compare_engines.py — synthesize the SAME text through every available engine.

Runs one short text snippet through each TTS engine that is currently
available on the machine, writing one labeled output file per engine so you
can listen to them side by side and pick the best one for a given language.

Why this exists
---------------
Choosing an engine is a listening decision: the only way to tell whether
Edge-TTS, Piper, or Chatterbox sounds best for a particular voice and
language is to hear the same words from each. Doing that by hand means
running the converter once per engine and remembering which file came from
where. This script does it in one pass and names every output
``<engine_id>__<voice>.<ext>`` so the source is never ambiguous.

What it does
------------
1. Enumerates every registered engine via ``src.tts_base.list_engines``
   (populated by importing ``src.engine_registry``).
2. Calls each engine's ``check_status()``. Unavailable engines are skipped
   with the engine's own printed reason — the script never crashes on a
   missing dependency or absent GPU venv.
3. For each AVAILABLE engine, picks the engine's default voice for the
   requested language and synthesizes the text to
   ``<out>/<engine_id>__<voice>.<ext>``.
4. Prints a summary table: engine id, status, and either the output path or
   the skip reason.

Output location
---------------
Defaults to an ``engine_compare`` subdirectory under
``src.synthesis_orchestrator.default_output_dir()`` — the canonical dev
output root (``.local/audiobooks/`` in dev mode, next-to-exe when frozen).
It is never the repo root or ``dist/``. Override with ``--out``.

Usage
-----
    # list what WOULD be synthesized, run nothing (safe, no GPU, no network):
    python scripts/compare_engines.py --dry-run

    # synthesize the built-in sample through every available engine:
    python scripts/compare_engines.py

    # custom text and language, custom output dir:
    python scripts/compare_engines.py --text "Hei maailma." --language fi
    python scripts/compare_engines.py --input snippet.txt --out ./.local/scratch/ab

Resource note
-------------
``--dry-run`` performs NO synthesis: it imports nothing heavy, touches no
GPU, and makes no network calls. A real (non-dry-run) run will synthesize
through whatever engines report available — including GPU engines
(Chatterbox) and online engines (Edge-TTS) — so only run it without
``--dry-run`` when you actually want audio and the machine is free.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Make ``src.*`` importable when the script is run from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Importing engine_registry populates the engine registry as a side effect,
# so list_engines() below sees the full set.
from src import engine_registry  # noqa: E402,F401  (registers engines on import)
from src.synthesis_orchestrator import default_output_dir  # noqa: E402
from src.tts_base import TTSEngine, list_engines  # noqa: E402


# A short, generic, copyright-free snippet. Mixed Finnish/English-safe
# punctuation so the same default reads acceptably in either language.
DEFAULT_SAMPLE_TEXT = (
    "This is a short sample sentence used to compare every available "
    "text-to-speech engine side by side."
)

# Every engine in this app synthesizes to MP3 (the TTSEngine.synthesize
# contract writes an MP3, and the Chatterbox subprocess produces MP3 too),
# so a single extension covers all of them. Kept as a function rather than a
# bare constant so a future engine with a different container has one obvious
# place to special-case.
def _output_ext(engine: TTSEngine) -> str:
    """Return the output file extension (no dot) an engine writes."""
    return "mp3"


@dataclass
class EnginePlan:
    """One engine's place in the comparison run.

    ``available`` mirrors the engine's ``check_status().available`` but is
    only ``True`` when a default voice for the language also exists — an
    available engine with no voice for the language cannot be synthesized,
    so it is reported as a skip with an actionable reason.
    """

    engine_id: str
    available: bool
    voice: Optional[str] = None
    output_path: Optional[Path] = None
    skip_reason: Optional[str] = None
    uses_subprocess: bool = False


def _safe_voice_label(voice_id: str) -> str:
    """Make a voice id safe to embed in a filename.

    Voice ids are mostly already filename-safe (``fi_FI-harri-medium``,
    ``grandmom``), but Edge ids carry no path separators either. This guard
    replaces anything outside ``[A-Za-z0-9._-]`` with ``-`` so an exotic
    third-party engine id can never escape the output directory or produce
    an unopenable file.
    """
    return "".join(c if (c.isalnum() or c in "._-") else "-" for c in voice_id)


def plan_engine(engine: TTSEngine, language: str, out_dir: Path) -> EnginePlan:
    """Decide what (if anything) to synthesize for one engine.

    Never raises: a misbehaving ``check_status`` / ``default_voice`` is
    caught and turned into a skip with the exception text as the reason, so
    one bad engine can't sink the whole comparison.
    """
    engine_id = getattr(engine, "id", engine.__class__.__name__)
    uses_subprocess = bool(getattr(engine, "uses_subprocess", False))

    try:
        status = engine.check_status()
    except Exception as exc:  # noqa: BLE001 — a bad engine must not crash the run
        return EnginePlan(
            engine_id=engine_id,
            available=False,
            skip_reason=f"check_status() raised: {exc}",
            uses_subprocess=uses_subprocess,
        )

    if not status.available:
        reason = status.reason.strip() or "engine reported not available"
        # check_status reasons can be multi-line (bilingual help blocks);
        # collapse to the first line for the one-row summary.
        reason = reason.splitlines()[0]
        return EnginePlan(
            engine_id=engine_id,
            available=False,
            skip_reason=reason,
            uses_subprocess=uses_subprocess,
        )

    try:
        voice = engine.default_voice(language)
    except Exception as exc:  # noqa: BLE001
        return EnginePlan(
            engine_id=engine_id,
            available=False,
            skip_reason=f"default_voice({language!r}) raised: {exc}",
            uses_subprocess=uses_subprocess,
        )

    if not voice:
        return EnginePlan(
            engine_id=engine_id,
            available=False,
            skip_reason=f"no default voice for language '{language}'",
            uses_subprocess=uses_subprocess,
        )

    ext = _output_ext(engine)
    filename = f"{engine_id}__{_safe_voice_label(voice)}.{ext}"
    return EnginePlan(
        engine_id=engine_id,
        available=True,
        voice=voice,
        output_path=out_dir / filename,
        uses_subprocess=uses_subprocess,
    )


def build_plans(language: str, out_dir: Path) -> list[EnginePlan]:
    """Return one :class:`EnginePlan` per registered engine, in registry order."""
    return [plan_engine(engine, language, out_dir) for engine in list_engines()]


def _synthesize_inprocess(
    engine: TTSEngine,
    text: str,
    plan: EnginePlan,
    language: str,
) -> None:
    """Run an in-process engine's ``synthesize()`` into ``plan.output_path``."""
    assert plan.output_path is not None and plan.voice is not None
    engine.synthesize(
        text=text,
        output_path=str(plan.output_path),
        voice_id=plan.voice,
        language=language,
    )


def _synthesize_subprocess(
    engine: TTSEngine,
    text: str,
    plan: EnginePlan,
    language: str,
) -> None:
    """Synthesize a subprocess (bridge) engine via the Chatterbox runner.

    Subprocess engines (Chatterbox) raise from ``synthesize()`` by contract —
    their work runs in a separate interpreter. We assemble the runner through
    the orchestrator's ``build_chatterbox_runner`` and drive it to completion,
    then move its produced MP3 to the labeled comparison path.
    """
    assert plan.output_path is not None
    from src.synthesis_orchestrator import (
        ChatterboxRequest,
        build_chatterbox_runner,
    )

    runner_script = _REPO_ROOT / "scripts" / "generate_chatterbox_audiobook.py"
    request = ChatterboxRequest(
        input_mode="text",
        input_text=text,
        language=language,
        output_path_hint=str(plan.output_path),
        output_basename_override=plan.output_path.stem,
    )
    sub_plan = build_chatterbox_runner(
        request, runner_script, plan.output_path.parent
    )
    runner = sub_plan.runner
    error_line: Optional[str] = None
    try:
        runner.start()
        while not runner.finished:
            event = runner.poll_event(timeout=0.1)
            if event is not None and event.kind == "error":
                error_line = event.raw_line or "engine reported an error"
        runner.join()
    finally:
        sub_plan.cleanup()
    if error_line is not None:
        raise RuntimeError(error_line)


def synthesize_plan(
    engine: TTSEngine,
    text: str,
    plan: EnginePlan,
    language: str,
) -> None:
    """Synthesize one available engine, routing subprocess engines correctly."""
    if plan.uses_subprocess:
        _synthesize_subprocess(engine, text, plan, language)
    else:
        _synthesize_inprocess(engine, text, plan, language)


def _print_summary(plans: list[EnginePlan], dry_run: bool) -> None:
    """Print the engine / status / output-or-reason table."""
    header_action = "would write" if dry_run else "output / reason"
    rows: list[tuple[str, str, str]] = []
    for plan in plans:
        if plan.available:
            status = "available"
            detail = str(plan.output_path)
        else:
            status = "skipped"
            detail = plan.skip_reason or "unavailable"
        rows.append((plan.engine_id, status, detail))

    id_w = max([len("engine")] + [len(r[0]) for r in rows], default=len("engine"))
    st_w = max([len("status")] + [len(r[1]) for r in rows], default=len("status"))

    print()
    print(f"{'engine':<{id_w}}  {'status':<{st_w}}  {header_action}")
    print(f"{'-' * id_w}  {'-' * st_w}  {'-' * len(header_action)}")
    for engine_id, status, detail in rows:
        print(f"{engine_id:<{id_w}}  {status:<{st_w}}  {detail}")
    print()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="compare_engines.py",
        description=(
            "Synthesize the same text through every available TTS engine and "
            "save labeled outputs side by side for manual A/B review."
        ),
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument(
        "--text",
        default=None,
        help="Text to synthesize. Defaults to a short built-in sample.",
    )
    src.add_argument(
        "--input",
        default=None,
        metavar="FILE",
        help="Read the text to synthesize from a UTF-8 text file.",
    )
    p.add_argument(
        "--language",
        default="fi",
        help="Short language code for voice selection (default: fi).",
    )
    p.add_argument(
        "--out",
        default=None,
        metavar="DIR",
        help=(
            "Output directory. Default: an 'engine_compare' subdir under the "
            "app's canonical output directory (never the repo root or dist/)."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "List what WOULD be synthesized per engine and exit. Runs no "
            "synthesis: no GPU, no network, no files written."
        ),
    )
    return p.parse_args(argv)


def _resolve_text(args: argparse.Namespace) -> str:
    """Return the text to synthesize, honoring --text / --input / default."""
    if args.input:
        return Path(args.input).read_text(encoding="utf-8").strip()
    if args.text:
        return args.text.strip()
    return DEFAULT_SAMPLE_TEXT


def _resolve_out_dir(args: argparse.Namespace) -> Path:
    """Return the comparison output directory (default under the canonical root)."""
    if args.out:
        return Path(args.out).expanduser().resolve()
    return (default_output_dir() / "engine_compare").resolve()


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    text = _resolve_text(args)
    if not text:
        print("Error: no text to synthesize (empty --text/--input).",
              file=sys.stderr)
        return 1

    out_dir = _resolve_out_dir(args)
    plans = build_plans(args.language, out_dir)

    available = [p for p in plans if p.available]
    skipped = [p for p in plans if not p.available]

    print(f"Comparing {len(plans)} engine(s) for language '{args.language}'.")
    print(f"Output directory: {out_dir}")
    if args.dry_run:
        print("Mode: DRY RUN - no synthesis will be performed.")
    print(f"Text ({len(text)} chars): {text[:80]!r}"
          + ("..." if len(text) > 80 else ""))

    for plan in available:
        tag = " (subprocess)" if plan.uses_subprocess else ""
        if args.dry_run:
            print(f"  [plan] {plan.engine_id}: voice={plan.voice!r}{tag} "
                  f"-> {plan.output_path}")
        else:
            print(f"  [synth] {plan.engine_id}: voice={plan.voice!r}{tag} "
                  f"-> {plan.output_path}")
    for plan in skipped:
        print(f"  [skip] {plan.engine_id}: {plan.skip_reason}")

    if not args.dry_run and available:
        out_dir.mkdir(parents=True, exist_ok=True)
        by_id = {getattr(e, "id", e.__class__.__name__): e for e in list_engines()}
        for plan in available:
            engine = by_id.get(plan.engine_id)
            if engine is None:  # registry changed mid-run; defensive only
                plan.available = False
                plan.skip_reason = "engine vanished from registry"
                continue
            try:
                synthesize_plan(engine, text, plan, args.language)
            except Exception as exc:  # noqa: BLE001 — one failure must not stop the rest
                plan.available = False
                plan.skip_reason = f"synthesis failed: {exc}"
                print(f"  [fail] {plan.engine_id}: {exc}", file=sys.stderr)

    _print_summary(plans, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
