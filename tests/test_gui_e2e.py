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
# Chatterbox language-aware voice helper
# ---------------------------------------------------------------------------


class TestChatterboxVoiceHelper:
    """The helper surfaces Grandmom once per supported language with a
    parenthetical tag so the voice dropdown is honest about what speaks
    what, matching the format used by Edge and Piper voices."""

    def test_finnish_returns_grandmom_with_suomi_tag(self) -> None:
        from src.gui_unified import _chatterbox_voices_for_language

        names = _chatterbox_voices_for_language("fi")
        assert names == ["Grandmom (suomi)"]

    def test_english_returns_grandmom_with_english_tag(self) -> None:
        from src.gui_unified import _chatterbox_voices_for_language

        names = _chatterbox_voices_for_language("en")
        assert names == ["Grandmom (English)"]

    def test_unknown_language_returns_empty(self) -> None:
        from src.gui_unified import _chatterbox_voices_for_language

        assert _chatterbox_voices_for_language("de") == []
        assert _chatterbox_voices_for_language("") == []


# ---------------------------------------------------------------------------
# Update-banner browser fallback
# ---------------------------------------------------------------------------


class TestUpdateBrowserFallback:
    """Banner's 'Open in browser' button always works, even when the in-app
    update flow is broken (v3.3.1-style shadowing bug, file lock, etc.)."""

    def test_button_exists(self, app):
        assert hasattr(app, "_update_browser_btn")

    def test_click_opens_latest_when_no_pending_update(self, app):
        import webbrowser
        from unittest.mock import patch
        app._pending_update = None
        with patch.object(webbrowser, "open") as mock_open:
            app._on_update_browser_click()
        mock_open.assert_called_once()
        url = mock_open.call_args[0][0]
        assert "/releases/latest" in url

    def test_click_opens_specific_version_when_known(self, app):
        import webbrowser
        from unittest.mock import patch
        from src.auto_updater import UpdateInfo
        app._pending_update = UpdateInfo(
            available=True, current_version="3.3.1",
            latest_version="9.9.9", download_url="",
            release_notes="", asset_size_bytes=0, sha256="",
        )
        with patch.object(webbrowser, "open") as mock_open:
            app._on_update_browser_click()
        mock_open.assert_called_once()
        url = mock_open.call_args[0][0]
        assert "/releases/tag/v9.9.9" in url


# ---------------------------------------------------------------------------
# Convert validation
# ---------------------------------------------------------------------------


class TestConvertValidation:
    @patch("tkinter.messagebox.showerror")
    def test_convert_pdf_mode_no_file_shows_pdf_error(self, mock_error, app):
        """PDF mode with no file selected should mention PDF in the error."""
        app._pdf_path = None
        # Switch to PDF tab (the Finnish internal tab name).
        app._input_nb.set("Kirja")
        app.update_idletasks()

        app._on_convert_click()

        mock_error.assert_called_once()
        msg = mock_error.call_args[0][1]
        # The error should be about selecting a book file, not about voice/engine.
        assert "kirja" in msg.lower() or "book" in msg.lower()

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
        app._input_nb.set("Kirja")
        app._pdf_path = None
        app.update_idletasks()

        app._on_listen_click()

        mock_error.assert_called_once()
        msg = mock_error.call_args[0][1]
        assert "kirja" in msg.lower() or "book" in msg.lower()

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
# Sample button (Tee n\u00e4yte)
# ---------------------------------------------------------------------------


class TestSampleButton:
    """Tee n\u00e4yte: generate a 30s sample before a long full run."""

    def test_sample_button_exists(self, app):
        assert hasattr(app, "_sample_btn")

    @patch("tkinter.messagebox.showerror")
    def test_sample_pdf_mode_no_file_shows_error(self, mock_error, app):
        """PDF mode with no file selected should mention the book file."""
        app._pdf_path = None
        app._input_nb.set("Kirja")
        app.update_idletasks()

        app._on_sample_click()

        mock_error.assert_called_once()
        msg = mock_error.call_args[0][1]
        assert "kirja" in msg.lower() or "book" in msg.lower()

    @patch("tkinter.messagebox.showerror")
    def test_sample_empty_text_shows_error(self, mock_error, app):
        app._input_nb.set("Teksti")
        app.update_idletasks()
        app._text_widget.delete("1.0", tk.END)
        app._text_has_placeholder = False
        app.update_idletasks()

        app._on_sample_click()

        mock_error.assert_called_once()

    def test_sample_routes_to_inprocess_with_overrides(self, app, tmp_path):
        """A valid text input must call _start_inprocess_engine with the
        truncated text and a sibling _sample output path."""
        from src.tts_base import EngineStatus, Voice
        app._input_nb.set("Teksti")
        app.update_idletasks()
        # Long text with a sentence boundary safely past the 100-char
        # cutoff floor so extract_sample_text trims at it cleanly.
        long_text = (
            ("alku " * 30)
            + "T\u00e4m\u00e4 on n\u00e4ytteen viimeinen lause. "
            + ("padding " * 200)
        )
        app._text_widget.delete("1.0", tk.END)
        app._text_widget.insert("1.0", long_text)
        app._text_has_placeholder = False
        # Force an output path so _on_sample_click doesn't bail at the
        # _auto_output_path step.
        app._output_path = str(tmp_path / "kirja.mp3")

        # Stub out engine/voice resolution so we don't depend on the
        # default voice combobox population state.
        fake_voice = Voice(
            id="fake-voice", display_name="Fake", language="en", gender="",
        )

        class _FakeEngine:
            display_name = "Fake"
            def check_status(self):  # noqa: D401
                return EngineStatus(available=True)

        with patch.object(app, "_current_engine_id", return_value="edge"), \
             patch.object(app, "_current_engine", return_value=_FakeEngine()), \
             patch.object(app, "_current_voice", return_value=fake_voice), \
             patch.object(app, "_save_current_config"), \
             patch.object(app, "_start_inprocess_engine") as mock_start:
            app._on_sample_click()

        mock_start.assert_called_once()
        kwargs = mock_start.call_args.kwargs
        assert "text_override" in kwargs
        assert "output_path_override" in kwargs
        assert len(kwargs["text_override"]) <= 500
        assert kwargs["text_override"].endswith(".")
        assert kwargs["output_path_override"].endswith("kirja_sample.mp3")
        assert app._is_sample_run is True


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


# ---------------------------------------------------------------------------
# Sticky status strip
# ---------------------------------------------------------------------------


class TestStatusStrip:
    """Sticky one-line status strip between progress and log.

    Four states: idle (hidden), ready (blue, book + estimate),
    synthesizing (blue, live pct + ETA), done (green, wall + size).
    """

    def test_strip_hidden_by_default(self, app) -> None:
        # manager() returns {} when the widget isn't gridded.
        assert app._status_strip_frame.winfo_manager() == ""

    def test_ready_state_shows_name_and_estimates(self, app) -> None:
        from unittest.mock import patch
        fake = {
            "audio_seconds": 3600,
            "wall_seconds": 120,
            "chars_per_second_synth": 50.0,
            "audio_human": "1 h 0 min",
            "wall_human": "2 min 0 s",
        }
        with patch("src.gui_unified._duration_estimate.estimate_job",
                   return_value=fake):
            # Simulate what _refresh_ready_status_strip does, but bypass
            # file I/O by calling the setter directly.
            app._set_status_strip(
                "ready",
                name="Rubicon.epub",
                chars=809,
                audio_human=fake["audio_human"],
                wall_human=fake["wall_human"],
                engine_display="Edge TTS",
            )
            app.update_idletasks()
        text = app._status_strip_label.cget("text")
        assert "Rubicon.epub" in text
        assert "809" in text
        assert "1 h" in text
        assert app._status_strip_frame.winfo_manager() == "grid"

    def test_synthesizing_state_shows_pct_and_eta(self, app) -> None:
        app._set_status_strip(
            "synthesizing",
            name="book.pdf",
            pct=42,
            eta_human="5 min 30 s",
            hhmm="14:37",
            rtf_suffix="",
        )
        app.update_idletasks()
        text = app._status_strip_label.cget("text")
        assert "book.pdf" in text
        assert "42" in text
        assert "5 min" in text
        assert "14:37" in text

    def test_done_state_uses_success_color(self, app) -> None:
        app._set_status_strip(
            "done",
            name="done_book.pdf",
            wall_human="3 min 12 s",
            size_mb=7.5,
        )
        app.update_idletasks()
        fg = app._status_strip_frame.cget("fg_color")
        # fg_color is a (light, dark) tuple for this frame.
        assert "green" in str(fg).lower() or "darkgreen" in str(fg).lower()
        text = app._status_strip_label.cget("text")
        assert "done_book.pdf" in text
        assert "8 MB" in text or "7 MB" in text  # rounded via :.0f

    def test_idle_hides_strip(self, app) -> None:
        # Show first, then flip back to idle.
        app._set_status_strip(
            "ready",
            name="x.pdf", chars=1, audio_human="1 s",
            wall_human="1 s", engine_display="Edge",
        )
        app.update_idletasks()
        assert app._status_strip_frame.winfo_manager() == "grid"
        app._set_status_strip("idle")
        app.update_idletasks()
        assert app._status_strip_frame.winfo_manager() == ""
