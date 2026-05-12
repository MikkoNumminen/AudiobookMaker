"""Shared helpers for the AudiobookMaker CLI.

Keeps argparse boilerplate, config-precedence resolution, exit codes,
and ProgressEvent formatting in one place so every subcommand module
stays thin.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from src.launcher_bridge import ProgressEvent

# ---------------------------------------------------------------------------
# Exit codes (documented in --help)
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_BAD_INPUT = 1
EXIT_MISSING_DEP = 2
EXIT_CANCELLED = 3
EXIT_RUNTIME = 4
EXIT_INTERNAL = 5

# ---------------------------------------------------------------------------
# Config precedence: CLI flag > env var > config.json > default
# ---------------------------------------------------------------------------

# Env var names per flag:
#   --engine     AUDIOBOOKMAKER_ENGINE
#   --language   AUDIOBOOKMAKER_LANGUAGE
#   --voice      AUDIOBOOKMAKER_VOICE
#   --speed      AUDIOBOOKMAKER_SPEED
#   --output     AUDIOBOOKMAKER_OUTPUT


def resolve_str(
    flag_value: Optional[str],
    env_key: str,
    config_value: str,
    default: str = "",
) -> str:
    """Return the highest-priority non-empty value.

    Priority: CLI flag > environment variable > persisted config > default.
    """
    if flag_value is not None and flag_value != "":
        return flag_value
    env = os.environ.get(env_key, "")
    if env:
        return env
    if config_value:
        return config_value
    return default


# ---------------------------------------------------------------------------
# Argparse helpers — the five standard flags used by convert/sample
# ---------------------------------------------------------------------------


def add_common_synthesis_flags(parser: argparse.ArgumentParser) -> None:
    """Add --engine, --language, --voice, --speed, --output to a parser.

    These five flags have identical semantics across convert and sample.
    Each flag documents its env-var override and default so --help is
    the contract.
    """
    parser.add_argument(
        "--engine",
        metavar="ID",
        default=None,
        help=(
            "TTS engine to use (e.g. edge, piper, chatterbox_fi). "
            "Default from config; fallback: edge. "
            "Env: AUDIOBOOKMAKER_ENGINE."
        ),
    )
    parser.add_argument(
        "--language",
        metavar="LANG",
        default=None,
        choices=["fi", "en"],
        help=(
            "Language code: fi or en. "
            "Default from config; fallback: auto-detect from locale. "
            "Env: AUDIOBOOKMAKER_LANGUAGE."
        ),
    )
    parser.add_argument(
        "--voice",
        metavar="ID",
        default=None,
        help=(
            "Voice id (engine-specific). "
            "Default: engine's default voice for the chosen language. "
            "Env: AUDIOBOOKMAKER_VOICE."
        ),
    )
    parser.add_argument(
        "--speed",
        metavar="ADJ",
        default=None,
        help=(
            "Speed adjustment: -25%%, +0%%, +25%%, +50%%. "
            "Default from config; fallback: +0%%. "
            "Env: AUDIOBOOKMAKER_SPEED."
        ),
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        default=None,
        help=(
            "Output MP3 path. "
            "Default: <output_dir>/<book-stem>.mp3. "
            "Env: AUDIOBOOKMAKER_OUTPUT."
        ),
    )


def add_output_mode_flags(parser: argparse.ArgumentParser) -> None:
    """Add --json and --quiet output mode flags to a parser."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit one JSON object per line (ProgressEvent shape).",
    )
    group.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress progress; print only the final output path.",
    )


# ---------------------------------------------------------------------------
# ProgressEvent → stdout printer
# ---------------------------------------------------------------------------


def print_event(event: ProgressEvent, *, json_mode: bool, quiet: bool) -> None:
    """Print a ProgressEvent to stdout according to the active output mode.

    - json_mode: emit one JSON object per line (machine-readable).
    - quiet: suppress all output except the final path (done/error events).
    - default: human-readable, one line per meaningful event.
    """
    if json_mode:
        # Emit every field as a flat JSON object.
        obj = {
            "kind": event.kind,
            "raw_line": event.raw_line,
            "output_path": event.output_path,
            "total_done": event.total_done,
            "total_chunks": event.total_chunks,
            "chapter_idx": event.chapter_idx,
            "chapter_total": event.chapter_total,
            "chunk_idx": event.chunk_idx,
            "chunk_total": event.chunk_total,
            "elapsed_s": event.elapsed_s,
            "eta_s": event.eta_s,
            "rtf": event.rtf,
            "returncode": event.returncode,
        }
        print(json.dumps(obj), flush=True)
        return

    if quiet:
        if event.kind == "done" and event.output_path:
            print(event.output_path, flush=True)
        elif event.kind == "error":
            print(f"Error: {event.raw_line}", file=sys.stderr, flush=True)
        return

    # Human-readable mode.
    if event.kind == "log":
        print(event.raw_line, flush=True)
    elif event.kind == "chunk":
        if event.total_chunks:
            pct = int(100 * event.total_done / event.total_chunks)
            print(
                f"  [{pct:3d}%] chunk {event.total_done}/{event.total_chunks}"
                + (f"  {event.raw_line}" if event.raw_line else ""),
                flush=True,
            )
        else:
            print(f"  chunk {event.chunk_idx}/{event.chunk_total}", flush=True)
    elif event.kind == "done":
        path = event.output_path or ""
        print(f"Done: {path}", flush=True)
    elif event.kind == "error":
        print(f"Error: {event.raw_line}", file=sys.stderr, flush=True)
    elif event.kind in ("chapter_start", "chapter_done", "full_done", "setup_total", "setup_cached"):
        print(event.raw_line, flush=True)
    elif event.kind == "exit":
        # Subprocess exit — only surface if non-zero.
        if event.returncode not in (0, None):
            print(f"Process exited with code {event.returncode}", file=sys.stderr, flush=True)
    # signal, and other unknown kinds: print the raw line if non-empty.
    elif event.raw_line:
        print(event.raw_line, flush=True)


# ---------------------------------------------------------------------------
# Resolve runner script path (same logic the GUI uses)
# ---------------------------------------------------------------------------


from pathlib import Path  # noqa: E402 — placed here to avoid top-level import noise


def _app_root() -> Path:
    """Return the repo/app root (where scripts/ lives)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent.parent


def runner_script_path() -> Path:
    """Return the path to generate_chatterbox_audiobook.py."""
    root = _app_root()
    return root / "scripts" / "generate_chatterbox_audiobook.py"
