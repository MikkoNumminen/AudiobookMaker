"""Unit tests for the pure-ish logic in ``src.gui_unified.UnifiedApp``.

These complement ``tests/test_gui_unified.py`` (validation paths) and the
mixin tests by pinning the state helpers, input predicates, placeholder
handlers, output-path helpers, and the engine/language refresh cascade —
the parts that are real logic rather than widget layout.

Self-contained on purpose: this module defines its own ``_shared_app`` /
``app`` fixtures (mirroring test_gui_unified.py) rather than adding to
conftest, so a parallel session editing conftest can't disturb it. Tests
call methods directly and assert state or spy on collaborators; they do
NOT assert deep Tk rendering, which would be brittle.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.tts_base import _REGISTRY
from src.gui_unified import LANGUAGES, _detect_system_language


# ---------------------------------------------------------------------------
# Fixtures — a single real Tk root shared across this module's tests.
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

    _shared_app._synth_running = False
    _shared_app._listening = False
    _shared_app._is_sample_run = False
    _shared_app._pdf_path = None
    _shared_app._output_path = None
    _shared_app._last_playable_path = None
    _shared_app._text_has_placeholder = True
    _shared_app.update_idletasks()
    return _shared_app


def _set_mode(app, mode: str) -> None:
    """Switch the input tabview to the tab that maps to *mode* ('pdf'/'text')."""
    tab = next(k for k, v in app._tab_name_map.items() if v == mode)
    app._input_nb.set(tab)


@pytest.fixture
def _restore_input_state(app):
    """Undo input-tab / text-widget mutations so the shared app doesn't bleed
    state into sibling tests (the base ``app`` reset doesn't touch these)."""
    yield
    try:
        _set_mode(app, "pdf")
    except Exception:
        pass
    app._text_widget.delete("1.0", tk.END)
    app._text_has_placeholder = True


# ---------------------------------------------------------------------------
# _detect_system_language — module-level locale probe (no app needed)
# ---------------------------------------------------------------------------


class TestDetectSystemLanguage:
    def test_finnish_locale(self) -> None:
        with patch("locale.getdefaultlocale", return_value=("fi_FI", "UTF-8")):
            assert _detect_system_language() == "fi"

    def test_english_locale(self) -> None:
        with patch("locale.getdefaultlocale", return_value=("en_US", "UTF-8")):
            assert _detect_system_language() == "en"

    def test_none_locale_defaults_english(self) -> None:
        with patch("locale.getdefaultlocale", return_value=(None, None)):
            assert _detect_system_language() == "en"

    def test_locale_error_defaults_english(self) -> None:
        with patch("locale.getdefaultlocale", side_effect=ValueError("boom")):
            assert _detect_system_language() == "en"


# ---------------------------------------------------------------------------
# State helpers — _input_mode, _current_language / _engine_id / _voice
# ---------------------------------------------------------------------------


class TestStateHelpers:
    def test_input_mode_reflects_active_tab(self, app, _restore_input_state) -> None:
        _set_mode(app, "pdf")
        assert app._input_mode == "pdf"
        _set_mode(app, "text")
        assert app._input_mode == "text"

    def test_current_language_maps_combobox(self, app) -> None:
        fi_display = next(k for k, v in LANGUAGES.items() if v == "fi")
        en_display = next(k for k, v in LANGUAGES.items() if v == "en")
        app._lang_cb.set(fi_display)
        assert app._current_language() == "fi"
        app._lang_cb.set(en_display)
        assert app._current_language() == "en"

    def test_current_language_unknown_defaults_fi(self, app) -> None:
        app._lang_cb.set("Klingon")
        assert app._current_language() == "fi"

    def test_current_engine_id_maps_display(self, app) -> None:
        saved = app._engine_display_to_id
        try:
            app._engine_display_to_id = {"My Engine": "myeng"}
            app._engine_cb.set("My Engine")
            assert app._current_engine_id() == "myeng"
            app._engine_cb.set("Not a known display")
            assert app._current_engine_id() == ""
        finally:
            app._engine_display_to_id = saved
            app._engine_cb.set("")

    def test_current_engine_none_when_no_id(self, app) -> None:
        with patch.object(app, "_current_engine_id", return_value=""):
            assert app._current_engine() is None

    def test_current_voice_none_when_no_engine(self, app) -> None:
        with patch.object(app, "_current_engine", return_value=None):
            assert app._current_voice() is None

    def test_current_voice_resolves_from_engine_list(self, app) -> None:
        v = SimpleNamespace(display_name="MyVoice")
        fake_engine = SimpleNamespace(list_voices=lambda lang: [v])
        with patch.object(app, "_current_engine", return_value=fake_engine), \
             patch.object(app, "_current_language", return_value="fi"):
            app._voice_cb.set("MyVoice")
            assert app._current_voice() is v

    def test_current_voice_falls_through_to_voice_pack(self, app) -> None:
        pack_voice = SimpleNamespace(display_name="PackVoice")
        fake_engine = SimpleNamespace(list_voices=lambda lang: [])
        with patch.object(app, "_current_engine", return_value=fake_engine), \
             patch.object(app, "_current_language", return_value="fi"), \
             patch.object(app, "_voice_pack_voices", return_value=[pack_voice]):
            app._voice_cb.set("PackVoice")
            assert app._current_voice() is pack_voice


# ---------------------------------------------------------------------------
# Input predicates — _has_usable_input, _has_playable_output
# ---------------------------------------------------------------------------


class TestInputPredicates:
    def test_has_usable_input_pdf_mode(self, app, tmp_path, _restore_input_state) -> None:
        _set_mode(app, "pdf")
        app._pdf_path = str(tmp_path / "book.pdf")
        assert app._has_usable_input() is True
        app._pdf_path = None
        assert app._has_usable_input() is False

    def test_has_usable_input_text_mode(self, app, _restore_input_state) -> None:
        _set_mode(app, "text")
        app._text_has_placeholder = True
        assert app._has_usable_input() is False
        app._text_has_placeholder = False
        app._text_widget.delete("1.0", tk.END)
        app._text_widget.insert("1.0", "real content")
        assert app._has_usable_input() is True

    def test_has_playable_output_true_when_output_exists(self, app, tmp_path) -> None:
        f = tmp_path / "out.mp3"
        f.write_bytes(b"x")
        app._last_playable_path = None
        app._output_path = str(f)
        assert app._has_playable_output() is True

    def test_has_playable_output_uses_last_playable_path(self, app, tmp_path) -> None:
        f = tmp_path / "prev.mp3"
        f.write_bytes(b"x")
        app._last_playable_path = str(f)
        app._output_path = None
        assert app._has_playable_output() is True

    def test_has_playable_output_false_when_missing(self, app, tmp_path) -> None:
        app._last_playable_path = None
        app._output_path = str(tmp_path / "missing.mp3")
        assert app._has_playable_output() is False


# ---------------------------------------------------------------------------
# Placeholder / focus handlers
# ---------------------------------------------------------------------------


class TestTextPlaceholderHandlers:
    def test_focus_in_clears_placeholder(self, app, _restore_input_state) -> None:
        app._text_has_placeholder = True
        app._text_widget.delete("1.0", tk.END)
        app._text_widget.insert("1.0", app._text_placeholder)
        app._on_text_focus_in()
        assert app._text_has_placeholder is False
        assert app._text_widget.get("1.0", tk.END).strip() == ""

    def test_focus_in_noop_without_placeholder(self, app, _restore_input_state) -> None:
        app._text_has_placeholder = False
        app._text_widget.delete("1.0", tk.END)
        app._text_widget.insert("1.0", "real text")
        app._on_text_focus_in()
        assert app._text_widget.get("1.0", tk.END).strip() == "real text"

    def test_focus_out_restores_placeholder_when_empty(self, app, _restore_input_state) -> None:
        app._text_has_placeholder = False
        app._text_widget.delete("1.0", tk.END)
        app._on_text_focus_out()
        assert app._text_has_placeholder is True
        assert app._text_placeholder in app._text_widget.get("1.0", tk.END)

    def test_focus_out_keeps_real_text(self, app, _restore_input_state) -> None:
        app._text_has_placeholder = False
        app._text_widget.delete("1.0", tk.END)
        app._text_widget.insert("1.0", "keep me")
        app._on_text_focus_out()
        assert app._text_has_placeholder is False
        assert "keep me" in app._text_widget.get("1.0", tk.END)

    def test_keyrelease_refreshes_button_state(self, app) -> None:
        with patch.object(app, "_update_action_buttons_state") as upd:
            app._on_text_keyrelease()
        upd.assert_called_once()


# ---------------------------------------------------------------------------
# Output-path helpers — _bump_output_path_if_exists, _auto_output_path
# ---------------------------------------------------------------------------


class TestOutputPathHelpers:
    def test_bump_increments_when_file_exists(self, app, tmp_path) -> None:
        existing = tmp_path / "book.mp3"
        existing.write_bytes(b"x")
        app._output_path = str(existing)
        app._bump_output_path_if_exists()
        assert app._output_path != str(existing)
        assert Path(app._output_path).name != "book.mp3"

    def test_bump_noop_when_path_is_fresh(self, app, tmp_path) -> None:
        fresh = str(tmp_path / "fresh.mp3")
        app._output_path = fresh
        app._bump_output_path_if_exists()
        assert app._output_path == fresh

    def test_bump_noop_when_unset(self, app) -> None:
        app._output_path = None
        app._bump_output_path_if_exists()  # must not raise
        assert app._output_path is None

    def test_auto_output_path_sets_state_and_widget(self, app, tmp_path) -> None:
        fixed = str(tmp_path / "auto.mp3")
        with patch("src.gui_unified.suggest_output_path", return_value=fixed):
            app._auto_output_path()
        assert app._output_path == fixed
        assert app._out_entry.get() == fixed


# ---------------------------------------------------------------------------
# Engine / language refresh cascade + settings toggle
# ---------------------------------------------------------------------------


class TestRefreshCascade:
    def test_on_tab_changed_updates_mode_and_refreshes(self, app, _restore_input_state) -> None:
        _set_mode(app, "text")
        with patch.object(app, "_auto_output_path") as auto, \
             patch.object(app, "_update_action_buttons_state") as upd:
            app._on_tab_changed()
        assert app._input_mode_raw == "text"
        auto.assert_called_once()
        upd.assert_called_once()

    def test_on_engine_changed_refreshes_voices_and_strip(self, app) -> None:
        with patch.object(app, "_refresh_voice_list") as rv, \
             patch.object(app, "_refresh_ready_status_strip") as rs:
            app._on_engine_changed()
        rv.assert_called_once()
        rs.assert_called_once()

    def test_on_language_changed_runs_full_cascade(self, app) -> None:
        with patch.object(app, "_populate_engine_list") as pe, \
             patch.object(app, "_refresh_voice_list") as rv, \
             patch.object(app, "_refresh_ready_status_strip") as rs, \
             patch.object(app, "_save_current_config") as sc:
            app._on_language_changed()
        pe.assert_called_once()
        rv.assert_called_once()
        rs.assert_called_once()
        sc.assert_called_once()

    def test_toggle_settings_flips_open_flag(self, app) -> None:
        app._settings_open = False
        app._toggle_settings()
        assert app._settings_open is True
        app._toggle_settings()
        assert app._settings_open is False

    def test_update_voice_count_label_renders_count(self, app) -> None:
        if not hasattr(app, "_voice_count_lbl"):
            pytest.skip("voice count label not built")
        with patch.object(app._voice_count_lbl, "configure") as cfg:
            app._update_voice_count_label(3)
        cfg.assert_called_once()
        assert "3" in cfg.call_args.kwargs.get("text", "")
