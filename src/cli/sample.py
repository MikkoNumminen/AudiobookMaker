"""sample subcommand — synthesize ~500 chars as a quick preview.

Usage:
    audiobookmaker sample <input> [same flags as convert]

Reads the input file, extracts the first ~500 characters trimmed at a
sentence boundary, and synthesizes only that snippet. Useful for a
quick voice/engine preview before committing to a long full-book run.

Output path defaults to <output_stem>_sample.mp3.

Exit codes: same as convert (0/1/2/3/4/5).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.cli._common import (
    EXIT_BAD_INPUT,
    EXIT_INTERNAL,
    EXIT_OK,
    add_common_synthesis_flags,
    add_output_mode_flags,
    validate_input_path,
)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "sample",
        help="Synthesize a ~500 char preview of a book.",
        description=(
            "Convert the first ~500 characters of a book to MP3 as a quick\n"
            "quality check before running the full conversion.\n\n"
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
    p.add_argument(
        "input",
        metavar="INPUT",
        help="Path to a PDF, EPUB, or TXT file.",
    )
    add_common_synthesis_flags(p)
    p.add_argument(
        "--ref-audio",
        metavar="PATH",
        default=None,
        help="Reference audio file for voice-cloning engines.",
    )
    p.add_argument(
        "--voice-pack",
        metavar="PATH",
        default=None,
        help="Path to a voice pack directory (Chatterbox only).",
    )
    p.add_argument(
        "--chunk-chars",
        metavar="N",
        type=int,
        default=None,
        help="Characters per synthesis chunk (Chatterbox only; default 300).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would happen without synthesizing.",
    )
    add_output_mode_flags(p)
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    input_path = str(Path(args.input).expanduser())

    # Validate input file via the shared helper so the rules stay in
    # one place across convert and sample.
    code, msg = validate_input_path(input_path)
    if code != EXIT_OK:
        print(f"Error: {msg}", file=sys.stderr)
        return code

    if getattr(args, "dry_run", False):
        # For dry-run, delegate to convert.run() with the flag set.
        # sample_text doesn't matter since nothing is synthesized.
        from src.cli import convert
        return convert.run(args, sample_text="(dry-run sample)")

    # Read and extract the sample text from the input file.
    try:
        from src.synthesis_orchestrator import parse_book
        book = parse_book(input_path)
        full_text = book.full_text
    except Exception as exc:
        print(f"Error reading input file: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT

    try:
        from src.sample_helpers import extract_sample_text
        sample_text = extract_sample_text(full_text)
    except Exception as exc:
        print(f"Error extracting sample text: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    if not sample_text.strip():
        print("Error: input file appears to be empty.", file=sys.stderr)
        return EXIT_BAD_INPUT

    # Delegate to convert.run() with the pre-extracted sample text.
    from src.cli import convert
    return convert.run(args, sample_text=sample_text)
