"""Unit tests for the synthesis orchestration mixin (SynthMixin).

Covers audit report v2 item 4.1: gui_synth_mixin.py had 0 unit tests
despite being 355 LoC of non-trivial state-machine + event-routing code.

These tests reuse the shared UnifiedApp fixture from test_gui_e2e.py
(UnifiedApp IS a SynthMixin host) and patch heavily so no real
Chatterbox subprocess or Edge-TTS network call is spawned.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.gui_synth_mixin import SynthMixin
from src.launcher_bridge import ProgressEvent
from src.tts_base import _REGISTRY


# ---------------------------------------------------------------------------
# Shared module-scoped UnifiedApp, mirroring tests/test_gui_e2e.py. Tkinter
# can only host one root window per interpreter, so we create it once per
# module and reset per-test state in the autouse fixture below.
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
def app(_shared_app):
    from src.tts_edge import EdgeTTSEngine
    from src.tts_piper import PiperTTSEngine

    if "edge" not in _REGISTRY:
        _REGISTRY["edge"] = EdgeTTSEngine
    if "piper" not in _REGISTRY:
        _REGISTRY["piper"] = PiperTTSEngine

    _shared_app.update_idletasks()
    return _shared_app


# ---------------------------------------------------------------------------
# Autouse: reset synth-related state before each test so mutations in one
# test can't bleed into the next.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_synth_state(app):
    app._synth_running = False
    app._cancel_requested = False
    app._cancel_flag.clear()
    app._is_sample_run = False
    app._pdf_path = None
    app._output_path = None
    app._text_has_placeholder = True
    app._chatterbox_runner = None
    # Drain any pending events from previous tests.
    try:
        while True:
            app._event_queue.get_nowait()
    except queue.Empty:
        pass
    app._text_widget.delete("1.0", tk.END)
    # Restore idle widget state: the mixin's _set_running_state hides the
    # cancel button and disables the open-folder one on entry; the idle
    # helper doesn't re-grid the cancel button but leaves it grid_remove'd.
    app._cancel_btn.grid_remove()
    app._listen_btn.configure(state="normal")
    app._convert_btn.configure(state="normal")
    app._sample_btn.configure(state="normal")
    app._open_folder_btn.configure(state="normal")
    app.update_idletasks()
    yield


# ---------------------------------------------------------------------------
# _set_running_state / _set_idle_state
# ---------------------------------------------------------------------------


class TestRunningIdleStateTransitions:
    def test_running_disables_action_buttons_and_shows_cancel(self, app):
        # Precondition: cancel button is hidden.
        assert app._cancel_btn.winfo_manager() == ""
        app._set_running_state()
        app.update_idletasks()
        assert str(app._convert_btn.cget("state")) == "disabled"
        assert str(app._sample_btn.cget("state")) == "disabled"
        assert str(app._listen_btn.cget("state")) == "disabled"
        # Cancel button must be visible (gridded) while a run is live.
        assert app._cancel_btn.winfo_manager() == "grid"
        assert app._synth_running is True
        assert app._cancel_requested is False
        assert not app._cancel_flag.is_set()

    def test_mixin_running_state_disables_open_folder_button(self, app):
        # Pin the mixin contract directly: a run start must disable the
        # "Avaa kansio" button so the user can't open a stale folder
        # mid-synthesis. SynthMixin is now the canonical implementation
        # (UnifiedApp no longer overrides this method — see the mixin
        # dedup commit), but we still invoke the method explicitly on
        # SynthMixin so the test stays independent of MRO regressions.
        app._open_folder_btn.configure(state="normal")
        app.update_idletasks()
        calls: list = []
        real_configure = app._open_folder_btn.configure

        def _spy(*args, **kwargs):
            calls.append((args, kwargs))
            return real_configure(*args, **kwargs)

        with patch.object(app._open_folder_btn, "configure", side_effect=_spy):
            SynthMixin._set_running_state(app)
            app.update_idletasks()
        assert any(kw.get("state") == "disabled" for _a, kw in calls), (
            f"Expected a configure(state='disabled') call; got {calls!r}"
        )

    def test_running_status_label_for_sample_run(self, app):
        app._is_sample_run = True
        app._set_running_state()
        app.update_idletasks()
        text = app._status_label_val.cget("text")
        assert text == app._s("making_sample")

    def test_running_status_label_for_full_run(self, app):
        app._is_sample_run = False
        app._set_running_state()
        app.update_idletasks()
        text = app._status_label_val.cget("text")
        assert text == app._s("converting")

    def test_idle_reverses_running_state(self, app):
        app._set_running_state()
        app.update_idletasks()
        app._set_idle_state()
        app.update_idletasks()
        assert str(app._convert_btn.cget("state")) == "normal"
        assert str(app._sample_btn.cget("state")) == "normal"
        assert str(app._listen_btn.cget("state")) == "normal"
        # Cancel button hidden again.
        assert app._cancel_btn.winfo_manager() == ""
        assert app._synth_running is False


# ---------------------------------------------------------------------------
# _handle_event severity routing
# ---------------------------------------------------------------------------


class TestHandleEventRouting:
    def test_error_kind_goes_to_error_log(self, app):
        ev = ProgressEvent(kind="error", raw_line="boom")
        with patch.object(app, "_append_log_error") as mock_err, \
             patch.object(app, "_append_log_warning") as mock_warn, \
             patch.object(app, "_append_log_success") as mock_ok, \
             patch.object(app, "_append_log") as mock_plain:
            app._handle_event(ev)
        mock_err.assert_called_once_with("boom")
        mock_warn.assert_not_called()
        mock_ok.assert_not_called()
        mock_plain.assert_not_called()

    def test_chunk_progress_line_goes_to_success_log(self, app):
        line = "[chapter 3/10] chunk 5/12 (14/120 total) - 0m05s elapsed"
        ev = ProgressEvent(kind="log", raw_line=line)
        with patch.object(app, "_append_log_success") as mock_ok, \
             patch.object(app, "_append_log_error") as mock_err:
            app._handle_event(ev)
        mock_ok.assert_called_once_with(line)
        mock_err.assert_not_called()


# ---------------------------------------------------------------------------
# Progress bar updates
# ---------------------------------------------------------------------------


class TestProgressBarUpdates:
    def test_chunk_event_sets_progress_to_fraction(self, app):
        ev = ProgressEvent(
            kind="chunk", raw_line="",
            total_done=5, total_chunks=10,
        )
        with patch.object(app._progress_bar, "set") as mock_set:
            app._handle_event(ev)
        mock_set.assert_called_with(0.5)

    def test_done_event_lifts_progress_to_one(self, app):
        ev = ProgressEvent(kind="done", raw_line="")
        with patch.object(app._progress_bar, "set") as mock_set:
            app._handle_event(ev)
        # kind="done" is the terminal progress event (see launcher_bridge
        # line 224+ — emitted when the subprocess reports all chunks
        # flushed). _handle_event maps it to progress=1.0.
        assert (1.0,) in [c.args for c in mock_set.call_args_list]


# ---------------------------------------------------------------------------
# _pump_events scheduling
# ---------------------------------------------------------------------------


class TestPumpEventsScheduling:
    """Tests target the SynthMixin._pump_events method directly.

    UnifiedApp overrides _pump_events with its own pump loop (see
    gui_unified.py line 2808); that override has different semantics
    (no had_exit guard, different kind routing). To exercise the
    mixin's behavior we call SynthMixin._pump_events(app, ...) so the
    override is bypassed. Documented as a behavior-divergence bug in
    the audit follow-up report — do NOT fix here.
    """

    @staticmethod
    def _make_spy(app):
        """Capture only calls that reschedule _pump_events (bound- or
        unbound-method form) and let all other after() calls through so
        CTk widgets keep working during log writes."""
        real_after = app.after
        collected: list = []
        mixin_pump = SynthMixin._pump_events

        def _spy(ms, func=None, *args):
            if func is not None:
                f = getattr(func, "__func__", func)
                if f is mixin_pump:
                    collected.append((ms, func))
                    return "spy-id"
            return real_after(ms, func, *args) if func is not None else real_after(ms)

        return _spy, collected

    def test_reschedules_while_running(self, app):
        app._synth_running = True
        app._event_queue.put(ProgressEvent(kind="log", raw_line="hi"))
        spy, pump_calls = self._make_spy(app)
        # Bind the mixin's method directly to `app` so it calls back
        # into SynthMixin._pump_events, not the UnifiedApp override.
        bound = SynthMixin._pump_events.__get__(app, type(app))
        with patch.object(app, "after", side_effect=spy), \
             patch.object(app, "_pump_events", new=bound):
            SynthMixin._pump_events(app)
        assert len(pump_calls) == 1
        assert pump_calls[0][0] == app.POLL_INTERVAL_MS

    def test_stops_scheduling_when_not_running(self, app):
        app._synth_running = False
        app._event_queue.put(ProgressEvent(kind="log", raw_line="hi"))
        spy, pump_calls = self._make_spy(app)
        bound = SynthMixin._pump_events.__get__(app, type(app))
        with patch.object(app, "after", side_effect=spy), \
             patch.object(app, "_pump_events", new=bound):
            SynthMixin._pump_events(app)
        assert pump_calls == []

    def test_exit_event_stops_rescheduling_even_if_running_flag_set(self, app):
        # An "exit" event must short-circuit re-scheduling; _on_synth_exit
        # is responsible for flipping _synth_running off, but the
        # had_exit guard inside _pump_events provides defense in depth.
        app._synth_running = True
        app._event_queue.put(ProgressEvent(kind="exit", returncode=0))
        spy, pump_calls = self._make_spy(app)
        bound = SynthMixin._pump_events.__get__(app, type(app))
        with patch.object(app, "after", side_effect=spy), \
             patch.object(app, "_pump_events", new=bound), \
             patch.object(app, "_on_synth_exit"):
            SynthMixin._pump_events(app)
        assert pump_calls == []


# ---------------------------------------------------------------------------
# Cancel flag
# ---------------------------------------------------------------------------


class TestRequestCancel:
    def test_sets_cancel_flag_and_requested(self, app):
        assert not app._cancel_flag.is_set()
        app._request_cancel()
        assert app._cancel_flag.is_set()
        assert app._cancel_requested is True

    def test_calls_cancel_on_active_runner(self, app):
        fake_runner = MagicMock()
        app._chatterbox_runner = fake_runner
        app._request_cancel()
        fake_runner.cancel.assert_called_once()

    def test_no_runner_is_harmless(self, app):
        app._chatterbox_runner = None
        # Should not raise.
        app._request_cancel()
        assert app._cancel_flag.is_set()


# ---------------------------------------------------------------------------
# _start_chatterbox_subprocess
# ---------------------------------------------------------------------------


class TestStartChatterboxSubprocess:
    def test_text_override_writes_temp_file_with_prefix(self, app, tmp_path):
        # UnifiedApp doesn't expose _out_var (legacy attribute from the
        # old gui.py GUI); the mixin's hasattr guard falls back to
        # ~/Documents/AudiobookMaker. We patch mkdir so no directory
        # is actually created on disk during the test.
        captured: dict = {}

        def _fake_runner(**kwargs):
            captured.update(kwargs)
            inst = MagicMock()
            inst.start = MagicMock()
            return inst

        fake_tmp = MagicMock()
        fake_tmp.name = str(tmp_path / "mybook_sample_abcd.txt")

        with patch("src.synthesis_orchestrator.ChatterboxRunner", autospec=True,
                   side_effect=_fake_runner), \
             patch("src.synthesis_orchestrator.resolve_chatterbox_python", autospec=True,
                   return_value=Path("python.exe")), \
             patch("src.gui_synth_mixin.threading.Thread") as mock_thread, \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.mkdir"), \
             patch("src.synthesis_orchestrator.tempfile.NamedTemporaryFile",
                   return_value=fake_tmp) as mock_tmp:
            app._start_chatterbox_subprocess(
                text_override="hello world snippet",
                output_basename_override="mybook_sample",
            )

        # The mixin must ask for a temp file whose prefix starts with the
        # override basename — that's what makes the sample output land in
        # <out_dir>/mybook_sample/00_full.mp3.
        assert mock_tmp.called
        prefix = mock_tmp.call_args.kwargs.get("prefix")
        assert prefix == "mybook_sample_"
        fake_tmp.write.assert_called_with("hello world snippet")
        # The runner must be started and a relay thread spawned.
        assert captured["text_path"] == fake_tmp.name
        assert captured["pdf_path"] is None
        assert captured["epub_path"] is None
        mock_thread.assert_called_once()

    def test_chunk_chars_custom_value_appends_cli_flag(self, app, tmp_path):
        # With a non-default chunk size the mixin must pass
        # --chunk-chars <value> to the Chatterbox subprocess so the
        # runner honors the user's override.
        captured: dict = {}

        def _fake_runner(**kwargs):
            captured.update(kwargs)
            inst = MagicMock()
            inst.start = MagicMock()
            return inst

        fake_tmp = MagicMock()
        fake_tmp.name = str(tmp_path / "sample.txt")

        app._chunk_chars_var.set(500)
        with patch("src.synthesis_orchestrator.ChatterboxRunner", autospec=True,
                   side_effect=_fake_runner), \
             patch("src.synthesis_orchestrator.resolve_chatterbox_python", autospec=True,
                   return_value=Path("python.exe")), \
             patch("src.gui_synth_mixin.threading.Thread"), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.mkdir"), \
             patch("src.synthesis_orchestrator.tempfile.NamedTemporaryFile",
                   return_value=fake_tmp):
            app._start_chatterbox_subprocess(text_override="hello")

        # Restore default so other tests aren't perturbed.
        app._chunk_chars_var.set(300)

        extra = captured.get("extra_args") or []
        assert "--chunk-chars" in extra, f"expected --chunk-chars in {extra!r}"
        idx = extra.index("--chunk-chars")
        assert extra[idx + 1] == "500"

    def test_chunk_chars_default_omits_cli_flag(self, app, tmp_path):
        # At the default value (300) the mixin must NOT pass the flag,
        # so the runner's CLI default wins. Keeps default-case logs
        # clean and avoids leaking GUI state when it matches the CLI.
        captured: dict = {}

        def _fake_runner(**kwargs):
            captured.update(kwargs)
            inst = MagicMock()
            inst.start = MagicMock()
            return inst

        fake_tmp = MagicMock()
        fake_tmp.name = str(tmp_path / "sample.txt")

        app._chunk_chars_var.set(300)
        with patch("src.synthesis_orchestrator.ChatterboxRunner", autospec=True,
                   side_effect=_fake_runner), \
             patch("src.synthesis_orchestrator.resolve_chatterbox_python", autospec=True,
                   return_value=Path("python.exe")), \
             patch("src.gui_synth_mixin.threading.Thread"), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.mkdir"), \
             patch("src.synthesis_orchestrator.tempfile.NamedTemporaryFile",
                   return_value=fake_tmp):
            app._start_chatterbox_subprocess(text_override="hello")

        extra = captured.get("extra_args") or []
        assert "--chunk-chars" not in extra, (
            f"expected --chunk-chars absent at default 300, got {extra!r}"
        )

    def test_pdf_mode_without_path_bails_with_no_pdf_error(self, app):
        # _input_mode is a read-only property that maps the active
        # notebook tab to 'pdf' or 'text'; switching to the Kirja tab
        # is how the real app enters PDF mode.
        app._input_nb.set("Kirja")
        app.update_idletasks()
        assert app._input_mode == "pdf"
        app._pdf_path = None
        with patch.object(app, "_fail") as mock_fail, \
             patch("src.gui_synth_mixin.ChatterboxRunner", autospec=True) as mock_runner:
            app._start_chatterbox_subprocess()
        mock_fail.assert_called_once_with(app._s("no_pdf"))
        # Must not reach runner construction.
        mock_runner.assert_not_called()
