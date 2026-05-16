"""report-bug subcommand — open a pre-filled GitHub bug-report URL.

Mirrors the "Report a bug" button in the GUI's Settings panel. Collects
the same context fields (app version, OS, active engine) and builds the
URL via :func:`src.bug_report.build_bug_report_url`.

Usage:
    audiobookmaker-cli report-bug           # print URL then open in browser
    audiobookmaker-cli report-bug --print   # print URL only, no browser
    audiobookmaker-cli report-bug --json    # emit {"url": ..., "fields": {...}}
    audiobookmaker-cli report-bug --quiet   # print URL only (same as --print)

Exit codes:
    0  success (URL printed, browser opened or fallback message shown)
    5  unexpected internal error (could not build the URL)
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import webbrowser

from src.auto_updater import APP_VERSION
from src.cli._common import EXIT_INTERNAL, EXIT_OK, add_output_mode_flags


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "report-bug",
        help="Open a pre-filled GitHub bug-report URL in the browser.",
        description=(
            "Build a GitHub 'new issue' URL pre-filled with app version, OS, "
            "and active engine, then open it in the default browser.\n\n"
            "The URL is always printed to stdout so you can copy it if the "
            "browser cannot be opened automatically.\n\n"
            "Exit codes:\n"
            "  0  success\n"
            "  5  unexpected internal error\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_output_mode_flags(p)
    p.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        default=False,
        help="Print the URL to stdout without opening a browser.",
    )
    p.set_defaults(func=run)


def _get_active_engine_id() -> str | None:
    """Return the active engine id from persisted config, or None."""
    try:
        from src.user_config import load_config
        cfg = load_config()
        return cfg.get("engine") or None
    except Exception:
        return None


def run(args: argparse.Namespace) -> int:
    json_mode: bool = getattr(args, "json", False)
    quiet: bool = getattr(args, "quiet", False)
    print_only: bool = getattr(args, "print_only", False)

    # Gather context — same fields the GUI handler passes.
    os_platform = platform.platform()
    engine_id = _get_active_engine_id()

    try:
        from src.bug_report import build_bug_report_url
        url = build_bug_report_url(
            app_version=APP_VERSION,
            engine_id=engine_id,
            os_platform=os_platform,
        )
    except Exception as exc:
        print(f"Error building bug-report URL: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    fields = {
        "app_version": APP_VERSION,
        "os_platform": os_platform,
        "engine_id": engine_id,
    }

    # --json wins over --quiet / --print when both are passed.
    if json_mode:
        print(json.dumps({"url": url, "fields": fields}), flush=True)
        return EXIT_OK

    # Both --quiet and --print: URL only, no browser.
    if quiet or print_only:
        print(url, flush=True)
        return EXIT_OK

    # Default: print URL for transparency, then attempt to open browser.
    print(url, flush=True)
    try:
        opened = webbrowser.open(url)
    except Exception:
        opened = False

    if not opened:
        print(
            "Could not open browser. Open the URL above manually.",
            file=sys.stderr,
        )

    return EXIT_OK
