"""Tests for src.synthesis_orchestrator.

The orchestrator is the first module extracted from the UnifiedApp
god-object. Its helpers have zero GUI dependencies, so they can be
unit-tested directly — no tkinter, no fixtures, no real engine.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pytest

from src.launcher_bridge import ProgressEvent
from src.synthesis_orchestrator import (
    ChatterboxBuildError,
    ChatterboxRequest,
    InprocessRequest,
    build_chatterbox_runner,
    default_output_dir,
    next_available_numbered_path,
    parse_book,
    run_inprocess_synthesis,
    suggest_output_path,
)
from src.tts_base import EngineStatus, TTSEngine, Voice, _REGISTRY


# ---------------------------------------------------------------------------
# parse_book — extension dispatch
# ---------------------------------------------------------------------------


def test_parse_book_txt_round_trip(tmp_path: Path):
    src = tmp_path / "my_book.txt"
    src.write_text("Hello world.\nSecond line.", encoding="utf-8")

    book = parse_book(str(src))

    assert book.metadata.title == "My Book"  # underscore → space + Title Case
    assert book.metadata.num_pages == 1
    assert len(book.chapters) == 1
    assert "Hello world." in book.chapters[0].content


def test_parse_book_txt_handles_non_utf8_bytes(tmp_path: Path):
    # Latin-1 bytes that are not valid UTF-8; parser uses errors="replace".
    src = tmp_path / "legacy.txt"
    src.write_bytes(b"caf\xe9 au lait")

    book = parse_book(str(src))

    # Implementation uses errors="replace" — content is readable, no crash.
    assert book.chapters[0].content  # non-empty


def test_parse_book_unknown_ext_falls_through_to_pdf(tmp_path: Path):
    """Unknown extensions default to the PDF parser so the error message
    stays familiar. We expect a parser-side failure, not a local crash."""
    src = tmp_path / "mystery.xyz"
    src.write_text("not a real pdf", encoding="utf-8")

    with pytest.raises(Exception):
        parse_book(str(src))


# ---------------------------------------------------------------------------
# default_output_dir
# ---------------------------------------------------------------------------


def test_default_output_dir_dev_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # Dev mode: not frozen. Should return ``<cwd>/out`` — the canonical
    # dev-mode output directory. Never escapes into ~/Documents/… and
    # never lands at the repo root.
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.chdir(tmp_path)

    result = default_output_dir()

    assert result == tmp_path / "out"


def test_default_output_dir_frozen_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # Simulate a PyInstaller bundle: frozen + executable under tmp_path.
    fake_exe = tmp_path / "AudiobookMaker.exe"
    fake_exe.write_bytes(b"")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)

    result = default_output_dir()

    assert result == tmp_path.resolve()


# ---------------------------------------------------------------------------
# suggest_output_path
# ---------------------------------------------------------------------------


def test_suggest_output_path_pdf_mode_routes_to_out_dir(tmp_path: Path):
    # PDF mode keeps the book stem but forces the file into ``out_dir``
    # — never sibling-to-PDF. Protects the canonical output-dir rule
    # from being violated by the auto-path helper.
    pdf = tmp_path / "source" / "my_book.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"")
    out_dir = tmp_path / "out"

    result = suggest_output_path("pdf", str(pdf), out_dir=out_dir)

    assert Path(result) == out_dir / "my_book.mp3"


def test_suggest_output_path_pdf_mode_uses_default_when_out_dir_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # When the caller doesn't pass out_dir, PDF mode still routes through
    # default_output_dir() rather than dropping next to the source.
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.chdir(tmp_path)
    pdf = tmp_path / "source" / "title.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"")

    result = suggest_output_path("pdf", str(pdf))

    assert Path(result) == tmp_path / "out" / "title.mp3"


def test_suggest_output_path_text_mode_auto_increments(tmp_path: Path):
    # Fresh dir → _1.
    assert suggest_output_path("text", None, out_dir=tmp_path).endswith(
        "texttospeech_1.mp3"
    )

    # _1 exists → _2.
    (tmp_path / "texttospeech_1.mp3").write_bytes(b"")
    assert suggest_output_path("text", None, out_dir=tmp_path).endswith(
        "texttospeech_2.mp3"
    )

    # _1 and _2 exist → _3.
    (tmp_path / "texttospeech_2.mp3").write_bytes(b"")
    assert suggest_output_path("text", None, out_dir=tmp_path).endswith(
        "texttospeech_3.mp3"
    )


def test_suggest_output_path_creates_missing_dir(tmp_path: Path):
    target_dir = tmp_path / "new" / "nested" / "dir"
    assert not target_dir.exists()

    suggest_output_path("text", None, out_dir=target_dir)

    assert target_dir.exists()  # helper created it


def test_suggest_output_path_text_mode_no_pdf_path(tmp_path: Path):
    """Even with input_mode='pdf', a None pdf_path falls back to text mode."""
    result = suggest_output_path("pdf", None, out_dir=tmp_path)
    assert result.endswith("texttospeech_1.mp3")


# ---------------------------------------------------------------------------
# next_available_numbered_path
# ---------------------------------------------------------------------------


def test_bump_path_returns_unchanged_when_file_missing(tmp_path: Path):
    fresh = tmp_path / "book.mp3"
    assert next_available_numbered_path(str(fresh)) == str(fresh)


def test_bump_path_numbered_stem_increments(tmp_path: Path):
    existing = tmp_path / "texttospeech_3.mp3"
    existing.write_bytes(b"")

    result = next_available_numbered_path(str(existing))

    assert Path(result).name == "texttospeech_4.mp3"


def test_bump_path_plain_stem_adds_suffix_2(tmp_path: Path):
    existing = tmp_path / "book.mp3"
    existing.write_bytes(b"")

    result = next_available_numbered_path(str(existing))

    assert Path(result).name == "book_2.mp3"


def test_bump_path_skips_over_existing_higher_numbers(tmp_path: Path):
    (tmp_path / "book.mp3").write_bytes(b"")
    (tmp_path / "book_2.mp3").write_bytes(b"")
    (tmp_path / "book_3.mp3").write_bytes(b"")

    result = next_available_numbered_path(str(tmp_path / "book.mp3"))

    assert Path(result).name == "book_4.mp3"


def test_bump_path_numbered_stem_skips_existing(tmp_path: Path):
    (tmp_path / "book_5.mp3").write_bytes(b"")
    (tmp_path / "book_6.mp3").write_bytes(b"")

    result = next_available_numbered_path(str(tmp_path / "book_5.mp3"))

    assert Path(result).name == "book_7.mp3"


def test_bump_path_preserves_extension(tmp_path: Path):
    existing = tmp_path / "clip.wav"
    existing.write_bytes(b"")

    result = next_available_numbered_path(str(existing))

    assert Path(result).suffix == ".wav"


def test_bump_path_handles_extensionless_input(tmp_path: Path):
    existing = tmp_path / "noext"
    existing.write_bytes(b"")

    result = next_available_numbered_path(str(existing))

    # Implementation defaults to .mp3 when there's no suffix.
    assert Path(result).name == "noext_2.mp3"


# ---------------------------------------------------------------------------
# run_inprocess_synthesis — in-process engine dispatch
# ---------------------------------------------------------------------------


class _FakeEngine(TTSEngine):
    """Test engine that records each synthesize() call instead of doing work.

    The class-level ``calls`` list is shared across instances so tests can
    inspect it even though ``get_engine()`` constructs a fresh instance
    on every call. The ``fake_engine_registered`` fixture clears it.

    Emits two chunk events via ``progress_cb`` so callers can verify the
    orchestrator forwards progress correctly.
    """

    id = "fake_engine_orch"
    display_name = "Fake Engine (orchestrator tests)"
    requires_gpu = False
    requires_internet = False

    calls: list[dict] = []
    _default_voice_override: Optional[str] = "fake_voice"

    def check_status(self) -> EngineStatus:
        return EngineStatus(available=True)

    def supported_languages(self) -> set[str]:
        return {"fi", "en"}

    def list_voices(self, language: str) -> list[Voice]:
        return [Voice(
            id="fake_voice", display_name="Fake", language=language, gender="female"
        )]

    def default_voice(self, language: str) -> Optional[str]:
        return type(self)._default_voice_override

    def synthesize(
        self, text, output_path, voice_id, language,
        progress_cb=None, reference_audio=None, voice_description=None,
    ) -> None:
        type(self).calls.append({
            "text": text,
            "output_path": output_path,
            "voice_id": voice_id,
            "language": language,
            "reference_audio": reference_audio,
            "voice_description": voice_description,
        })
        if progress_cb is not None:
            progress_cb(1, 2, "chunk 1")
            progress_cb(2, 2, "chunk 2")
        # Write an empty placeholder so callers that check for the file work.
        Path(output_path).write_bytes(b"")


@pytest.fixture
def fake_engine_registered():
    """Register _FakeEngine for the duration of one test, clearing its call log."""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _FakeEngine.calls = []
    _FakeEngine._default_voice_override = "fake_voice"
    _REGISTRY[_FakeEngine.id] = _FakeEngine
    try:
        yield _FakeEngine
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)
        _FakeEngine.calls = []


def _collect_events() -> tuple[list[ProgressEvent], callable]:
    """Return (events_list, on_event sink) — tests inspect the list."""
    events: list[ProgressEvent] = []
    return events, events.append


def test_inprocess_happy_path_emits_log_chunk_done(
    fake_engine_registered, tmp_path: Path
):
    events, on_event = _collect_events()
    request = InprocessRequest(
        engine_id="fake_engine_orch",
        language="fi",
        input_mode="text",
        output_path=str(tmp_path / "out.mp3"),
        voice_id="fake_voice",
        input_text="Hello world.",
    )

    run_inprocess_synthesis(request, on_event)

    kinds = [e.kind for e in events]
    assert "log" in kinds
    assert kinds.count("chunk") == 2
    assert kinds[-1] == "done"
    assert events[-1].output_path == str(tmp_path / "out.mp3")


def test_inprocess_unknown_engine_emits_error(tmp_path: Path):
    # No engines registered — get_engine returns None.
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    try:
        events, on_event = _collect_events()
        request = InprocessRequest(
            engine_id="does_not_exist",
            language="fi",
            input_mode="text",
            input_text="hi",
            output_path=str(tmp_path / "out.mp3"),
        )
        run_inprocess_synthesis(request, on_event)
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)

    assert events[-1].kind == "error"
    assert "not found" in events[-1].raw_line


def test_inprocess_empty_text_emits_error(fake_engine_registered, tmp_path: Path):
    events, on_event = _collect_events()
    request = InprocessRequest(
        engine_id="fake_engine_orch",
        language="fi",
        input_mode="text",
        input_text="   ",  # whitespace — stripped to empty by caller in real flow
        output_path=str(tmp_path / "out.mp3"),
    )
    # Simulate the caller pre-stripping: empty string.
    request = InprocessRequest(
        engine_id="fake_engine_orch",
        language="fi",
        input_mode="text",
        input_text="",
        output_path=str(tmp_path / "out.mp3"),
    )

    run_inprocess_synthesis(request, on_event)

    assert events[-1].kind == "error"
    assert "No text" in events[-1].raw_line


def test_inprocess_pdf_mode_requires_path(fake_engine_registered, tmp_path: Path):
    events, on_event = _collect_events()
    request = InprocessRequest(
        engine_id="fake_engine_orch",
        language="fi",
        input_mode="pdf",
        pdf_path=None,
        output_path=str(tmp_path / "out.mp3"),
    )

    run_inprocess_synthesis(request, on_event)

    assert events[-1].kind == "error"
    assert "pdf_path" in events[-1].raw_line


def test_inprocess_pdf_mode_reads_txt_fallback(
    fake_engine_registered, tmp_path: Path
):
    # "pdf" input_mode also handles .txt via parse_book's extension dispatch.
    src = tmp_path / "story.txt"
    src.write_text("Chapter one text.", encoding="utf-8")

    events, on_event = _collect_events()
    request = InprocessRequest(
        engine_id="fake_engine_orch",
        language="fi",
        input_mode="pdf",
        pdf_path=str(src),
        output_path=str(tmp_path / "out.mp3"),
    )

    run_inprocess_synthesis(request, on_event)

    # Engine saw the text from the parsed book.
    calls = fake_engine_registered.calls
    assert len(calls) == 1
    assert "Chapter one text." in calls[0]["text"]
    assert events[-1].kind == "done"


def test_inprocess_falls_back_to_default_voice(
    fake_engine_registered, tmp_path: Path
):
    # Caller didn't specify voice_id → orchestrator asks engine.default_voice.
    events, on_event = _collect_events()
    request = InprocessRequest(
        engine_id="fake_engine_orch",
        language="fi",
        input_mode="text",
        input_text="hello",
        output_path=str(tmp_path / "out.mp3"),
        voice_id=None,
    )

    run_inprocess_synthesis(request, on_event)

    calls = fake_engine_registered.calls
    assert calls[0]["voice_id"] == "fake_voice"


def test_inprocess_no_voice_available_emits_error(
    fake_engine_registered, tmp_path: Path
):
    # Replace the registered class with one whose default_voice returns None.
    class _NoVoiceEngine(_FakeEngine):
        def default_voice(self, language: str) -> Optional[str]:
            return None

    _REGISTRY["fake_engine_orch"] = _NoVoiceEngine

    events, on_event = _collect_events()
    request = InprocessRequest(
        engine_id="fake_engine_orch",
        language="fi",
        input_mode="text",
        input_text="hi",
        output_path=str(tmp_path / "out.mp3"),
        voice_id=None,
    )

    run_inprocess_synthesis(request, on_event)

    assert events[-1].kind == "error"
    assert "No voice" in events[-1].raw_line


def test_inprocess_engine_exception_becomes_error_event(
    fake_engine_registered, tmp_path: Path
):
    class _ExplodingEngine(_FakeEngine):
        def synthesize(self, *args, **kwargs):
            raise RuntimeError("engine blew up")

    _REGISTRY["fake_engine_orch"] = _ExplodingEngine

    events, on_event = _collect_events()
    request = InprocessRequest(
        engine_id="fake_engine_orch",
        language="fi",
        input_mode="text",
        input_text="hi",
        output_path=str(tmp_path / "out.mp3"),
        voice_id="fake_voice",
    )

    run_inprocess_synthesis(request, on_event)

    # Never raises — exception becomes an error event.
    assert events[-1].kind == "error"
    assert "engine blew up" in events[-1].raw_line


def test_inprocess_forwards_reference_audio_and_description(
    fake_engine_registered, tmp_path: Path
):
    events, on_event = _collect_events()
    request = InprocessRequest(
        engine_id="fake_engine_orch",
        language="fi",
        input_mode="text",
        input_text="hi",
        output_path=str(tmp_path / "out.mp3"),
        voice_id="fake_voice",
        reference_audio=str(tmp_path / "ref.wav"),
        voice_description="warm narrator",
    )

    run_inprocess_synthesis(request, on_event)

    calls = fake_engine_registered.calls
    assert calls[0]["reference_audio"] == str(tmp_path / "ref.wav")
    assert calls[0]["voice_description"] == "warm narrator"


def test_inprocess_creates_output_directory(
    fake_engine_registered, tmp_path: Path
):
    out_path = tmp_path / "nested" / "dir" / "out.mp3"
    assert not out_path.parent.exists()

    events, on_event = _collect_events()
    request = InprocessRequest(
        engine_id="fake_engine_orch",
        language="fi",
        input_mode="text",
        input_text="hi",
        output_path=str(out_path),
        voice_id="fake_voice",
    )

    run_inprocess_synthesis(request, on_event)

    assert out_path.parent.exists()
    assert events[-1].kind == "done"


# ---------------------------------------------------------------------------
# build_chatterbox_runner — subprocess engine dispatch
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_chatterbox_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Pretend the chatterbox venv + runner script exist.

    Returns ``(runner_script_path, default_out_dir)`` the caller passes
    to :func:`build_chatterbox_runner`. The python_exe resolver is
    stubbed to always return a path under ``tmp_path`` so the "missing
    venv" branch doesn't accidentally trip.
    """
    fake_python = tmp_path / "fake_python.exe"
    fake_python.write_bytes(b"")
    monkeypatch.setattr(
        "src.synthesis_orchestrator.resolve_chatterbox_python",
        lambda: fake_python,
    )

    runner_script = tmp_path / "scripts" / "generate_chatterbox_audiobook.py"
    runner_script.parent.mkdir(parents=True)
    runner_script.write_text("# fake runner", encoding="utf-8")

    default_out_dir = tmp_path / "default_out"
    return runner_script, default_out_dir


def test_build_chatterbox_no_pdf_raises(fake_chatterbox_env, tmp_path: Path):
    runner_script, default_out = fake_chatterbox_env
    req = ChatterboxRequest(input_mode="pdf", pdf_path=None)
    with pytest.raises(ChatterboxBuildError) as exc:
        build_chatterbox_runner(req, runner_script, default_out)
    assert exc.value.kind == "no_pdf"


def test_build_chatterbox_no_text_raises(fake_chatterbox_env):
    runner_script, default_out = fake_chatterbox_env
    req = ChatterboxRequest(input_mode="text", input_text="   ")
    with pytest.raises(ChatterboxBuildError) as exc:
        build_chatterbox_runner(req, runner_script, default_out)
    assert exc.value.kind == "no_text"


def test_build_chatterbox_venv_missing_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "src.synthesis_orchestrator.resolve_chatterbox_python",
        lambda: None,
    )
    runner_script = tmp_path / "nonexistent.py"  # also missing
    req = ChatterboxRequest(input_mode="text", input_text="hello")
    with pytest.raises(ChatterboxBuildError) as exc:
        build_chatterbox_runner(req, runner_script, tmp_path)
    assert exc.value.kind == "chatterbox_venv_missing"


def test_build_chatterbox_pdf_extension_routes_to_pdf_flag(
    fake_chatterbox_env, tmp_path: Path
):
    runner_script, default_out = fake_chatterbox_env
    book = tmp_path / "story.pdf"
    book.write_bytes(b"")
    req = ChatterboxRequest(input_mode="pdf", pdf_path=str(book))

    plan = build_chatterbox_runner(req, runner_script, default_out)

    assert plan.runner.pdf_path == str(book)
    assert plan.runner.text_path is None
    assert plan.runner.epub_path is None


def test_build_chatterbox_epub_extension_routes_to_epub_flag(
    fake_chatterbox_env, tmp_path: Path
):
    runner_script, default_out = fake_chatterbox_env
    book = tmp_path / "story.epub"
    book.write_bytes(b"")
    req = ChatterboxRequest(input_mode="pdf", pdf_path=str(book))

    plan = build_chatterbox_runner(req, runner_script, default_out)

    assert plan.runner.epub_path == str(book)
    assert plan.runner.pdf_path is None
    assert plan.runner.text_path is None


def test_build_chatterbox_txt_extension_routes_to_text_flag(
    fake_chatterbox_env, tmp_path: Path
):
    runner_script, default_out = fake_chatterbox_env
    book = tmp_path / "story.txt"
    book.write_text("Chapter one.", encoding="utf-8")
    req = ChatterboxRequest(input_mode="pdf", pdf_path=str(book))

    plan = build_chatterbox_runner(req, runner_script, default_out)

    assert plan.runner.text_path == str(book)
    assert plan.runner.pdf_path is None


def test_build_chatterbox_text_mode_creates_tempfile(
    fake_chatterbox_env, tmp_path: Path
):
    runner_script, default_out = fake_chatterbox_env
    req = ChatterboxRequest(input_mode="text", input_text="Hello world.")

    plan = build_chatterbox_runner(req, runner_script, default_out)

    assert plan.runner.text_path is not None
    assert Path(plan.runner.text_path).exists()
    assert Path(plan.runner.text_path).read_text(encoding="utf-8") == "Hello world."


def test_build_chatterbox_text_override_wins_over_pdf_mode(
    fake_chatterbox_env, tmp_path: Path
):
    """Sample flow: text_override bypasses the input_mode branch entirely."""
    runner_script, default_out = fake_chatterbox_env
    book = tmp_path / "full_book.pdf"
    book.write_bytes(b"")

    req = ChatterboxRequest(
        input_mode="pdf",
        pdf_path=str(book),
        text_override="Snippet for sample.",
        output_basename_override="my_sample",
    )

    plan = build_chatterbox_runner(req, runner_script, default_out)

    # Snippet went to a tempfile, pdf_path is not forwarded to the runner.
    assert plan.runner.pdf_path is None
    assert plan.runner.text_path is not None
    tmp_contents = Path(plan.runner.text_path).read_text(encoding="utf-8")
    assert tmp_contents == "Snippet for sample."
    assert "my_sample" in Path(plan.runner.text_path).name


def test_build_chatterbox_ref_audio_added_to_extra_args(
    fake_chatterbox_env, tmp_path: Path
):
    runner_script, default_out = fake_chatterbox_env
    req = ChatterboxRequest(
        input_mode="text",
        input_text="hi",
        reference_audio="/path/to/ref.wav",
    )

    plan = build_chatterbox_runner(req, runner_script, default_out)

    assert "--ref-audio" in plan.runner.extra_args
    idx = plan.runner.extra_args.index("--ref-audio")
    assert plan.runner.extra_args[idx + 1] == "/path/to/ref.wav"


def test_build_chatterbox_default_chunk_chars_not_forwarded(
    fake_chatterbox_env, tmp_path: Path
):
    """300 is the runner's built-in default — skip the flag to keep logs clean."""
    runner_script, default_out = fake_chatterbox_env
    req = ChatterboxRequest(input_mode="text", input_text="hi", chunk_chars=300)

    plan = build_chatterbox_runner(req, runner_script, default_out)

    assert "--chunk-chars" not in plan.runner.extra_args


def test_build_chatterbox_custom_chunk_chars_forwarded(
    fake_chatterbox_env, tmp_path: Path
):
    runner_script, default_out = fake_chatterbox_env
    req = ChatterboxRequest(input_mode="text", input_text="hi", chunk_chars=500)

    plan = build_chatterbox_runner(req, runner_script, default_out)

    assert "--chunk-chars" in plan.runner.extra_args
    idx = plan.runner.extra_args.index("--chunk-chars")
    assert plan.runner.extra_args[idx + 1] == "500"


def test_build_chatterbox_output_path_hint_sets_parent_dir(
    fake_chatterbox_env, tmp_path: Path
):
    runner_script, default_out = fake_chatterbox_env
    hint_dir = tmp_path / "chosen" / "out"
    hint_dir.mkdir(parents=True)
    hint_path = hint_dir / "book.mp3"
    req = ChatterboxRequest(
        input_mode="text",
        input_text="hi",
        output_path_hint=str(hint_path),
    )

    plan = build_chatterbox_runner(req, runner_script, default_out)

    assert plan.out_dir == hint_dir.resolve()
    assert plan.runner.out_dir == str(hint_dir.resolve())


def test_build_chatterbox_no_hint_uses_default(
    fake_chatterbox_env, tmp_path: Path
):
    runner_script, default_out = fake_chatterbox_env
    assert not default_out.exists()

    req = ChatterboxRequest(input_mode="text", input_text="hi")
    plan = build_chatterbox_runner(req, runner_script, default_out)

    assert plan.out_dir == default_out.resolve()
    assert default_out.exists()  # created


def test_build_chatterbox_language_forwarded(
    fake_chatterbox_env, tmp_path: Path
):
    runner_script, default_out = fake_chatterbox_env
    req = ChatterboxRequest(input_mode="text", input_text="hi", language="en")

    plan = build_chatterbox_runner(req, runner_script, default_out)

    assert plan.runner.language == "en"
