"""python -m src.cli entry point.

Parses argv, dispatches to the appropriate subcommand module.
Each subcommand module exposes:
    add_parser(subparsers) -> None
    run(args) -> int          # via args.func

Exit codes are documented on every subcommand's --help and in docs/CLI.md:
    0  success
    1  bad input / validation failure
    2  missing dependency (engine not installed, ffmpeg missing)
    3  user cancelled (Ctrl-C)
    4  runtime failure (network, GPU, synthesis error)
    5  unexpected internal error
"""

from __future__ import annotations

import argparse
import logging
import sys

from src.auto_updater import APP_VERSION
from src.cli._common import EXIT_INTERNAL
from src.ffmpeg_path import setup_ffmpeg_path

# Configure ffmpeg before any synthesis path can import pydub. Matches
# what src/main.py and src/launcher.py do for the GUI entry points so
# the CLI honours dist/ffmpeg/ in dev mode and the bundled ffmpeg.exe
# next to the frozen .exe in installed mode.
setup_ffmpeg_path()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audiobookmaker",
        description=(
            "AudiobookMaker — convert books to speech from the command line.\n\n"
            "Subcommands:\n"
            "  convert     Convert PDF/EPUB/TXT to MP3\n"
            "  sample      Synthesize a ~500 char preview\n"
            "  preview     Speak a short string through the system audio\n"
            "  voices      List available voices\n"
            "  engines     List, install, remove, or check TTS engines\n"
            "  packs       Manage installed voice packs\n"
            "  config      Show, set, or reset persistent user config\n"
            "  update      Check for or install a new app version\n"
            "  doctor      Check system requirements\n\n"
            "Exit codes:\n"
            "  0  success\n"
            "  1  bad input / validation failure\n"
            "  2  missing dependency (engine not installed, ffmpeg missing)\n"
            "  3  user cancelled (Ctrl-C)\n"
            "  4  runtime failure (network, GPU, synthesis error)\n"
            "  5  unexpected internal error\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"AudiobookMaker {APP_VERSION}",
    )

    log_group = parser.add_mutually_exclusive_group()
    log_group.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help=(
            "Increase verbosity. -v → INFO, -vv → DEBUG. "
            "Mutually exclusive with --log-level."
        ),
    )
    log_group.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error"],
        default=None,
        metavar="LEVEL",
        help=(
            "Set log level explicitly (debug/info/warning/error). "
            "Mutually exclusive with -v/--verbose. Default: warning."
        ),
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = False

    # Import each subcommand module and let it register its parser.
    from src.cli import (
        config,
        convert,
        doctor,
        engines,
        packs,
        preview,
        sample,
        update,
        voices,
    )

    convert.add_parser(subparsers)
    sample.add_parser(subparsers)
    preview.add_parser(subparsers)
    voices.add_parser(subparsers)
    engines.add_parser(subparsers)
    packs.add_parser(subparsers)
    config.add_parser(subparsers)
    update.add_parser(subparsers)
    doctor.add_parser(subparsers)

    # Short aliases for the most-used subcommands are declared via the
    # public ``aliases=`` parameter on each subcommand's add_parser()
    # call (convert → c, sample → s, preview → p). No private-API
    # manipulation needed.

    return parser


def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 on stdout/stderr so non-ASCII names (e.g. "Isoäiti") don't
    # raise UnicodeEncodeError on Windows cmd.exe with the default cp1252 codepage.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    parser = _build_parser()
    args = parser.parse_args(argv)

    # Configure root logger from -v / --log-level before dispatching.
    if args.log_level is not None:
        # Explicit --log-level wins.
        _level = getattr(logging, args.log_level.upper())
    else:
        # -v count: 0 → WARNING, 1 → INFO, 2+ → DEBUG.
        _v = args.verbose or 0
        _level = logging.WARNING if _v == 0 else logging.INFO if _v == 1 else logging.DEBUG
    # force=True so this call wins even if an imported src/ module
    # already touched the root logger handler list during import.
    logging.basicConfig(
        level=_level,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.debug("verbosity: %s", logging.getLevelName(_level))

    if args.command is None:
        parser.print_help()
        return 0

    if not hasattr(args, "func"):
        # Subcommand registered but no sub-subcommand selected (e.g. bare
        # "audiobookmaker engines" without "list").  Each nested parser
        # already marks its sub-subparsers as required, so argparse would
        # have exited before reaching here — but guard defensively.
        parser.print_help()
        return 1

    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 3
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            return code
        return EXIT_INTERNAL
    except Exception as exc:
        print(f"Internal error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
