"""Tests for the Continue affordance and the one-shot automatic retry.

The requirement these pin: after a failure the user must be able to pick the
conversion back up WITHOUT re-adding the source file, and continuing must
actually reuse the chunks already on disk rather than quietly starting from
zero. A Continue button that restarts is worse than no button, because it
burns another 14 hours and the user cannot tell.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src import job_state
from src.job_state import JobState
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
def app(_shared_app, clean_registry, tmp_path, monkeypatch):
    """App with the job file redirected into tmp_path."""
    job_file = tmp_path / "last_job.json"
    monkeypatch.setattr(job_state, "JOB_FILE", job_file)
    _shared_app._synth_running = False
    _shared_app._is_sample_run = False
    _shared_app._job_state = None
    _shared_app._pending_auto_retries = 0
    _shared_app._chatterbox_runner = None
    while not _shared_app._event_queue.empty():
        _shared_app._event_queue.get_nowait()
    return _shared_app


def _saved_job(tmp_path, **overrides) -> JobState:
    book = tmp_path / "book.pdf"
    book.write_bytes(b"%PDF-1.4 fake")
    fields = dict(
        input_mode="pdf",
        pdf_path=str(book),
        language="",
        out_dir=str(tmp_path / "out"),
        output_path_hint=str(tmp_path / "out.mp3"),
        status="failed",
        total_done=2229,
        total_chunks=4100,
    )
    fields.update(overrides)
    state = JobState(**fields)
    job_state.save(state)
    return state


class TestContinueVisibility:
    def test_hidden_when_there_is_nothing_to_continue(self, app):
        app._refresh_resume_affordance()
        assert app._continue_btn.grid_info() == {}

    def test_shown_after_a_failed_job(self, app, tmp_path):
        _saved_job(tmp_path)
        app._refresh_resume_affordance()
        app.update_idletasks()
        assert app._continue_btn.grid_info() != {}

    def test_hidden_after_a_finished_job(self, app, tmp_path):
        _saved_job(tmp_path, status="done")
        app._refresh_resume_affordance()
        app.update_idletasks()
        assert app._continue_btn.grid_info() == {}

    def test_hidden_while_a_run_is_in_flight(self, app, tmp_path):
        _saved_job(tmp_path)
        app._synth_running = True
        app._refresh_resume_affordance()
        assert app._continue_btn.grid_info() == {}
        app._synth_running = False

    def test_hint_reports_how_much_was_done(self, app, tmp_path):
        """The user needs to see that Continue skips work, not repeats it."""
        _saved_job(tmp_path)
        app._refresh_resume_affordance()
        text = app._resume_hint.cget("text")
        assert "2229" in text and "4100" in text

    def test_hint_says_when_the_app_already_retried(self, app, tmp_path):
        """An automatic retry that leaves no trace looks like a slow app."""
        _saved_job(tmp_path, auto_retries=1)
        app._refresh_resume_affordance()
        assert app._resume_hint.cget("text") != ""
        assert len(app._resume_hint.cget("text")) > 40


class TestContinueRestoresTheJob:
    def test_source_file_is_restored_without_the_user(self, app, tmp_path):
        """The whole point: no re-adding the file."""
        state = _saved_job(tmp_path)
        app._pdf_path = None
        with patch.object(app, "_on_convert_click"):
            app._on_continue_click()
        assert app._pdf_path == state.pdf_path

    def test_output_path_is_restored(self, app, tmp_path):
        """The chunk cache lives under the output dir. A re-derived path that
        differs at all is a silent full restart."""
        state = _saved_job(tmp_path)
        app._output_path = None
        with patch.object(app, "_on_convert_click"):
            app._on_continue_click()
        assert app._output_path == state.output_path_hint

    def test_continue_actually_starts_the_run(self, app, tmp_path):
        _saved_job(tmp_path)
        with patch.object(app, "_on_convert_click") as convert:
            app._on_continue_click()
        assert convert.call_count == 1

    def test_vanished_source_is_reported_not_retried(self, app, tmp_path):
        """Offering Continue for a deleted file would just fail again."""
        state = _saved_job(tmp_path)
        Path(state.pdf_path).unlink()
        with patch.object(app, "_on_convert_click") as convert, \
                patch("src.gui_unified.messagebox.showerror") as err:
            app._on_continue_click()
        assert convert.call_count == 0
        assert err.call_count == 1

    def test_a_changed_language_blocks_the_resume(self, app, tmp_path):
        """Appending chunks in one voice to a cache full of another produces
        a book that changes narrator partway through."""
        _saved_job(tmp_path, language="fi")
        with patch.object(app, "_current_language", return_value="en"), \
                patch.object(app, "_on_convert_click") as convert, \
                patch("src.gui_unified.messagebox.showerror") as err:
            app._on_continue_click()
        assert convert.call_count == 0
        assert err.call_count == 1


class TestAutoRetry:
    def test_first_failure_retries_by_itself(self, app, tmp_path):
        _saved_job(tmp_path, auto_retries=0)
        with patch.object(app, "after") as after:
            retried = app._auto_retry_or_offer_continue()
        assert retried is True
        assert after.call_count == 1

    def test_second_failure_stops_and_asks(self, app, tmp_path):
        """A deterministic crash must not burn attempt after attempt."""
        _saved_job(tmp_path, auto_retries=1)
        app._pending_auto_retries = 1
        with patch.object(app, "after") as after:
            retried = app._auto_retry_or_offer_continue()
        assert retried is False
        assert after.call_count == 0

    def test_the_retry_is_recorded_so_the_budget_is_not_reset(self, app, tmp_path):
        _saved_job(tmp_path, auto_retries=0)
        with patch.object(app, "after"):
            app._auto_retry_or_offer_continue()
        assert job_state.load().auto_retries == 1

    def test_no_saved_job_means_no_retry(self, app):
        assert app._auto_retry_or_offer_continue() is False

    def test_exit_event_triggers_the_retry_path(self, app, tmp_path):
        _saved_job(tmp_path, auto_retries=0)
        app._synth_running = True
        app._event_queue.put(ProgressEvent(kind="exit", returncode=1))
        with patch.object(app, "after") as after, patch.object(app, "_fail") as fail:
            app._pump_events()
        # Retrying, so the user must NOT also get a failure dialog.
        assert fail.call_count == 0
        assert after.call_count >= 1

    def test_exit_after_the_budget_reports_the_failure(self, app, tmp_path):
        _saved_job(tmp_path, auto_retries=1)
        app._pending_auto_retries = 1
        app._synth_running = True
        app._event_queue.put(ProgressEvent(kind="exit", returncode=1))
        with patch.object(app, "after"), patch.object(app, "_fail") as fail:
            app._pump_events()
        assert fail.call_count == 1


class TestJobLifecycle:
    def test_finishing_clears_the_job(self, app, tmp_path):
        _saved_job(tmp_path)
        app._record_job_finished("done")
        assert job_state.load() is None

    def test_progress_is_recorded_on_the_fiftieth_chunk(self, app, tmp_path):
        app._job_state = _saved_job(tmp_path, total_done=0, total_chunks=4100)
        app._record_job_progress(50, 4100)
        assert job_state.load().total_done == 50

    def test_progress_does_not_write_every_chunk(self, app, tmp_path):
        """A book-length run would otherwise rewrite this file thousands
        of times for a number that only has to be roughly right."""
        app._job_state = _saved_job(tmp_path, total_done=0, total_chunks=4100)
        app._record_job_progress(51, 4100)
        assert job_state.load().total_done == 0

    def test_progress_is_safe_with_no_job(self, app):
        app._job_state = None
        app._record_job_progress(10, 100)  # must not raise
