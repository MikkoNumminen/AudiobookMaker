"""voices subcommand — list available TTS voices.

Usage:
    audiobookmaker voices list [--engine ID] [--language fi|en] [--json] [--quiet]

Prints one row per voice with id, display name, language, and gender.
Filters by engine and/or language when flags are given.

Exit codes:
    0  success
    1  unknown engine id
    5  unexpected error
"""

from __future__ import annotations

import argparse
import json
import sys

from src.cli._common import EXIT_BAD_INPUT, EXIT_INTERNAL, EXIT_OK, add_output_mode_flags


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "voices",
        help="List available TTS voices.",
        description="Browse voices available in installed TTS engines.",
    )
    sub = p.add_subparsers(dest="voices_cmd", metavar="CMD")
    sub.required = True

    lst = sub.add_parser(
        "list",
        help="List available voices.",
        description=(
            "List voices across all engines, or filtered by engine / language.\n\n"
            "Exit codes:\n"
            "  0  success\n"
            "  1  unknown engine id\n"
            "  5  unexpected error\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    lst.add_argument(
        "--engine",
        metavar="ID",
        default=None,
        help="Filter to voices of a specific engine (e.g. edge, piper, chatterbox_fi).",
    )
    lst.add_argument(
        "--language",
        metavar="LANG",
        default=None,
        choices=["fi", "en"],
        help="Filter to voices for a specific language (fi or en).",
    )
    add_output_mode_flags(
        lst,
        json_help=(
            "Emit one voice object per line (NDJSON) with fields: "
            "engine, id, language, gender, display_name."
        ),
        quiet_help="Print only voice ids, one per line.",
    )
    lst.set_defaults(func=_run_list)


def run(args: argparse.Namespace) -> int:
    return args.func(args)


def _run_list(args: argparse.Namespace) -> int:
    json_mode: bool = getattr(args, "json", False)
    quiet: bool = getattr(args, "quiet", False)
    engine_filter: str | None = getattr(args, "engine", None)
    lang_filter: str | None = getattr(args, "language", None)

    try:
        from src import engine_registry  # noqa: F401
        from src.tts_base import get_engine, list_engines
    except Exception as exc:
        print(f"Error loading engine registry: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    if engine_filter is not None:
        eng = get_engine(engine_filter)
        if eng is None:
            print(f"Error: unknown engine '{engine_filter}'.", file=sys.stderr)
            print("Run 'audiobookmaker engines list' to see available engines.", file=sys.stderr)
            return EXIT_BAD_INPUT
        engines_to_query = [eng]
    else:
        engines_to_query = list_engines()

    rows = []
    languages_to_query = [lang_filter] if lang_filter else ["fi", "en"]

    seen_ids: set[tuple[str, str]] = set()
    for eng in engines_to_query:
        for lang in languages_to_query:
            try:
                voices = eng.list_voices(lang)
            except Exception:
                voices = []
            for voice in voices:
                key = (eng.id, voice.id)
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                rows.append({
                    "engine": eng.id,
                    "id": voice.id,
                    "display_name": voice.display_name,
                    "language": voice.language or lang,
                    "gender": voice.gender or "",
                })

    if json_mode:
        for row in rows:
            print(json.dumps(row), flush=True)
    elif quiet:
        for row in rows:
            print(row["id"], flush=True)
    else:
        _print_voices_table(rows)

    return EXIT_OK


def _print_voices_table(rows: list[dict]) -> None:
    if not rows:
        print("No voices found.")
        return
    print(f"  {'Engine':<18}  {'ID':<35}  {'Lang':<5}  {'Gender':<8}  Display name")
    print("  " + "-" * 90)
    for row in rows:
        print(
            f"  {row['engine']:<18}  {row['id']:<35}  "
            f"{row['language']:<5}  {row['gender']:<8}  {row['display_name']}"
        )
