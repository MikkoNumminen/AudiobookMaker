"""preview subcommand — synthesize text and play it immediately.

Usage:
    audiobookmaker preview <text> [flags]

Takes a short text string, synthesizes it with the current engine, and
plays the result through the system audio output. Nothing is saved.

Use --no-play to synthesize only (prints the tempfile path). Useful for
CI verification or scripting.

Exit codes:
    0  success
    1  bad input (empty text, unknown engine, subprocess engine)
    2  missing dependency (engine unavailable)
    3  user cancelled (Ctrl-C)
    4  runtime failure (synthesis error)
    5  unexpected internal error
"""

from __future__ import annotations

import argparse
import shlex
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from src.cli._common import (
    EXIT_BAD_INPUT,
    EXIT_CANCELLED,
    EXIT_MISSING_DEP,
    EXIT_OK,
    EXIT_RUNTIME,
    SPEED_KEYWORD_TO_RATE,
    add_common_synthesis_flags,
    add_output_mode_flags,
    resolve_str,
)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "preview",
        aliases=["p"],
        help="Synthesize text and play it immediately.",
        description=(
            "Synthesize a short text string and play it through the system\n"
            "audio output. Nothing is saved to disk.\n\n"
            "In-process engines only (edge, piper). Chatterbox runs as a\n"
            "subprocess and is not supported here — use 'sample' instead.\n\n"
            "Exit codes:\n"
            "  0  success\n"
            "  1  bad input / validation failure\n"
            "  2  missing dependency (engine not installed)\n"
            "  3  user cancelled (Ctrl-C)\n"
            "  4  runtime failure (synthesis error)\n"
            "  5  unexpected internal error\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "text",
        metavar="TEXT",
        help="The text to speak, or '-' to read text from stdin.",
    )
    add_common_synthesis_flags(p)
    p.add_argument(
        "--no-play",
        action="store_true",
        default=False,
        help=(
            "Synthesize only — do not play audio. "
            "Prints the tempfile path on stdout. "
            "The caller is responsible for deleting the file."
        ),
    )
    add_output_mode_flags(
        p,
        json_help=(
            "Emit one ProgressEvent per line (NDJSON); "
            "see docs/CLI.md for the event schema."
        ),
        quiet_help=(
            "Suppress progress; print only the tempfile path (with --no-play) "
            "or nothing (when audio is played and the file is deleted)."
        ),
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    json_mode: bool = getattr(args, "json", False)
    quiet: bool = getattr(args, "quiet", False)
    no_play: bool = getattr(args, "no_play", False)

    raw_text: str = args.text

    if raw_text == "-":
        # Stdin sentinel — read text from stdin.
        if sys.stdin.isatty():
            print(
                "Error: stdin is a terminal — pipe data in, or pass a file path.",
                file=sys.stderr,
            )
            return EXIT_BAD_INPUT
        try:
            # Reconfigure stdin to UTF-8 for consistent decoding on all platforms.
            try:
                sys.stdin.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass
            raw_text = sys.stdin.read()
        except Exception as exc:
            print(f"Error reading from stdin: {exc}", file=sys.stderr)
            return EXIT_BAD_INPUT
        # Strip trailing newlines but preserve internal structure.
        raw_text = raw_text.rstrip("\r\n")
        if not raw_text.strip():
            print("Error: empty input from stdin.", file=sys.stderr)
            return EXIT_BAD_INPUT

    text: str = raw_text
    if not text or not text.strip():
        print("Error: text must not be empty.", file=sys.stderr)
        return EXIT_BAD_INPUT

    # --output is a no-op for preview (we synthesize to a tempfile).
    output_flag = resolve_str(
        getattr(args, "output", None),
        "AUDIOBOOKMAKER_OUTPUT",
        "",
        "",
    )
    if output_flag:
        print(
            "Warning: --output is ignored by preview (no file is saved).",
            file=sys.stderr,
        )

    # Resolve engine/language/voice with the standard precedence chain.
    from src.app_config import load as load_config
    cfg = load_config()

    engine_id = resolve_str(
        getattr(args, "engine", None),
        "AUDIOBOOKMAKER_ENGINE",
        cfg.engine_id,
        "edge",
    )
    language = resolve_str(
        getattr(args, "language", None),
        "AUDIOBOOKMAKER_LANGUAGE",
        cfg.language,
        "fi",
    )
    voice_id = resolve_str(
        getattr(args, "voice", None),
        "AUDIOBOOKMAKER_VOICE",
        cfg.voice_id,
        "",
    ) or None
    speed_keyword = resolve_str(
        getattr(args, "speed", None),
        "AUDIOBOOKMAKER_SPEED",
        "",
        "",
    ) or None
    if speed_keyword is not None:
        rate: Optional[str] = SPEED_KEYWORD_TO_RATE.get(speed_keyword)
        if rate is None:
            print(
                f"Error: invalid --speed value '{speed_keyword}'. "
                f"Choose from: {', '.join(SPEED_KEYWORD_TO_RATE)}.",
                file=sys.stderr,
            )
            return EXIT_BAD_INPUT
    else:
        # See convert.run() for the rationale on sanitize_rate.
        from src.cli._common import sanitize_rate
        raw_cfg_speed = cfg.speed or ""
        rate = sanitize_rate(raw_cfg_speed, default="+0%")
        if raw_cfg_speed and rate != raw_cfg_speed:
            print(
                f"[config] ignoring malformed speed value {raw_cfg_speed!r}; "
                "falling back to '+0%'.",
                file=sys.stderr,
            )
    voice_description: Optional[str] = resolve_str(
        getattr(args, "voice_description", None),
        "AUDIOBOOKMAKER_VOICE_DESCRIPTION",
        cfg.voice_description,
        "",
    ) or None

    # Load engine registry and look up the engine.
    try:
        from src import engine_registry  # noqa: F401
        from src.tts_base import get_engine
        engine = get_engine(engine_id)
    except Exception as exc:
        print(f"Error loading engine registry: {exc}", file=sys.stderr)
        from src.cli._common import EXIT_INTERNAL
        return EXIT_INTERNAL

    if engine is None:
        print(f"Error: unknown engine '{engine_id}'.", file=sys.stderr)
        print("Run 'audiobookmaker engines list' to see available engines.", file=sys.stderr)
        return EXIT_BAD_INPUT

    # Subprocess engines cannot be used in-process for preview.
    if engine.uses_subprocess:
        print(
            f"Error: engine '{engine_id}' runs as a subprocess and cannot be "
            "used with preview. Use 'convert' or 'sample' instead.",
            file=sys.stderr,
        )
        return EXIT_BAD_INPUT

    # Check engine availability.
    try:
        status = engine.check_status()
    except Exception as exc:
        print(f"Error checking engine status: {exc}", file=sys.stderr)
        from src.cli._common import EXIT_INTERNAL
        return EXIT_INTERNAL

    if not status.available:
        print(f"Error: engine '{engine_id}' is not available.", file=sys.stderr)
        if status.reason:
            print(f"  Reason: {status.reason}", file=sys.stderr)
        return EXIT_MISSING_DEP

    # Synthesize to a tempfile.
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name

        events: list[str] = []

        def _on_progress(current: int, total: int, message: str = "") -> None:
            # TTSEngine.synthesize uses ProgressCallback = Callable[[int, int, str], None].
            # In human mode we surface the message line; quiet/json suppress it.
            if quiet or json_mode:
                return
            if message:
                events.append(message)

        engine.synthesize(
            text=text,
            output_path=tmp_path,
            voice_id=voice_id,
            language=language,
            progress_cb=_on_progress,
            voice_description=voice_description,
            rate=rate,
        )
    except KeyboardInterrupt:
        _delete_temp(tmp_path)
        return EXIT_CANCELLED
    except Exception as exc:
        _delete_temp(tmp_path)
        print(f"Synthesis error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    if no_play:
        # Hand the file to the caller; they own it.
        # Shell-quote the path so callers can safely eval/use it even
        # when the tempdir contains spaces (common on Windows/macOS).
        print(shlex.quote(str(tmp_path)), flush=True)
        return EXIT_OK

    # Play the clip and wait for it to finish.
    try:
        from src._audio_player import get_player
        player = get_player()

        if not quiet and not json_mode:
            print(f"Playing preview ({engine_id}) …", flush=True)

        player.play(tmp_path)

        while player.is_playing():
            time.sleep(0.05)

    except KeyboardInterrupt:
        try:
            player.stop()
        except Exception:
            pass
        _delete_temp(tmp_path)
        return EXIT_CANCELLED
    except Exception as exc:
        _delete_temp(tmp_path)
        print(f"Playback error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    finally:
        _delete_temp(tmp_path)

    return EXIT_OK


def _delete_temp(path: str | None) -> None:
    """Delete a tempfile, ignoring errors (already deleted, locked, etc.)."""
    if path is None:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass
