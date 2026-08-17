"""Tests for how the GUI reacts when the runner subprocess dies.

Field incident (2026-08-14): a ~14 h conversion stopped overnight and the app
sat in "Converting…" indefinitely — bar frozen, Convert greyed out, no dialog.
The bridge had emitted ``ProgressEvent(kind="exit", returncode=rc)`` all along,
but ``_pump_events`` had no branch for it, so the event fell through to the
reschedule at the bottom of the loop and the pump re-armed itself forever.

A healthy run prints a completion line, which the parser turns into a "done"
event, and that branch returns before "exit" is ever drained. So reaching the
exit branch always means the run failed.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.launcher_bridge import ProgressEvent
from src.tts_base import _REGISTRY


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
    _shared_app._synth_running = True
    _shared_app._is_sample_run = False
    _shared_app._chatterbox_runner = None
    while not _shared_app._event_queue.empty():
        _shared_app._event_queue.get_nowait()
    return _shared_app


def _drain(app) -> list:
    """Run one pump pass, returning the reschedule calls it made."""
    scheduled: list = []
    with patch.object(app, "after", side_effect=lambda *a, **k: scheduled.append(a)):
        app._pump_events()
    return scheduled


class TestExitStopsThePump:
    def test_exit_event_reports_failure(self, app):
        app._event_queue.put(ProgressEvent(kind="exit", returncode=1))
        with patch.object(app, "_fail") as fail:
            _drain(app)
        assert fail.call_count == 1

    def test_exit_event_does_not_reschedule(self, app):
        """The defect: the pump re-armed itself forever and the UI hung."""
        app._event_queue.put(ProgressEvent(kind="exit", returncode=1))
        with patch.object(app, "_fail"):
            scheduled = _drain(app)
        assert scheduled == []

    def test_zero_returncode_without_done_is_still_a_failure(self, app):
        """A terminated runner can report 0 on Windows.

        The absence of the completion event is what makes this a failure, so
        the return code must not be used to wave the run through as a success.
        """
        app._event_queue.put(ProgressEvent(kind="exit", returncode=0))
        with patch.object(app, "_fail") as fail:
            _drain(app)
        assert fail.call_count == 1

    def test_done_before_exit_wins(self, app):
        """A healthy run returns on "done" and never reaches the exit branch."""
        app._event_queue.put(
            ProgressEvent(kind="done", total_done=10, total_chunks=10)
        )
        app._event_queue.put(ProgressEvent(kind="exit", returncode=0))
        with patch.object(app, "_fail") as fail, \
                patch.object(app, "_finalize_chatterbox_output_if_needed"), \
                patch.object(app, "_log_success_summary"), \
                patch.object(app, "_update_done_strip"):
            _drain(app)
        assert fail.call_count == 0


class TestExitMessage:
    def test_message_carries_the_return_code(self, app):
        app._event_queue.put(ProgressEvent(kind="exit", returncode=3221225477))
        with patch.object(app, "_fail") as fail:
            _drain(app)
        assert "3221225477" in fail.call_args[0][0]

    def test_message_includes_the_stdout_tail(self, app):
        """A Python traceback never matches the `[error]` prefix the parser
        looks for, so it arrives as ordinary log lines. The tail is the only
        description of the failure the user gets."""
        class _Runner:
            def tail_lines(self, n=20):
                return ["  File \"runner.py\", line 1", "MemoryError", ""]

        app._chatterbox_runner = _Runner()
        app._event_queue.put(ProgressEvent(kind="exit", returncode=1))
        with patch.object(app, "_fail") as fail:
            _drain(app)
        assert "MemoryError" in fail.call_args[0][0]

    def test_a_broken_runner_handle_does_not_mask_the_failure(self, app):
        """tail_lines() raising must not turn a failure into a silent hang."""
        class _Runner:
            def tail_lines(self, n=20):
                raise RuntimeError("handle already closed")

        app._chatterbox_runner = _Runner()
        app._event_queue.put(ProgressEvent(kind="exit", returncode=1))
        with patch.object(app, "_fail") as fail:
            scheduled = _drain(app)
        assert fail.call_count == 1
        assert scheduled == []


class TestUnknownKinds:
    def test_unknown_kind_keeps_pumping(self, app):
        """An unrecognised kind is logged, not treated as terminal."""
        app._event_queue.put(ProgressEvent(kind="brand_new_kind"))
        scheduled = _drain(app)
        assert scheduled != []

    def test_log_lines_keep_pumping(self, app):
        app._event_queue.put(ProgressEvent(kind="log", raw_line="hello"))
        scheduled = _drain(app)
        assert scheduled != []


class TestResumeVisibility:
    """A resumed run must look like a resume, not like starting over."""

    def test_cached_count_seeds_the_progress_bar(self, app):
        app._event_queue.put(
            ProgressEvent(kind="setup_cached", total_done=2229, total_chunks=4100)
        )
        _drain(app)
        assert app._progress_bar.get() == pytest.approx(2229 / 4100, abs=0.01)

    def test_cached_count_is_shown_to_the_user(self, app):
        app._event_queue.put(
            ProgressEvent(kind="setup_cached", total_done=2229, total_chunks=4100)
        )
        _drain(app)
        text = app._status_label_val.cget("text")
        assert "2229" in text and "4100" in text

    def test_a_fresh_run_does_not_claim_to_be_resuming(self, app):
        """Nothing cached means nothing to say about it."""
        app._progress_bar.set(0)
        app._event_queue.put(
            ProgressEvent(kind="setup_cached", total_done=0, total_chunks=4100)
        )
        _drain(app)
        assert app._progress_bar.get() == pytest.approx(0.0, abs=0.01)

    def test_it_keeps_pumping(self, app):
        app._event_queue.put(
            ProgressEvent(kind="setup_cached", total_done=10, total_chunks=100)
        )
        assert _drain(app) != []
