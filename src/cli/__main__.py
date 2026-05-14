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
import sys

from src.auto_updater import APP_VERSION
from src.cli._common import EXIT_INTERNAL


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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

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
