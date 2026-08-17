"""Tests for the saved-job state that backs the Continue button.

A conversion that dies has all its synthesized chunks still on disk. Whether
the user can get them back comes down to re-running with the same arguments,
so this module's whole job is to not lose those arguments and to not lie about
whether a job is worth continuing.
"""
from __future__ import annotations

import json

import pytest

from src import job_state
from src.job_state import JobState


@pytest.fixture
def job_file(tmp_path):
    return tmp_path / "last_job.json"


def _pdf_job(tmp_path, **overrides) -> JobState:
    book = tmp_path / "book.pdf"
    book.write_bytes(b"%PDF-1.4 fake")
    fields = dict(
        input_mode="pdf",
        pdf_path=str(book),
        language="fi",
        out_dir=str(tmp_path / "out"),
        total_done=2229,
        total_chunks=4100,
    )
    fields.update(overrides)
    return JobState(**fields)


class TestRoundTrip:
    def test_save_then_load_preserves_every_field(self, tmp_path, job_file):
        original = _pdf_job(
            tmp_path,
            voice_pack_path="C:/packs/isoaiti",
            reference_audio="C:/refs/clip.wav",
            chunk_chars=250,
            engine_id="chatterbox_grandmom",
            output_path_hint="D:/books/out.mp3",
        )
        job_state.save(original, job_file)
        loaded = job_state.load(job_file)

        assert loaded is not None
        for name in JobState.__dataclass_fields__:
            if name == "updated_at":
                continue
            assert getattr(loaded, name) == getattr(original, name), name

    def test_out_dir_survives(self, tmp_path, job_file):
        """The cache is keyed off this path. Re-deriving it is how a resume
        silently becomes a restart."""
        job_state.save(_pdf_job(tmp_path), job_file)
        assert job_state.load(job_file).out_dir == str(tmp_path / "out")

    def test_save_is_atomic(self, tmp_path, job_file):
        """No .tmp litter left behind, and the target is complete."""
        job_state.save(_pdf_job(tmp_path), job_file)
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []
        assert json.loads(job_file.read_text(encoding="utf-8"))["status"] == "running"

    def test_load_missing_file_is_none(self, job_file):
        assert job_state.load(job_file) is None

    def test_clear_removes_it(self, tmp_path, job_file):
        job_state.save(_pdf_job(tmp_path), job_file)
        job_state.clear(job_file)
        assert job_state.load(job_file) is None

    def test_clear_is_safe_when_absent(self, job_file):
        job_state.clear(job_file)  # must not raise


class TestCorruptFilesAreTreatedAsAbsent:
    @pytest.mark.parametrize(
        "content",
        ['{"status": "run', "", "null", "[1, 2, 3]", "not json at all"],
    )
    def test_unreadable_file_is_none_not_an_exception(self, job_file, content):
        """A torn file from a mid-write crash must not crash startup."""
        job_file.write_text(content, encoding="utf-8")
        assert job_state.load(job_file) is None

    def test_unknown_keys_are_ignored(self, tmp_path, job_file):
        """A file written by a newer version must not break an older one."""
        job_file.write_text(
            json.dumps({"status": "failed", "pdf_path": "x", "from_the_future": 1}),
            encoding="utf-8",
        )
        loaded = job_state.load(job_file)
        assert loaded is not None
        assert loaded.status == "failed"


class TestIsResumable:
    def test_a_failed_pdf_job_is_resumable(self, tmp_path):
        assert _pdf_job(tmp_path, status="failed").is_resumable()

    def test_a_still_running_job_is_resumable(self, tmp_path):
        """`running` means the process died without reporting an outcome.

        That is exactly the case worth recovering, and it is what the app
        finds after a crash or a power cut.
        """
        assert _pdf_job(tmp_path, status="running").is_resumable()

    @pytest.mark.parametrize("status", ["done", "cancelled"])
    def test_finished_jobs_are_not_resumable(self, tmp_path, status):
        assert not _pdf_job(tmp_path, status=status).is_resumable()

    def test_a_vanished_source_file_is_not_resumable(self, tmp_path):
        """Offering Continue for a file the user moved or deleted would
        produce a second, more confusing failure."""
        job = _pdf_job(tmp_path, status="failed")
        job.pdf_path = str(tmp_path / "gone.pdf")
        assert not job.is_resumable()

    def test_text_job_needs_its_text(self, tmp_path):
        assert not JobState(input_mode="text", input_text="   ").is_resumable()
        assert JobState(input_mode="text", input_text="hello").is_resumable()

    def test_load_resumable_filters(self, tmp_path, job_file):
        job_state.save(_pdf_job(tmp_path, status="done"), job_file)
        assert job_state.load_resumable(job_file) is None
        job_state.save(_pdf_job(tmp_path, status="failed"), job_file)
        assert job_state.load_resumable(job_file) is not None


class TestAutoRetryBudget:
    def test_first_failure_may_retry(self, tmp_path):
        assert _pdf_job(tmp_path, status="failed", auto_retries=0).may_auto_retry()

    def test_budget_is_one_then_it_asks(self, tmp_path):
        """A deterministic failure must surface after one wasted attempt,
        not burn several identical ones."""
        assert not _pdf_job(
            tmp_path, status="failed", auto_retries=1
        ).may_auto_retry()

    def test_a_finished_job_never_auto_retries(self, tmp_path):
        assert not _pdf_job(tmp_path, status="done", auto_retries=0).may_auto_retry()

    def test_budget_constant_is_one(self):
        assert job_state.MAX_AUTO_RETRIES == 1


class TestProgressFraction:
    def test_reports_how_much_was_done(self, tmp_path):
        assert _pdf_job(tmp_path).progress_fraction() == pytest.approx(2229 / 4100)

    def test_unknown_total_is_zero_not_a_crash(self):
        assert JobState(total_done=5, total_chunks=0).progress_fraction() == 0.0

    def test_clamped_to_one(self):
        assert JobState(total_done=99, total_chunks=10).progress_fraction() == 1.0


class TestSaveNeverBreaksARun:
    def test_an_unwritable_path_is_logged_not_raised(self, tmp_path):
        """Saving happens while a conversion runs; it must never take one down."""
        blocked = tmp_path / "a-file-not-a-dir" / "last_job.json"
        (tmp_path / "a-file-not-a-dir").write_text("in the way", encoding="utf-8")
        job_state.save(JobState(), blocked)  # must not raise
