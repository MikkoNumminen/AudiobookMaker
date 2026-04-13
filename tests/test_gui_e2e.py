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


@pytest.fixture
def clean_registry():
    """Isolate each test from the real engine registry."""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


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
        """UnifiedApp can be created and has the expected window title."""
        assert app.title() == "AudiobookMaker"

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
