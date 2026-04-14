"""End-to-end GUI tests for AudiobookMaker.

Instantiates the real UnifiedApp and exercises validation flows.
Uses mocked messageboxes to verify error messages without user interaction.
Tkinter works headlessly on Windows CI runners.
"""
from __future__ import annotations

import tkinter as tk
from unittest.mock import patch

import pytest

from src.tts_base import (
    EngineStatus,
    TTSEngine,
    Voice,
    _REGISTRY,
    register_engine,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _shared_app():
    """Module-scoped: create one UnifiedApp for all GUI tests.

    Tkinter can only have one root window per interpreter. Creating and
    destroying multiple Tk() instances across tests causes crashes, so we
    share a single instance across the whole module.
    """
    from src.tts_edge import EdgeTTSEngine
    from src.tts_piper import PiperTTSEngine
    from src.gui_unified import UnifiedApp

    # Ensure engines are registered (decorators only fire on first import)
    if "edge" not in _REGISTRY:
        _REGISTRY["edge"] = EdgeTTSEngine
    if "piper" not in _REGISTRY:
        _REGISTRY["piper"] = PiperTTSEngine

    instance = UnifiedApp()
    instance.update_idletasks()
    yield instance
    instance.destroy()


@pytest.fixture
def app(_shared_app, clean_registry):
    """Per-test fixture: resets registry but reuses the shared app window.

    Re-registers Edge-TTS and Piper after clean_registry clears them.
    """
    from src.tts_edge import EdgeTTSEngine
    from src.tts_piper import PiperTTSEngine

    if "edge" not in _REGISTRY:
        _REGISTRY["edge"] = EdgeTTSEngine
    if "piper" not in _REGISTRY:
        _REGISTRY["piper"] = PiperTTSEngine

    _shared_app.update_idletasks()
    return _shared_app


# ---------------------------------------------------------------------------
# Fake unavailable engine
# ---------------------------------------------------------------------------


class _UnavailableEngine(TTSEngine):
    id = "test_unavail"
    display_name = "Unavailable Test"
    description = "Always unavailable"

    def check_status(self) -> EngineStatus:
        return EngineStatus(
            available=False,
            reason="Install required: pip install test-engine",
        )

    def list_voices(self, language: str) -> list[Voice]:
        return []

    def default_voice(self, language: str) -> str | None:
        return None

    def synthesize(self, *args, **kwargs) -> None:
        raise RuntimeError("Not installed")


# ---------------------------------------------------------------------------
# App instantiation
# ---------------------------------------------------------------------------


class TestAppInstantiation:
    def test_app_creates_and_destroys(self, app):
        """UnifiedApp title starts with AudiobookMaker and includes a version."""
        assert app.title().startswith("AudiobookMaker")
        # Version marker ("v") lets the user verify which build runs after
        # an update.
        assert " v" in app.title()

    def test_engine_dropdown_populated(self, app):
        """The engine combobox has at least one entry."""
        values = list(app._engine_cb.cget("values"))
        assert len(values) >= 1

    def test_registered_engines_include_edge(self, app):
        """Edge-TTS should always be registered."""
        from src.tts_base import registered_ids

        ids = registered_ids()
        assert "edge" in ids

    def test_registered_engines_include_piper(self, app):
        """Piper should always be registered."""
        from src.tts_base import registered_ids

        ids = registered_ids()
        assert "piper" in ids

    def test_engine_display_to_id_mapping(self, app):
        """Every engine in the dropdown maps to a valid engine id."""
        for display_name, engine_id in app._engine_display_to_id.items():
            assert engine_id, f"Empty engine id for display name {display_name!r}"


# ---------------------------------------------------------------------------
# Convert validation
# ---------------------------------------------------------------------------


class TestConvertValidation:
    @patch("tkinter.messagebox.showerror")
    def test_convert_pdf_mode_no_file_shows_pdf_error(self, mock_error, app):
        """PDF mode with no file selected should mention PDF in the error."""
        app._pdf_path = None
        # Switch to PDF tab (the Finnish internal tab name).
        app._input_nb.set("PDF-tiedosto")
        app.update_idletasks()

        app._on_convert_click()

        mock_error.assert_called_once()
        msg = mock_error.call_args[0][1]
        # The error should be about selecting a PDF, not about voice/engine.
        assert "pdf" in msg.lower() or "PDF" in msg

    @patch("tkinter.messagebox.showerror")
    def test_convert_empty_text_shows_text_error(self, mock_error, app):
        """Text mode with empty text should show a 'no text' error."""
        # Switch to Text tab.
        app._input_nb.set("Teksti")
        app.update_idletasks()

        # Clear text widget and mark placeholder as removed.
        app._text_widget.delete("1.0", tk.END)
        app._text_has_placeholder = False
        app.update_idletasks()

        app._on_convert_click()

        mock_error.assert_called_once()
        msg = mock_error.call_args[0][1]
        # Should mention text, not PDF or voice.
        assert "teksti" in msg.lower() or "text" in msg.lower()

    @patch("tkinter.messagebox.showerror")
    def test_convert_placeholder_text_shows_text_error(self, mock_error, app):
        """Text mode where placeholder is still shown should count as empty."""
        app._input_nb.set("Teksti")
        app.update_idletasks()

        # Ensure the placeholder flag is set (simulating user never typed).
        app._text_has_placeholder = True
        app.update_idletasks()

        app._on_convert_click()

        mock_error.assert_called_once()

    @patch("tkinter.messagebox.showerror")
    def test_unavailable_engine_convert_shows_install_hint(self, mock_error, app):
        """An unavailable engine should show install instructions, not 'Valitse aani'."""
        register_engine(_UnavailableEngine)

        # Re-populate the engine dropdown so our fake engine appears.
        app._populate_engine_list()
        app.update_idletasks()

        # Select the unavailable engine in the combobox.
        for display, eid in app._engine_display_to_id.items():
            if eid == "test_unavail":
                app._engine_cb.set(display)
                break
        app.update_idletasks()

        # Set up valid text input so we get past text validation.
        app._input_nb.set("Teksti")
        app.update_idletasks()
        app._text_widget.delete("1.0", tk.END)
        app._text_widget.insert("1.0", "Test text for synthesis")
        app._text_has_placeholder = False
        # Set an output path so we get past output validation.
        app._output_path = "C:\\temp\\test_output.mp3"

        app._on_convert_click()

        mock_error.assert_called()
        msg = mock_error.call_args[0][1]
        # The error MUST mention install instructions, NOT "Valitse aani".
        assert "ääni" not in msg.lower(), (
            f"Got voice-selection error instead of install hint: {msg}"
        )
        assert "install" in msg.lower(), (
            f"Expected install instructions in the error, got: {msg}"
        )


# ---------------------------------------------------------------------------
# Listen validation
# ---------------------------------------------------------------------------


class TestListenValidation:
    @patch("tkinter.messagebox.showerror")
    def test_listen_empty_text_shows_error(self, mock_error, app):
        """Listen with empty text should show an error."""
        app._input_nb.set("Teksti")
        app.update_idletasks()

        app._text_widget.delete("1.0", tk.END)
        app._text_has_placeholder = False
        app.update_idletasks()

        app._on_listen_click()

        mock_error.assert_called_once()

    @patch("tkinter.messagebox.showerror")
    def test_listen_pdf_mode_no_file_shows_error(self, mock_error, app):
        """Listen in PDF mode with no file should mention PDF."""
        app._input_nb.set("PDF-tiedosto")
        app._pdf_path = None
        app.update_idletasks()

        app._on_listen_click()

        mock_error.assert_called_once()
        msg = mock_error.call_args[0][1]
        assert "pdf" in msg.lower() or "PDF" in msg

    @patch("tkinter.messagebox.showerror")
    def test_listen_unavailable_engine_shows_install_hint(self, mock_error, app):
        """Listen with an unavailable engine should show install hint, not voice error."""
        register_engine(_UnavailableEngine)

        app._populate_engine_list()
        app.update_idletasks()

        # Select the unavailable engine.
        for display, eid in app._engine_display_to_id.items():
            if eid == "test_unavail":
                app._engine_cb.set(display)
                break
        app.update_idletasks()

        # Set up valid text so we pass text validation.
        app._input_nb.set("Teksti")
        app.update_idletasks()
        app._text_widget.delete("1.0", tk.END)
        app._text_widget.insert("1.0", "Test text for listening preview")
        app._text_has_placeholder = False

        app._on_listen_click()

        mock_error.assert_called()
        msg = mock_error.call_args[0][1]
        assert "ääni" not in msg.lower(), (
            f"Got voice-selection error instead of install hint: {msg}"
        )
        assert "install" in msg.lower(), (
            f"Expected install instructions in the error, got: {msg}"
        )


# ---------------------------------------------------------------------------
# Engine status display
# ---------------------------------------------------------------------------


class TestEngineStatusDisplay:
    def test_available_engine_not_blocked(self, app):
        """An available engine (edge-tts) should report available status."""
        from src.tts_base import get_engine

        engine = get_engine("edge")
        assert engine is not None
        status = engine.check_status()
        assert status.available

    def test_unavailable_engine_has_reason(self):
        """The unavailable test engine should include a reason string."""
        engine = _UnavailableEngine()
        status = engine.check_status()
        assert not status.available
        assert "install" in status.reason.lower()


# ---------------------------------------------------------------------------
# Log panel color coding
# ---------------------------------------------------------------------------


def _tags_at_end(log_widget) -> list[str]:
    """Return the list of tags applied to the last line of the log."""
    try:
        inner = log_widget._textbox  # CTkTextbox wraps a Tk Text
    except AttributeError:
        inner = log_widget
    # Position of the second-to-last char (skip the final newline).
    last_line_start = inner.index("end-2l linestart")
    return list(inner.tag_names(last_line_start))


class TestLogColoring:
    """Verify that log lines get the right severity tag based on content."""

    def test_warning_line_gets_warning_tag(self, app):
        app._clear_log()
        app._append_log_warning("WARNING: something odd happened")
        app.update_idletasks()
        tags = _tags_at_end(app._log_text)
        assert "log_warning" in tags

    def test_error_line_gets_error_tag(self, app):
        app._clear_log()
        app._append_log_error("\u2718 Something broke")
        app.update_idletasks()
        tags = _tags_at_end(app._log_text)
        assert "log_error" in tags

    def test_success_line_gets_success_tag(self, app):
        app._clear_log()
        app._append_log_success("\u2714 Valmis! Tallennettu: out.mp3")
        app.update_idletasks()
        tags = _tags_at_end(app._log_text)
        assert "log_success" in tags

    def test_plain_line_has_no_severity_tag(self, app):
        app._clear_log()
        app._append_log("Setup: engine=chatterbox_fi")
        app.update_idletasks()
        tags = _tags_at_end(app._log_text)
        assert "log_warning" not in tags
        assert "log_error" not in tags
        assert "log_success" not in tags


class TestAutoSeverityDetection:
    """Verify _handle_event routes lines to the right color based on content."""

    def _make_event(self, raw_line: str, kind: str = "log"):
        from src.launcher_bridge import ProgressEvent
        return ProgressEvent(kind=kind, raw_line=raw_line)

    def test_warning_keyword_routed_to_warning(self, app):
        app._clear_log()
        app._handle_event(self._make_event("WARNING: deprecated feature"))
        app.update_idletasks()
        tags = _tags_at_end(app._log_text)
        assert "log_warning" in tags

    def test_futurewarning_routed_to_warning(self, app):
        app._clear_log()
        app._handle_event(self._make_event(
            "FutureWarning: `LoRACompatibleLinear` is deprecated"
        ))
        app.update_idletasks()
        tags = _tags_at_end(app._log_text)
        assert "log_warning" in tags

    def test_error_kind_routed_to_error(self, app):
        app._clear_log()
        app._handle_event(self._make_event("Something failed", kind="error"))
        app.update_idletasks()
        tags = _tags_at_end(app._log_text)
        assert "log_error" in tags

    def test_checkmark_routed_to_success(self, app):
        app._clear_log()
        app._handle_event(self._make_event("\u2714 Valmis! out.mp3"))
        app.update_idletasks()
        tags = _tags_at_end(app._log_text)
        assert "log_success" in tags

    def test_plain_info_stays_plain(self, app):
        app._clear_log()
        app._handle_event(self._make_event("[setup] device=cuda"))
        app.update_idletasks()
        tags = _tags_at_end(app._log_text)
        assert "log_warning" not in tags
        assert "log_error" not in tags
        assert "log_success" not in tags

    def test_chunk_progress_routed_to_success(self, app):
        app._clear_log()
        app._handle_event(self._make_event(
            "[chapter 1/1] chunk 1/3 (1/3 total) - 0m13s elapsed, "
            "~0m26s remaining, RTF 1.14x"
        ))
        app.update_idletasks()
        tags = _tags_at_end(app._log_text)
        assert "log_success" in tags

    def test_chapter_idx_routed_to_success(self, app):
        app._clear_log()
        app._handle_event(self._make_event(
            "[chapter 1/1] idx=0 title='Text' chunks=3"
        ))
        app.update_idletasks()
        tags = _tags_at_end(app._log_text)
        assert "log_success" in tags

    def test_generic_tts_line_stays_plain(self, app):
        app._clear_log()
        app._handle_event(self._make_event("[tts] loading voice model"))
        app.update_idletasks()
        tags = _tags_at_end(app._log_text)
        assert "log_success" not in tags
        assert "log_warning" not in tags
        assert "log_error" not in tags


# ---------------------------------------------------------------------------
# Output-path auto-increment
# ---------------------------------------------------------------------------


class TestBumpOutputPath:
    """Never overwrite an existing MP3: auto-bump the numeric suffix."""

    def test_bump_numbered_file(self, app, tmp_path) -> None:
        existing = tmp_path / "texttospeech_3.mp3"
        existing.write_bytes(b"x")
        app._output_path = str(existing)
        app._bump_output_path_if_exists()
        from pathlib import Path
        assert Path(app._output_path).name == "texttospeech_4.mp3"
        assert not Path(app._output_path).exists()

    def test_bump_unnumbered_file_gets_suffix_2(self, app, tmp_path) -> None:
        existing = tmp_path / "book.mp3"
        existing.write_bytes(b"x")
        app._output_path = str(existing)
        app._bump_output_path_if_exists()
        from pathlib import Path
        assert Path(app._output_path).name == "book_2.mp3"

    def test_skips_already_taken_numbers(self, app, tmp_path) -> None:
        # Gaps get filled but existing files are skipped.
        (tmp_path / "texttospeech_1.mp3").write_bytes(b"x")
        (tmp_path / "texttospeech_2.mp3").write_bytes(b"x")
        (tmp_path / "texttospeech_3.mp3").write_bytes(b"x")
        app._output_path = str(tmp_path / "texttospeech_1.mp3")
        app._bump_output_path_if_exists()
        from pathlib import Path
        assert Path(app._output_path).name == "texttospeech_4.mp3"

    def test_no_bump_when_target_missing(self, app, tmp_path) -> None:
        target = tmp_path / "brand_new.mp3"
        app._output_path = str(target)
        app._bump_output_path_if_exists()
        assert app._output_path == str(target)

    def test_entry_widget_updated(self, app, tmp_path) -> None:
        existing = tmp_path / "texttospeech_1.mp3"
        existing.write_bytes(b"x")
        app._output_path = str(existing)
        app._bump_output_path_if_exists()
        app.update_idletasks()
        shown = app._out_entry.get()
        assert shown.endswith("texttospeech_2.mp3")

    def test_preserves_user_chosen_flag(self, app, tmp_path) -> None:
        """Bumping doesn't reset the 'user-picked folder' flag."""
        existing = tmp_path / "my_book.mp3"
        existing.write_bytes(b"x")
        app._output_path = str(existing)
        app._output_user_chosen = True
        app._bump_output_path_if_exists()
        assert app._output_user_chosen is True
