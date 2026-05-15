"""convert subcommand — PDF/EPUB/TXT → MP3.

Usage:
    audiobookmaker convert <input> [flags]

Routes to the correct synthesis path:
- In-process engines (edge, piper, voxcpm): call run_inprocess_synthesis().
- Subprocess engine (chatterbox_fi): build a ChatterboxRunner via
  build_chatterbox_runner(), pump its event queue, await completion.

Exit codes:
    0  success
    1  bad input or validation failure
    2  missing dependency (engine not installed, venv missing, ffmpeg absent)
    3  user cancelled (KeyboardInterrupt)
    4  runtime failure (network, GPU, synthesis error)
    5  unexpected internal error
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from src.cli._common import (
    EXIT_BAD_INPUT,
    EXIT_CANCELLED,
    EXIT_INTERNAL,
    EXIT_MISSING_DEP,
    EXIT_OK,
    EXIT_RUNTIME,
    SPEED_KEYWORD_TO_RATE,
    add_common_synthesis_flags,
    add_output_mode_flags,
    print_event,
    resolve_str,
    runner_script_path,
    validate_input_path,
)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "convert",
        help="Convert a PDF/EPUB/TXT to MP3.",
        description=(
            "Convert a book file (PDF, EPUB, or TXT) to an MP3 audiobook.\n\n"
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


def run(args: argparse.Namespace, *, sample_text: Optional[str] = None) -> int:
    """Run the convert command.

    When ``sample_text`` is provided (called from sample.py), that text
    is used instead of reading and synthesizing the full book.
    """
    json_mode: bool = getattr(args, "json", False)
    quiet: bool = getattr(args, "quiet", False)
    dry_run: bool = getattr(args, "dry_run", False)

    input_path = str(Path(args.input).expanduser())

    # Validate input file.
    code, msg = validate_input_path(input_path)
    if code != EXIT_OK:
        print(f"Error: {msg}", file=sys.stderr)
        return code

    # Resolve config and flags. app_config.load() already returns a
    # default UserConfig() on disk / JSON errors, so no outer wrap.
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
    # Convert the speed keyword to an edge-tts rate string.  When the
    # config stores a raw rate string (e.g. "+0%") fall back to that so
    # the GUI-persisted value is honoured even when the user doesn't pass
    # the flag explicitly.
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
        rate = cfg.speed if cfg.speed else "+0%"
    voice_description = resolve_str(
        getattr(args, "voice_description", None),
        "AUDIOBOOKMAKER_VOICE_DESCRIPTION",
        cfg.voice_description,
        "",
    ) or None
    output_flag_raw = resolve_str(
        getattr(args, "output", None),
        "AUDIOBOOKMAKER_OUTPUT",
        "",
        "",
    ) or None
    output_flag: Optional[str] = str(Path(output_flag_raw).expanduser()) if output_flag_raw else None

    ref_audio_raw: Optional[str] = getattr(args, "ref_audio", None)
    ref_audio: Optional[str] = str(Path(ref_audio_raw).expanduser()) if ref_audio_raw else None
    voice_pack_raw: Optional[str] = getattr(args, "voice_pack", None)
    voice_pack: Optional[str] = str(Path(voice_pack_raw).expanduser()) if voice_pack_raw else None
    chunk_chars: Optional[int] = getattr(args, "chunk_chars", None)

    # Resolve output path.
    try:
        from src.synthesis_orchestrator import suggest_output_path
        output_path = output_flag or suggest_output_path("pdf", input_path)
        if sample_text is not None:
            # Override stem to add _sample suffix for the sample subcommand.
            from src.sample_helpers import compute_sample_output_path
            base = output_flag or suggest_output_path("pdf", input_path)
            output_path = compute_sample_output_path(base)
    except Exception as exc:
        print(f"Error resolving output path: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    if dry_run:
        _print_dry_run(
            input_path=input_path,
            engine_id=engine_id,
            language=language,
            voice_id=voice_id,
            output_path=output_path,
            rate=rate,
            voice_description=voice_description,
            ref_audio=ref_audio,
            voice_pack=voice_pack,
            chunk_chars=chunk_chars,
            is_sample=(sample_text is not None),
            json_mode=json_mode,
        )
        return EXIT_OK

    # Load engine registry and look up the engine.
    try:
        from src import engine_registry  # noqa: F401
        from src.tts_base import get_engine
        engine = get_engine(engine_id)
    except Exception as exc:
        print(f"Error loading engine registry: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    if engine is None:
        print(f"Error: unknown engine '{engine_id}'.", file=sys.stderr)
        print("Run 'audiobookmaker engines list' to see available engines.", file=sys.stderr)
        return EXIT_BAD_INPUT

    # Check engine availability.
    try:
        status = engine.check_status()
    except Exception as exc:
        print(f"Error checking engine status: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    if not status.available:
        print(f"Error: engine '{engine_id}' is not available.", file=sys.stderr)
        if status.reason:
            print(f"  Reason: {status.reason}", file=sys.stderr)
        return EXIT_MISSING_DEP

    # Dispatch to the right synthesis path.
    if engine.uses_subprocess:
        return _run_chatterbox(
            args,
            input_path=input_path,
            language=language,
            output_path=output_path,
            ref_audio=ref_audio,
            voice_pack=voice_pack,
            chunk_chars=chunk_chars,
            sample_text=sample_text,
            json_mode=json_mode,
            quiet=quiet,
        )
    else:
        return _run_inprocess(
            args,
            engine_id=engine_id,
            input_path=input_path,
            language=language,
            voice_id=voice_id,
            output_path=output_path,
            ref_audio=ref_audio,
            voice_description=voice_description,
            rate=rate,
            sample_text=sample_text,
            json_mode=json_mode,
            quiet=quiet,
        )


def _run_inprocess(
    args: argparse.Namespace,
    *,
    engine_id: str,
    input_path: str,
    language: str,
    voice_id: Optional[str],
    output_path: str,
    ref_audio: Optional[str],
    voice_description: Optional[str],
    rate: Optional[str],
    sample_text: Optional[str],
    json_mode: bool,
    quiet: bool,
) -> int:
    from src.synthesis_orchestrator import InprocessRequest, run_inprocess_synthesis

    if sample_text is not None:
        # Sample path: synthesize the pre-extracted text snippet directly.
        request = InprocessRequest(
            engine_id=engine_id,
            language=language,
            input_mode="text",
            output_path=output_path,
            voice_id=voice_id,
            input_text=sample_text,
            reference_audio=ref_audio,
            voice_description=voice_description,
            rate=rate,
        )
    else:
        request = InprocessRequest(
            engine_id=engine_id,
            language=language,
            input_mode="pdf",
            output_path=output_path,
            voice_id=voice_id,
            pdf_path=input_path,
            reference_audio=ref_audio,
            voice_description=voice_description,
            rate=rate,
        )

    result_code = EXIT_OK

    def on_event(event):
        nonlocal result_code
        print_event(event, json_mode=json_mode, quiet=quiet)
        if event.kind == "error":
            result_code = EXIT_RUNTIME

    try:
        run_inprocess_synthesis(request, on_event=on_event)
    except KeyboardInterrupt:
        return EXIT_CANCELLED
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    return result_code


def _run_chatterbox(
    args: argparse.Namespace,
    *,
    input_path: str,
    language: str,
    output_path: str,
    ref_audio: Optional[str],
    voice_pack: Optional[str],
    chunk_chars: Optional[int],
    sample_text: Optional[str],
    json_mode: bool,
    quiet: bool,
) -> int:
    from src.synthesis_orchestrator import (
        ChatterboxBuildError,
        ChatterboxRequest,
        build_chatterbox_runner,
        default_output_dir,
    )

    ext = Path(input_path).suffix.lower()

    if sample_text is not None:
        req = ChatterboxRequest(
            input_mode="pdf",
            pdf_path=input_path,
            text_override=sample_text,
            output_path_hint=output_path,
            reference_audio=ref_audio,
            chunk_chars=chunk_chars or 300,
            language=language,
            voice_pack_path=voice_pack,
        )
    else:
        req = ChatterboxRequest(
            input_mode="pdf",
            pdf_path=input_path,
            output_path_hint=output_path,
            reference_audio=ref_audio,
            chunk_chars=chunk_chars or 300,
            language=language,
            voice_pack_path=voice_pack,
        )

    runner_script = runner_script_path()
    try:
        plan = build_chatterbox_runner(req, runner_script, default_output_dir())
    except ChatterboxBuildError as err:
        kind = err.kind
        messages = {
            "no_pdf": "No input file provided.",
            "no_text": "No text to synthesize.",
            "chatterbox_venv_missing": (
                "Chatterbox virtual environment not found. "
                "Install it via the GUI's Engine settings, or set "
                "CHATTERBOX_PYTHON to the Python interpreter in your "
                "Chatterbox venv."
            ),
        }
        print(f"Error: {messages.get(kind, kind)}", file=sys.stderr)
        return EXIT_MISSING_DEP
    except Exception as exc:
        print(f"Error building Chatterbox runner: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    try:
        plan.runner.start()

        final_code = EXIT_OK
        last_error: Optional[str] = None

        while not plan.runner.finished:
            ev = plan.runner.poll_event(timeout=0.1)
            if ev is not None:
                print_event(ev, json_mode=json_mode, quiet=quiet)
                if ev.kind == "error":
                    last_error = ev.raw_line
                    final_code = EXIT_RUNTIME
                elif ev.kind == "exit":
                    if ev.returncode not in (0, None):
                        final_code = EXIT_RUNTIME

        # Drain any remaining events.
        while True:
            ev = plan.runner.poll_event(timeout=0.0)
            if ev is None:
                break
            print_event(ev, json_mode=json_mode, quiet=quiet)

        plan.runner.join(timeout=5.0)
        return final_code

    except KeyboardInterrupt:
        plan.runner.cancel()
        plan.runner.join(timeout=10.0)
        return EXIT_CANCELLED
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL
    finally:
        plan.cleanup()


def _print_dry_run(
    *,
    input_path: str,
    engine_id: str,
    language: str,
    voice_id: Optional[str],
    output_path: str,
    rate: Optional[str],
    voice_description: Optional[str],
    ref_audio: Optional[str],
    voice_pack: Optional[str],
    chunk_chars: Optional[int],
    is_sample: bool,
    json_mode: bool = False,
) -> None:
    """Print what the conversion would do without running it.

    In ``--json`` mode emits a single JSON object so the dry-run is
    machine-readable for the same callers that consume ``--json`` for
    the real run.
    """
    kind = "sample" if is_sample else "convert"

    if json_mode:
        import json as _json
        obj = {
            "dry_run": True,
            "kind": kind,
            "input": input_path,
            "engine": engine_id,
            "language": language,
            "voice": voice_id,
            "output": output_path,
            "rate": rate,
            "voice_description": voice_description,
            "ref_audio": ref_audio,
            "voice_pack": voice_pack,
            "chunk_chars": chunk_chars,
        }
        print(_json.dumps(obj), flush=True)
        return

    print(f"dry-run: {kind}")
    print(f"  input:      {input_path}")
    print(f"  engine:     {engine_id}")
    print(f"  language:   {language}")
    print(f"  voice:      {voice_id or '(engine default)'}")
    print(f"  output:     {output_path}")
    if rate:
        print(f"  rate:       {rate}")
    if voice_description:
        print(f"  voice-desc: {voice_description}")
    if ref_audio:
        print(f"  ref-audio:  {ref_audio}")
    if voice_pack:
        print(f"  voice-pack: {voice_pack}")
    if chunk_chars:
        print(f"  chunk-chars:{chunk_chars}")
