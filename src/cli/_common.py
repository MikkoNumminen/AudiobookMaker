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
from pathlib import Path
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
#   --engine              AUDIOBOOKMAKER_ENGINE
#   --language            AUDIOBOOKMAKER_LANGUAGE
#   --voice               AUDIOBOOKMAKER_VOICE
#   --output              AUDIOBOOKMAKER_OUTPUT
#   --speed               AUDIOBOOKMAKER_SPEED
#   --voice-description   AUDIOBOOKMAKER_VOICE_DESCRIPTION

# Speed keyword → edge-tts rate string mapping (same values as the GUI).
SPEED_KEYWORD_TO_RATE: dict[str, str] = {
    "slow":   "-25%",
    "normal": "+0%",
    "fast":   "+25%",
    "xfast":  "+50%",
}

# Accepted format for a raw rate string in the config / env var: an
# optional ``+`` or ``-`` sign, one or more digits, and a trailing ``%``.
# Matches edge-tts's documented rate parameter shape. Anything else is
# treated as malformed and the call site falls back to "+0%".
import re as _re
_RATE_PATTERN = _re.compile(r"^[+-]?\d+%$")


def sanitize_rate(raw: Optional[str], *, default: str = "+0%") -> str:
    """Return ``raw`` if it matches the edge-tts rate format, else
    ``default``.

    Used to defend against a corrupt config file or a hand-edited env
    var carrying a bogus rate value (e.g. ``"bogus"`` or ``"fast"``).
    Both would otherwise be passed straight through to the engine,
    which would surface an opaque error mid-synthesis.

    ``None`` and the empty string return ``default`` with no warning;
    they're the natural "field absent" sentinel from the config layer.
    A non-empty string that fails the regex returns ``default`` — the
    caller is responsible for logging the substitution if it wants to.
    """
    if raw is None or raw == "":
        return default
    if _RATE_PATTERN.match(raw):
        return raw
    return default


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
    """Add --engine, --language, --voice, --output, --speed, --voice-description to a parser.

    These flags have identical semantics across convert, sample, and
    preview. Each flag documents its env-var override and default so
    --help is the contract.
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
        help=(
            "Language code (e.g. fi, en). The Language picker in the GUI "
            "exposes fi + en; other codes route through to the engine, "
            "which will reject anything it doesn't speak. "
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
        "--output",
        metavar="PATH",
        default=None,
        help=(
            "Output MP3 path. "
            "Default: <output_dir>/<book-stem>.mp3. "
            "Env: AUDIOBOOKMAKER_OUTPUT."
        ),
    )
    parser.add_argument(
        "--speed",
        metavar="KEYWORD",
        choices=list(SPEED_KEYWORD_TO_RATE.keys()),
        default=None,
        help=(
            "Playback speed. One of: slow (-25%%), normal (+0%%), fast (+25%%), "
            "xfast (+50%%). Engines that do not support speed control ignore "
            "this flag. Default from config (GUI Speed setting); fallback: normal. "
            "Env: AUDIOBOOKMAKER_SPEED."
        ),
    )
    parser.add_argument(
        "--voice-description",
        metavar="TEXT",
        default=None,
        help=(
            "Free-text voice style prompt for engines that support it "
            "(e.g. 'a warm baritone elderly male voice'). Ignored by engines "
            "that do not support voice descriptions. "
            "Default from config (GUI Voice style field). "
            "Env: AUDIOBOOKMAKER_VOICE_DESCRIPTION."
        ),
    )


def add_output_mode_flags(parser: argparse.ArgumentParser) -> None:
    """Add --json and --quiet output mode flags to a parser."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit one JSON object per line (NDJSON format, ProgressEvent shape).",
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


def _app_root() -> Path:
    """Return the repo/app root (where scripts/ lives)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent.parent


def runner_script_path() -> Path:
    """Return the path to generate_chatterbox_audiobook.py.

    TODO(cli-frozen-build): when the standalone audiobookmaker_cli.spec
    ships, verify that scripts/generate_chatterbox_audiobook.py is in
    that bundle's datas list or this lookup will fail at runtime in
    installed-binary mode. The GUI's spec already includes it; the
    CLI's future spec must too.
    """
    root = _app_root()
    return root / "scripts" / "generate_chatterbox_audiobook.py"


def validate_input_path(path: str) -> tuple[int, str]:
    """Validate that ``path`` exists and has a supported book extension.

    Expands a leading ``~`` before the existence check so callers that
    pass ``~/books/foo.epub`` (Makefile, cron, subprocess.run with
    shell=False) get the same behaviour as an absolute path.

    Returns ``(EXIT_OK, '')`` if valid, or ``(EXIT_BAD_INPUT, message)``
    if invalid. Shared by convert and sample so the validation rules
    stay in one place.
    """
    resolved = Path(path).expanduser()
    if not resolved.exists():
        return EXIT_BAD_INPUT, f"input file not found: {resolved}"
    ext = resolved.suffix.lower()
    if ext not in (".pdf", ".epub", ".txt"):
        return (
            EXIT_BAD_INPUT,
            f"unsupported file type '{ext}'. Supported formats: .pdf, .epub, .txt",
        )
    return EXIT_OK, ""
