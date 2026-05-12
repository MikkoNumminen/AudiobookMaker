"""engines subcommand — list registered TTS engines.

Usage:
    audiobookmaker engines list [--installed-only] [--json] [--quiet]

Prints one row per engine with id, display name, availability, and
a reason string when unavailable.

Exit codes:
    0  success
    5  unexpected error
"""

from __future__ import annotations

import argparse
import json
import sys

from src.cli._common import EXIT_INTERNAL, EXIT_OK, add_output_mode_flags


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "engines",
        help="List and inspect TTS engines.",
        description="Manage and inspect installed TTS engines.",
    )
    sub = p.add_subparsers(dest="engines_cmd", metavar="CMD")
    sub.required = True

    # engines list
    lst = sub.add_parser(
        "list",
        help="List all registered engines.",
        description=(
            "List every registered TTS engine with its availability.\n\n"
            "Exit codes:\n"
            "  0  success\n"
            "  5  unexpected error\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    lst.add_argument(
        "--installed-only",
        action="store_true",
        default=False,
        help="Only show engines that are currently available.",
    )
    add_output_mode_flags(lst)
    lst.set_defaults(func=_run_list)


def run(args: argparse.Namespace) -> int:
    return args.func(args)


def _run_list(args: argparse.Namespace) -> int:
    json_mode: bool = getattr(args, "json", False)
    quiet: bool = getattr(args, "quiet", False)
    installed_only: bool = getattr(args, "installed_only", False)

    try:
        from src import engine_registry  # noqa: F401
        from src.tts_base import list_engines
        engines = list_engines()
    except Exception as exc:
        print(f"Error loading engines: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    rows = []
    for eng in engines:
        try:
            status = eng.check_status()
            available = status.available
            reason = status.reason if not available else ""
        except Exception as exc:
            available = False
            reason = str(exc)

        if installed_only and not available:
            continue

        rows.append({
            "id": eng.id,
            "display_name": eng.display_name,
            "available": available,
            "reason": reason,
        })

    if json_mode:
        for row in rows:
            print(json.dumps(row), flush=True)
    elif quiet:
        for row in rows:
            print(row["id"], flush=True)
    else:
        _print_engines_table(rows)

    return EXIT_OK


def _print_engines_table(rows: list[dict]) -> None:
    if not rows:
        print("No engines found.")
        return
    print(f"  {'ID':<20}  {'Available':<10}  {'Name / Reason'}")
    print("  " + "-" * 72)
    for row in rows:
        avail = "yes" if row["available"] else "no"
        detail = row["display_name"] if row["available"] else row["reason"]
        print(f"  {row['id']:<20}  {avail:<10}  {detail}")
