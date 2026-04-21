"""Unit tests for validation paths in ``src.gui_unified.UnifiedApp``.

These tests complement ``tests/test_gui_e2e.py`` by locking in validation
behaviours that were previously only covered transitively. The focus is
early-return paths (running flags) and gaps in the engine-availability
ordering for the sample and listen handlers.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
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
# Fixtures — mirror test_gui_e2e so we share a single Tk root across tests
# but keep this module self-contained (a parallel agent may be editing
# conftest.py, so we don't add fixtures there).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _shared_app():
    from src.tts_edge import EdgeTTSEngine
    from src.tts_piper import PiperTTSEngine
    from src.gui_unified import UnifiedApp

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
    from src.tts_edge import EdgeTTSEngine
    from src.tts_piper import PiperTTSEngine

    if "edge" not in _REGISTRY:
        _REGISTRY["edge"] = EdgeTTSEngine
    if "piper" not in _REGISTRY:
        _REGISTRY["piper"] = PiperTTSEngine

    # Reset per-test state that handlers mutate.
    _shared_app._synth_running = False
    _shared_app._listening = False
    _shared_app._is_sample_run = False
    _shared_app._sample_output_path = None
    _shared_app._pdf_path = None
    _shared_app._output_path = None
    _shared_app._text_has_placeholder = True
    _shared_app.update_idletasks()
    return _shared_app


class _UnavailableEngine(TTSEngine):
    id = "test_unavail_unit"
    display_name = "Unavailable Unit"
    description = "Always unavailable"

    def check_status(self) -> EngineStatus:
        return EngineStatus(
            available=False,
            reason="Install required: pip install some-engine",
        )

    def list_voices(self, language: str) -> list[Voice]:
        return []

    def default_voice(self, language: str) -> str | None:
        return None

    def synthesize(self, *args, **kwargs) -> None:
        raise RuntimeError("Not installed")


# ---------------------------------------------------------------------------
# Running-flag no-op tests: each handler must bail silently when a
# synthesis or listen session is already in flight, otherwise the user
# could launch overlapping threads that trample shared state.
# ---------------------------------------------------------------------------


class TestRunningFlagNoOps:
    @patch("tkinter.messagebox.showerror")
    @patch("tkinter.messagebox.showinfo")
    def test_convert_click_noop_when_synth_running(
        self, mock_info, mock_error, app,
    ):
        app._synth_running = True
        # Deliberately arrange invalid state so that any real validation
        # path would fire an error — the running-flag guard must short
        # circuit before we reach any of those branches.
        app._input_nb.set("Kirja")
        app._pdf_path = None
        app.update_idletasks()

        app._on_convert_click()

        mock_error.assert_not_called()
        mock_info.assert_not_called()

    @patch("tkinter.messagebox.showerror")
    @patch("tkinter.messagebox.showinfo")
    def test_sample_click_noop_when_synth_running(
        self, mock_info, mock_error, app,
    ):
        app._synth_running = True
        app._input_nb.set("Kirja")
        app._pdf_path = None
        app.update_idletasks()

        app._on_sample_click()

        mock_error.assert_not_called()
        mock_info.assert_not_called()

    @patch("tkinter.messagebox.showerror")
    @patch("tkinter.messagebox.showinfo")
    def test_listen_click_noop_when_listening(
        self, mock_info, mock_error, app,
    ):
        app._listening = True
        app._input_nb.set("Kirja")
        app._pdf_path = None
        app.update_idletasks()

        app._on_listen_click()

        mock_error.assert_not_called()
        mock_info.assert_not_called()

    @patch("tkinter.messagebox.showerror")
    @patch("tkinter.messagebox.showinfo")
    def test_listen_click_noop_when_synth_running(
        self, mock_info, mock_error, app,
    ):
        app._synth_running = True
        app._input_nb.set("Kirja")
        app._pdf_path = None
        app.update_idletasks()

        app._on_listen_click()

        mock_error.assert_not_called()
        mock_info.assert_not_called()


# ---------------------------------------------------------------------------
# Sample click: validation gaps that convert-click tests cover in e2e but
# sample does not.
# ---------------------------------------------------------------------------


class TestSampleValidationGaps:
    @patch("tkinter.messagebox.showerror")
    def test_sample_placeholder_text_counts_as_empty(self, mock_error, app):
        """Placeholder text in text mode must be treated as empty input."""
        app._input_nb.set("Teksti")
        app._text_has_placeholder = True
        app.update_idletasks()

        app._on_sample_click()

        mock_error.assert_called_once()
        msg = mock_error.call_args[0][1]
        assert "teksti" in msg.lower() or "text" in msg.lower()

    @patch("tkinter.messagebox.showerror")
    def test_sample_unavailable_engine_shows_install_hint(
        self, mock_error, app, tmp_path,
    ):
        """Sample path must show install instructions (not a voice
        selection error) when the selected engine is unavailable.

        Covers the same validation-ordering guarantee the convert-click
        e2e test locks in, but for the sample handler.
        """
        register_engine(_UnavailableEngine)
        app._populate_engine_list()
        app.update_idletasks()

        for display, eid in app._engine_display_to_id.items():
            if eid == "test_unavail_unit":
                app._engine_cb.set(display)
                break
        app.update_idletasks()

        app._input_nb.set("Teksti")
        app.update_idletasks()
        app._text_widget.delete("1.0", tk.END)
        app._text_widget.insert("1.0", "Riittävän pitkä näyteteksti testiä varten.")
        app._text_has_placeholder = False
        app._output_path = str(tmp_path / "kirja.mp3")

        app._on_sample_click()

        mock_error.assert_called()
        msg = mock_error.call_args[0][1]
        assert "ääni" not in msg.lower(), (
            f"Got voice-selection error instead of install hint: {msg}"
        )
        assert "install" in msg.lower(), (
            f"Expected install instructions in the error, got: {msg}"
        )


# ---------------------------------------------------------------------------
# Sample click: truncation contract.
# ---------------------------------------------------------------------------


class TestSampleTruncation:
    def test_sample_truncates_long_text_to_under_500_chars(self, app, tmp_path):
        """Long input text must be truncated to <=500 chars before being
        handed to the engine so the audition run stays ~30 s."""
        fake_voice = Voice(
            id="fake", display_name="Fake", language="en", gender="",
        )

        class _FakeEngine:
            display_name = "Fake"
            uses_subprocess = False

            def check_status(self):
                return EngineStatus(available=True)

        app._input_nb.set("Teksti")
        app.update_idletasks()
        long_text = (
            ("alku " * 30)
            + "Tämä on näytteen viimeinen lause. "
            + ("padding " * 500)
        )
        app._text_widget.delete("1.0", tk.END)
        app._text_widget.insert("1.0", long_text)
        app._text_has_placeholder = False
        app._output_path = str(tmp_path / "book.mp3")

        with patch.object(app, "_current_engine_id", return_value="edge"), \
             patch.object(app, "_current_engine", return_value=_FakeEngine()), \
             patch.object(app, "_current_voice", return_value=fake_voice), \
             patch.object(app, "_save_current_config"), \
             patch.object(app, "_start_inprocess_engine") as mock_start:
            app._on_sample_click()

        mock_start.assert_called_once()
        text_override = mock_start.call_args.kwargs["text_override"]
        assert 0 < len(text_override) <= 500, (
            f"Expected 1..500 chars, got {len(text_override)}"
        )
        # The truncation should keep the start of the input, not a tail
        # slice — otherwise the audition doesn't represent how the full
        # book opens.
        assert text_override.startswith("alku"), text_override[:40]


# ---------------------------------------------------------------------------
# Convert click text-mode happy path through PDF validation branch.
# ---------------------------------------------------------------------------


class TestConvertTextModeBypassesPdfCheck:
    @patch("tkinter.messagebox.showerror")
    def test_convert_text_mode_with_none_pdf_does_not_error_on_pdf(
        self, mock_error, app, tmp_path,
    ):
        """Text-mode convert with _pdf_path=None must NOT fire the
        'no_pdf' error — the PDF check is only active when the Kirja
        tab is selected.
        """
        app._input_nb.set("Teksti")
        app._pdf_path = None
        app._text_widget.delete("1.0", tk.END)
        app._text_widget.insert("1.0", "Jokin mielekäs teksti muunnosta varten.")
        app._text_has_placeholder = False
        app._output_path = str(tmp_path / "out.mp3")
        app.update_idletasks()

        # Stub the engine/voice layer and synthesis kickoff so we stop
        # right after validation — we only care that no PDF-related
        # messagebox fires along the way.
        fake_voice = Voice(
            id="fake", display_name="Fake", language="en", gender="",
        )

        class _FakeEngine:
            display_name = "Fake"
            uses_subprocess = False

            def check_status(self):
                return EngineStatus(available=True)

        with patch.object(app, "_current_engine_id", return_value="edge"), \
             patch.object(app, "_current_engine", return_value=_FakeEngine()), \
             patch.object(app, "_current_voice", return_value=fake_voice), \
             patch.object(app, "_save_current_config"), \
             patch.object(app, "_set_running_state"), \
             patch.object(app, "_start_inprocess_engine"):
            app._on_convert_click()

        # If any showerror fired, it must not be the PDF one.
        for call in mock_error.call_args_list:
            msg = call.args[1].lower()
            assert "kirja" not in msg and "pdf" not in msg, (
                f"Unexpected PDF error during text-mode convert: {msg}"
            )


# ---------------------------------------------------------------------------
# Sample-run flag bookkeeping.
# ---------------------------------------------------------------------------


class TestSampleRunFlag:
    def test_convert_click_clears_stale_sample_run_flag(self, app, tmp_path):
        """A leftover _is_sample_run from a previous sample press must
        be reset on Muunna so the completion handler doesn't mis-label
        a full-book run as a sample.
        """
        app._is_sample_run = True
        app._sample_output_path = "leftover.mp3"

        # Arrange a failing path so we exit quickly after the reset.
        app._input_nb.set("Kirja")
        app._pdf_path = None
        app.update_idletasks()

        with patch("tkinter.messagebox.showerror"):
            app._on_convert_click()

        assert app._is_sample_run is False
        assert app._sample_output_path is None

    def test_sample_click_sets_sample_run_flag_on_success(self, app, tmp_path):
        """A successful sample dispatch must mark the in-flight run so
        the completion handler announces it as an audition rather than
        a full conversion."""
        fake_voice = Voice(
            id="fake", display_name="Fake", language="en", gender="",
        )

        class _FakeEngine:
            display_name = "Fake"
            uses_subprocess = False

            def check_status(self):
                return EngineStatus(available=True)

        app._input_nb.set("Teksti")
        app.update_idletasks()
        app._text_widget.delete("1.0", tk.END)
        app._text_widget.insert(
            "1.0",
            "Tämä on riittävän pitkä näyteteksti lauseineen. " * 10,
        )
        app._text_has_placeholder = False
        app._output_path = str(tmp_path / "kirja.mp3")

        with patch.object(app, "_current_engine_id", return_value="edge"), \
             patch.object(app, "_current_engine", return_value=_FakeEngine()), \
             patch.object(app, "_current_voice", return_value=fake_voice), \
             patch.object(app, "_save_current_config"), \
             patch.object(app, "_set_running_state"), \
             patch.object(app, "_start_inprocess_engine"):
            app._on_sample_click()

        assert app._is_sample_run is True
        assert app._sample_output_path is not None
        assert str(app._sample_output_path).endswith("kirja_sample.mp3")


class TestChunkCharsVisibility:
    """Chatterbox chunk-chars spinbox must only be visible when Chatterbox
    is the active engine — for every other engine the knob is inert, so
    hiding it keeps the engine bar honest.

    Note: we check ``grid_info()`` rather than ``winfo_ismapped()`` because
    headless tests never actually map the toplevel, so ``ismapped`` is
    always 0 regardless of grid state. An empty ``grid_info()`` dict means
    ``grid_remove()`` was called — exactly what we want to assert.
    """

    def test_chunk_chars_hidden_for_edge(self, app):
        # Force the engine selection to Edge, then re-trigger the capability
        # refresh path — _refresh_voice_list is the documented entry point.
        for display, eid in app._engine_display_to_id.items():
            if eid == "edge":
                app._engine_cb.set(display)
                break
        else:
            pytest.skip("edge engine not registered")
        app._refresh_voice_list()
        app.update_idletasks()

        assert app._chunk_chars_spin.grid_info() == {}, (
            "chunk-chars spinbox must be hidden when Edge is selected"
        )
        assert app._chunk_chars_label.grid_info() == {}, (
            "chunk-chars label must be hidden when Edge is selected"
        )

    def test_chunk_chars_shown_for_chatterbox(self, app):
        """When the user flips back to Chatterbox the spinbox returns."""
        from src.tts_chatterbox_bridge import ChatterboxFiEngine

        # clean_registry wipes the registry between tests, and the module-
        # scoped _shared_app's _engine_display_to_id can go stale if an
        # earlier test populated it. Re-register Chatterbox and rebuild
        # the engine map so this test doesn't depend on ordering.
        if "chatterbox_fi" not in _REGISTRY:
            _REGISTRY["chatterbox_fi"] = ChatterboxFiEngine
        app._populate_engine_list()
        app.update_idletasks()

        target_display = None
        for display, eid in app._engine_display_to_id.items():
            if eid == "chatterbox_fi":
                target_display = display
                break
        assert target_display is not None, "chatterbox_fi must be in the map"
        app._engine_cb.set(target_display)
        app._refresh_voice_list()
        app.update_idletasks()

        assert app._chunk_chars_spin.grid_info() != {}, (
            "chunk-chars spinbox must be visible when Chatterbox is selected"
        )
        assert app._chunk_chars_label.grid_info() != {}, (
            "chunk-chars label must be visible when Chatterbox is selected"
        )


class TestProgressBarVisibilityLifecycle:
    """The conversion progress bar is a run-state indicator — it has no
    meaning when nothing is converting. Hidden by default, grid'd in on
    ``_set_running_state``, grid-removed again on ``_set_idle_state``.

    ``grid_info()`` dict check (not ``winfo_ismapped``) because headless
    tests never map the toplevel — same pattern as
    ``TestChunkCharsVisibility``.
    """

    def test_progress_bar_hidden_after_init(self, app):
        app._set_idle_state()
        app.update_idletasks()
        assert app._progress_bar.grid_info() == {}, (
            "progress bar must be hidden when no synthesis is running"
        )

    def test_progress_bar_shown_while_running(self, app):
        app._set_running_state()
        try:
            app.update_idletasks()
            assert app._progress_bar.grid_info() != {}, (
                "progress bar must be visible while a run is in flight"
            )
        finally:
            app._set_idle_state()
            app.update_idletasks()

    def test_progress_bar_hidden_on_return_to_idle(self, app):
        app._set_running_state()
        app.update_idletasks()
        app._set_idle_state()
        app.update_idletasks()
        assert app._progress_bar.grid_info() == {}, (
            "progress bar must hide again once the run returns to idle"
        )


class TestProgressiveDisclosure:
    """Action-row buttons follow configuration state:

    - Convert / Make sample: need input + voice.
    - Preview / Open folder: need a playable output from a prior run.

    A run in flight freezes this whole picture (``_set_running_state``
    owns button state), which we don't re-verify here because the
    progress-bar lifecycle suite already pins that.
    """

    def test_convert_disabled_without_input(self, app):
        # Text mode, placeholder still in place → no usable input.
        app._input_mode_raw = "text"
        for tab_name, mode in app._tab_name_map.items():
            if mode == "text":
                app._input_nb.set(tab_name)
                break
        app._text_has_placeholder = True
        app._pdf_path = None
        app._last_playable_path = None
        app._output_path = None
        app._update_action_buttons_state()
        app.update_idletasks()

        assert app._convert_btn.cget("state") == "disabled"
        assert app._sample_btn.cget("state") == "disabled"

    def test_convert_enabled_when_text_and_voice_ready(self, app):
        # Force the engine back to Edge (a prior test may have left the
        # combobox on something else that clean_registry then wiped) and
        # rebuild the display->id map so _current_voice resolves.
        app._populate_engine_list()
        for display, eid in app._engine_display_to_id.items():
            if eid == "edge":
                app._engine_cb.set(display)
                break
        app._refresh_voice_list()

        for tab_name, mode in app._tab_name_map.items():
            if mode == "text":
                app._input_nb.set(tab_name)
                app._input_mode_raw = "text"
                break
        app._text_has_placeholder = False
        app._text_widget.delete("1.0", tk.END)
        app._text_widget.insert("1.0", "Testilause.")
        app._pdf_path = None
        app._update_action_buttons_state()
        app.update_idletasks()

        assert app._current_voice() is not None, "fixture must ship a default voice"
        assert app._convert_btn.cget("state") == "normal"
        assert app._sample_btn.cget("state") == "normal"

    def test_preview_and_open_folder_require_output(self, app, tmp_path):
        # Even with input + voice ready, no prior output means these stay off.
        for tab_name, mode in app._tab_name_map.items():
            if mode == "text":
                app._input_nb.set(tab_name)
                app._input_mode_raw = "text"
                break
        app._text_has_placeholder = False
        app._text_widget.delete("1.0", tk.END)
        app._text_widget.insert("1.0", "Testilause.")
        app._last_playable_path = None
        app._output_path = None
        app._update_action_buttons_state()
        app.update_idletasks()
        assert app._listen_btn.cget("state") == "disabled"
        assert app._open_folder_btn.cget("state") == "disabled"

        # Now a real file exists on disk → both light up.
        out_mp3 = tmp_path / "done.mp3"
        out_mp3.write_bytes(b"fake")
        app._last_playable_path = str(out_mp3)
        app._update_action_buttons_state()
        app.update_idletasks()
        assert app._listen_btn.cget("state") == "normal"
        assert app._open_folder_btn.cget("state") == "normal"

    def test_set_idle_state_does_not_reenable_output_buttons_without_output(
        self, app
    ):
        """The mixin's _set_idle_state force-enables Open folder; the override
        must re-gate it when no run has actually produced anything."""
        app._last_playable_path = None
        app._output_path = None
        app._set_idle_state()
        app.update_idletasks()
        assert app._open_folder_btn.cget("state") == "disabled"
        assert app._listen_btn.cget("state") == "disabled"


class TestChatterboxFinalizePreservesNestedCache:
    """Nested dist/audiobook/<stem>/ must survive sample-run finalization."""

    def test_finalize_keeps_chunks_after_sample_copy(self, app, tmp_path: Path) -> None:
        out_root = tmp_path / "audiobook"
        nested = out_root / "book_stem"
        chunks = nested / ".chunks"
        chunks.mkdir(parents=True)
        wav_keep = chunks / "ch01_chunk0000.wav"
        wav_keep.write_bytes(b"fake wav")
        src_mp3 = nested / "01_Text.mp3"
        src_mp3.write_bytes(b"fake mp3")
        dst_flat = tmp_path / "sample_flat.mp3"

        app._chatterbox_last_mp3 = "book_stem/01_Text.mp3"
        app._is_sample_run = True
        app._sample_output_path = str(dst_flat)
        app._chatterbox_runner = SimpleNamespace(out_dir=str(out_root))

        app._finalize_chatterbox_output_if_needed()

        assert nested.is_dir()
        assert wav_keep.is_file()
        assert dst_flat.is_file()
