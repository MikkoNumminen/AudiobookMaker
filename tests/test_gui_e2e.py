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
    from src.tts_chatterbox_bridge import ChatterboxFiEngine
    from src.gui_unified import UnifiedApp

    # Ensure engines are registered (decorators only fire on first import)
    if "edge" not in _REGISTRY:
        _REGISTRY["edge"] = EdgeTTSEngine
    if "piper" not in _REGISTRY:
        _REGISTRY["piper"] = PiperTTSEngine
    if "chatterbox_fi" not in _REGISTRY:
        _REGISTRY["chatterbox_fi"] = ChatterboxFiEngine

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
    from src.tts_chatterbox_bridge import ChatterboxFiEngine

    if "edge" not in _REGISTRY:
        _REGISTRY["edge"] = EdgeTTSEngine
    if "piper" not in _REGISTRY:
        _REGISTRY["piper"] = PiperTTSEngine
    if "chatterbox_fi" not in _REGISTRY:
        _REGISTRY["chatterbox_fi"] = ChatterboxFiEngine

    _shared_app.update_idletasks()
    return _shared_app


@pytest.fixture(autouse=True)
def _reset_app_state(app):
    """Reset shared app to a known baseline before each test.

    The underlying UnifiedApp instance is module-scoped (Tkinter can't
    create/destroy multiple roots safely in one interpreter), so without
    this reset every test inherits whatever engine/language/text state
    the previous test left behind. This fixture wipes the state that
    tests actually poke at, so tests run as if they had a fresh app.
    """
    # Run-state flags
    app._synth_running = False
    app._listening = False
    app._cancel_requested = False
    app._is_sample_run = False
    app._sample_output_path = None
    # I/O state
    app._pdf_path = None
    app._output_path = None
    app._output_user_chosen = False
    app._text_has_placeholder = True
    app._text_widget.delete("1.0", tk.END)
    # Combobox selections back to a known default
    app._lang_cb.set("Suomi")
    engine_values = list(app._engine_cb.cget("values"))
    if engine_values:
        app._engine_cb.set(engine_values[0])
    app._populate_engine_list()
    app._refresh_voice_list()
    app.update_idletasks()
    yield


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


class TestHeroHeader:
    """The redesigned header exposes the logo, title, and tagline as
    named attributes so tests and future tweaks can reach them.
    """

    def test_hero_widgets_exist(self, app):
        """Logo, title, and tagline widgets are all constructed."""
        assert hasattr(app, "_hero_logo")
        assert hasattr(app, "_hero_title")
        assert hasattr(app, "_hero_tagline")

    def test_hero_title_text(self, app):
        """Title reads AudiobookMaker regardless of the UI language."""
        assert app._hero_title.cget("text") == "AudiobookMaker"

    def test_hero_tagline_includes_version(self, app):
        """Tagline ends with the current app version — visual build marker."""
        from src.auto_updater import APP_VERSION

        tagline = app._hero_tagline.cget("text")
        assert f"v{APP_VERSION}" in tagline, (
            f"tagline {tagline!r} should include v{APP_VERSION}"
        )

    def test_hero_tagline_flips_on_language_toggle(self, app):
        """Switching UI language between Finnish and English flips the tagline."""
        # Start in Finnish.
        app._ui_lang = "fi"
        app._apply_ui_language()
        fi_text = app._hero_tagline.cget("text")
        assert "Kirjasi" in fi_text, fi_text

        # Flip to English.
        app._ui_lang = "en"
        app._apply_ui_language()
        en_text = app._hero_tagline.cget("text")
        assert "Your books" in en_text, en_text

        # Restore Finnish so later tests see the default state.
        app._ui_lang = "fi"
        app._apply_ui_language()

    def test_header_preserves_legacy_attributes(self, app):
        """_ui_lang_cb and _install_engines_btn remain reachable for
        the language-change handler, auto-updater hook, and existing tests.
        """
        assert hasattr(app, "_ui_lang_cb")
        assert hasattr(app, "_install_engines_btn")


# ---------------------------------------------------------------------------
# Chatterbox language-aware voice helper
# ---------------------------------------------------------------------------


class TestChatterboxVoiceHelper:
    """ChatterboxFiEngine.list_voices surfaces Grandmom once per supported
    language with a parenthetical tag so the voice dropdown is honest
    about what speaks what, matching the format used by Edge and Piper."""

    def test_finnish_returns_grandmom_with_suomi_tag(self) -> None:
        from src.tts_chatterbox_bridge import ChatterboxFiEngine

        voices = ChatterboxFiEngine().list_voices("fi")
        assert [v.display_name for v in voices] == ["Grandmom (suomi)"]

    def test_english_returns_grandmom_with_english_tag(self) -> None:
        from src.tts_chatterbox_bridge import ChatterboxFiEngine

        voices = ChatterboxFiEngine().list_voices("en")
        assert [v.display_name for v in voices] == ["Grandmom (English)"]

    def test_unknown_language_returns_empty(self) -> None:
        from src.tts_chatterbox_bridge import ChatterboxFiEngine

        engine = ChatterboxFiEngine()
        assert engine.list_voices("de") == []
        assert engine.list_voices("") == []


# ---------------------------------------------------------------------------
# Kieli <-> voice list interaction (Phase 2 engine bar)
# ---------------------------------------------------------------------------


@pytest.fixture
def _reset_kieli_after(app):
    """Put the shared app back to its default Kieli after a test so
    later tests in the module don't inherit Kieli/engine pollution."""
    yield
    app._lang_cb.set("Suomi")
    app._on_language_changed("Suomi")
    app.update_idletasks()


class TestKieliVoiceInteraction:
    """Kieli in the engine bar drives a Kieli -> Moottori -> Ääni funnel:
    changing the language re-filters engines and the voice dropdown,
    and the side-label truthfully reports how many voices are available."""

    def _set_language(self, app, label: str) -> None:
        """Simulate a user pick on the Kieli combobox."""
        app._lang_cb.set(label)
        app._on_language_changed(label)
        app.update_idletasks()

    def test_kieli_in_engine_bar_not_settings(self, app, _reset_kieli_after) -> None:
        # _lang_cb must be parented by the engine bar frame, not the
        # settings frame, so it's visible without expanding Asetukset.
        assert app._lang_cb.master is app._engine_bar
        # Sanity: the settings frame still exists but must not host Kieli.
        for child in app._settings_frame.winfo_children():
            assert child is not app._lang_cb

    def test_changing_kieli_refilters_edge_voices(self, app, _reset_kieli_after) -> None:
        # Pin the engine to Edge (which supports both fi and en).
        from src.tts_edge import EdgeTTSEngine

        edge_label = next(
            (lbl for lbl, eid in app._engine_display_to_id.items()
             if eid == "edge"),
            None,
        )
        assert edge_label is not None, "Edge engine missing from dropdown"
        app._engine_cb.set(edge_label)

        # Switch to English and confirm every listed voice is English.
        self._set_language(app, "English")
        voices_en = list(app._voice_cb.cget("values"))
        assert voices_en, "Edge should offer at least one English voice"
        en_ids = {v.id for v in EdgeTTSEngine().list_voices("en")}
        en_display_names = {v.display_name for v in EdgeTTSEngine().list_voices("en")}
        assert set(voices_en).issubset(en_display_names)

        # And back to Suomi — voices must switch to Finnish.
        self._set_language(app, "Suomi")
        voices_fi = list(app._voice_cb.cget("values"))
        fi_display_names = {v.display_name for v in EdgeTTSEngine().list_voices("fi")}
        assert set(voices_fi).issubset(fi_display_names)
        assert voices_fi != voices_en

    def test_chatterbox_grandmom_visible_for_both_languages(self, app, _reset_kieli_after) -> None:
        # Force a chatterbox_fi entry into the engine map regardless of
        # whether the dev machine actually has the venv installed — the
        # voice-list refresh path is what we're testing. The entry has
        # to be re-applied after each Kieli change because
        # _on_language_changed rebuilds the engine map from the
        # registry. We also stub the bridge engine's check_status so the
        # voice list renders even when the .venv-chatterbox doesn't exist
        # on the test machine.
        from unittest.mock import patch
        from src.tts_base import EngineStatus
        from src.tts_chatterbox_bridge import ChatterboxFiEngine

        fake_label = "test-chatterbox"

        def _force_chatterbox() -> None:
            app._engine_display_to_id[fake_label] = "chatterbox_fi"
            app._engine_cb.configure(
                values=list(app._engine_display_to_id.keys())
            )
            app._engine_cb.set(fake_label)

        with patch.object(
            ChatterboxFiEngine, "check_status",
            return_value=EngineStatus(available=True),
        ):
            self._set_language(app, "Suomi")
            _force_chatterbox()
            app._refresh_voice_list()
            fi_voices = list(app._voice_cb.cget("values"))
            assert fi_voices == ["Grandmom (suomi)"]

            self._set_language(app, "English")
            _force_chatterbox()
            app._refresh_voice_list()
            en_voices = list(app._voice_cb.cget("values"))
            assert en_voices == ["Grandmom (English)"]

    def test_voice_count_label_updates(self, app, _reset_kieli_after) -> None:
        edge_label = next(
            (lbl for lbl, eid in app._engine_display_to_id.items()
             if eid == "edge"),
            None,
        )
        assert edge_label is not None
        app._engine_cb.set(edge_label)

        self._set_language(app, "Suomi")
        text_fi = app._voice_count_lbl.cget("text")
        # Expect the Finnish side-label to mention a non-zero count.
        assert text_fi, "Voice count side-label should not be empty"
        assert any(ch.isdigit() for ch in text_fi)

        self._set_language(app, "English")
        text_en = app._voice_count_lbl.cget("text")
        assert text_en
        assert text_en != text_fi  # count and/or language name differs


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
            uses_subprocess = False

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
        with patch("src.gui_unified._duration_estimate.estimate_job", autospec=True,
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
        # fg_color is the (light, dark) tuple from gui_style.SUCCESS.
        from src import gui_style
        assert tuple(fg) == tuple(gui_style.SUCCESS), (
            f"done-state strip should use gui_style.SUCCESS, got {fg!r}"
        )
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


# ---------------------------------------------------------------------------
# Voice pack import (Slice 4)
# ---------------------------------------------------------------------------


def _make_few_shot_pack(source_dir, name: str = "Test Pack") -> None:
    """Create a minimal valid few_shot voice pack on disk."""
    import yaml

    from src.voice_pack.pack import VOICE_PACK_FORMAT_VERSION

    source_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "name": name,
        "language": "fi",
        "tier": "few_shot",
        "tier_reason": "3.0 min — few-shot",
        "total_source_minutes": 3.0,
        "emotion_coverage": {"neutral": 10},
        "base_model": "chatterbox-multilingual",
        "format_version": VOICE_PACK_FORMAT_VERSION,
        "created_at": "2026-04-18T00:00:00+00:00",
        "notes": "",
    }
    (source_dir / "meta.yaml").write_text(
        yaml.safe_dump(meta, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (source_dir / "sample.wav").write_bytes(b"\x00" * 64)
    (source_dir / "reference.wav").write_bytes(b"\x00" * 64)


class TestVoicePackImport:
    """GUI wire-up of the voice pack import flow (install_pack + list_packs)."""

    def test_import_button_present_and_localized(self, app) -> None:
        """The Import voice pack button lives in the Settings panel and
        picks up whichever UI language is active."""
        assert hasattr(app, "_import_pack_btn")
        # Finnish default.
        assert "\u00e4\u00e4nipaketti" in app._import_pack_btn.cget("text").lower()

    def test_import_picks_no_folder_noop(self, app, monkeypatch, tmp_path) -> None:
        """Cancelled folder picker leaves the dropdown untouched."""
        root_dir = tmp_path / "packs_root"
        monkeypatch.setattr(
            "src.gui_unified.default_voice_packs_root", lambda: root_dir
        )
        monkeypatch.setattr(
            "src.voice_pack.pack.default_voice_packs_root", lambda: root_dir
        )
        with patch("src.gui_unified.filedialog.askdirectory", return_value=""):
            app._import_voice_pack()
        assert not root_dir.exists() or not any(root_dir.iterdir())

    def test_import_copies_pack_and_refreshes_dropdown(
        self, app, monkeypatch, tmp_path
    ) -> None:
        """A valid few_shot pack gets copied to the root and shows up
        next to Grandmom in the Chatterbox voice dropdown."""
        root_dir = tmp_path / "packs_root"
        source_dir = tmp_path / "incoming_pack"
        _make_few_shot_pack(source_dir, name="Granny Fixture")
        monkeypatch.setattr(
            "src.gui_unified.default_voice_packs_root", lambda: root_dir
        )
        monkeypatch.setattr(
            "src.voice_pack.pack.default_voice_packs_root", lambda: root_dir
        )

        # Switch to Chatterbox so pack voices surface in the dropdown.
        chatterbox_display = next(
            (d for d, eid in app._engine_display_to_id.items()
             if eid == "chatterbox_fi"),
            None,
        )
        if chatterbox_display is None:
            pytest.skip("Chatterbox engine not registered in this test run")
        app._engine_cb.set(chatterbox_display)

        with patch(
            "src.gui_unified.filedialog.askdirectory",
            return_value=str(source_dir),
        ):
            app._import_voice_pack()
        app.update_idletasks()

        # Pack copied to user-data root.
        assert root_dir.exists()
        copied = list(root_dir.iterdir())
        assert len(copied) == 1, "install_pack should copy exactly one pack"

        # Dropdown now contains an entry tagged with the voice-pack label.
        values = list(app._voice_cb.cget("values"))
        tagged = [v for v in values if "Granny Fixture" in v]
        assert tagged, f"Expected pack entry in {values}"

        # Active selection points at the newly imported pack.
        assert "Granny Fixture" in app._voice_cb.get()

    def test_import_rejects_invalid_folder(self, app, monkeypatch, tmp_path) -> None:
        """A folder without meta.yaml raises an error dialog and does
        not touch the packs root."""
        root_dir = tmp_path / "packs_root"
        bogus_dir = tmp_path / "bogus"
        bogus_dir.mkdir()
        monkeypatch.setattr(
            "src.gui_unified.default_voice_packs_root", lambda: root_dir
        )
        monkeypatch.setattr(
            "src.voice_pack.pack.default_voice_packs_root", lambda: root_dir
        )

        errors: list[tuple[str, str]] = []
        with patch(
            "src.gui_unified.filedialog.askdirectory",
            return_value=str(bogus_dir),
        ), patch(
            "src.gui_unified.messagebox.showerror",
            side_effect=lambda title, msg: errors.append((title, msg)),
        ):
            app._import_voice_pack()
        assert errors, "Invalid pack should trigger an error dialog"
        assert not root_dir.exists() or not any(root_dir.iterdir())

    def test_voice_pack_reference_auto_populated(
        self, app, monkeypatch, tmp_path
    ) -> None:
        """Selecting an imported few_shot pack makes
        ``_effective_reference_audio`` return the pack's reference.wav
        even when the user hasn't typed anything into Ref. ääni."""
        root_dir = tmp_path / "packs_root"
        source_dir = tmp_path / "incoming_pack"
        _make_few_shot_pack(source_dir, name="Auto Ref")
        monkeypatch.setattr(
            "src.gui_unified.default_voice_packs_root", lambda: root_dir
        )
        monkeypatch.setattr(
            "src.voice_pack.pack.default_voice_packs_root", lambda: root_dir
        )

        from src.tts_base import Voice

        with patch(
            "src.gui_unified.filedialog.askdirectory",
            return_value=str(source_dir),
        ):
            app._import_voice_pack()

        # Resolve the installed pack back to its slug, then simulate the
        # pick without going through combobox display-name plumbing.
        packs = app._list_installed_voice_packs()
        assert len(packs) == 1
        pack = packs[0]
        voice = Voice(
            id=f"voicepack:{pack.root.name}",
            display_name=pack.display_name,
            language="fi",
        )
        resolved = app._effective_reference_audio(voice, manual_ref=None)
        assert resolved is not None
        assert resolved.endswith("reference.wav")

    def test_manual_ref_overrides_pack_reference(
        self, app, monkeypatch, tmp_path
    ) -> None:
        """If the user has typed a Ref. ääni path, it wins over the
        pack's default — lets power users tweak per-run without
        un-picking the pack from the dropdown."""
        root_dir = tmp_path / "packs_root"
        source_dir = tmp_path / "incoming_pack"
        _make_few_shot_pack(source_dir, name="Override Me")
        monkeypatch.setattr(
            "src.gui_unified.default_voice_packs_root", lambda: root_dir
        )
        monkeypatch.setattr(
            "src.voice_pack.pack.default_voice_packs_root", lambda: root_dir
        )

        from src.tts_base import Voice

        with patch(
            "src.gui_unified.filedialog.askdirectory",
            return_value=str(source_dir),
        ):
            app._import_voice_pack()

        packs = app._list_installed_voice_packs()
        voice = Voice(
            id=f"voicepack:{packs[0].root.name}",
            display_name=packs[0].display_name,
            language="fi",
        )
        manual = str(tmp_path / "manual.wav")
        resolved = app._effective_reference_audio(voice, manual_ref=manual)
        assert resolved == manual
