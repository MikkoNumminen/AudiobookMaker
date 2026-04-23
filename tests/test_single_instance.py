"""Tests for src.single_instance — process-singleton guard.

Focuses on the PID-lock-file path (non-Windows code path used on Linux /
macOS and exercised directly on Windows as a belt-and-braces check on top
of the named mutex). The key invariant: two racing instances cannot both
believe they hold the lock.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from src import single_instance


@pytest.fixture(autouse=True)
def _isolated_lock_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point _acquire_lock_file at a per-test temp dir and reset globals.

    ``_acquire_lock_file`` imports ``tempfile`` lazily, so patching the
    stdlib module itself is sufficient — the function resolves
    ``tempfile.gettempdir`` at call time.
    """
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    single_instance._mutex_handle = None
    single_instance._lock_file = None
    yield
    # Clean up any lock file the test left behind.
    single_instance.release()
    single_instance._mutex_handle = None
    single_instance._lock_file = None


# ---------------------------------------------------------------------------
# Happy-path + stale-lock takeover
# ---------------------------------------------------------------------------


class TestAcquireLockFile:
    def test_first_caller_acquires(self, tmp_path: Path) -> None:
        assert single_instance._acquire_lock_file() is True
        lock = tmp_path / "audiobookmaker.lock"
        assert lock.exists()
        assert lock.read_text().strip() == str(os.getpid())

    def test_second_caller_fails_when_owner_alive(self, tmp_path: Path) -> None:
        # First instance takes the lock with the current (definitely alive) PID.
        assert single_instance._acquire_lock_file() is True

        # Simulate a second process racing: reset module globals so the call
        # looks fresh, but leave the lock file (with our PID) on disk.
        single_instance._lock_file = None
        assert single_instance._acquire_lock_file() is False

    def test_stale_lock_is_taken_over(self, tmp_path: Path) -> None:
        # Plant a lock file owned by a PID that is guaranteed dead.
        lock = tmp_path / "audiobookmaker.lock"
        lock.write_text("999999999")  # PID well above any real process

        with patch("os.kill", side_effect=ProcessLookupError):
            assert single_instance._acquire_lock_file() is True

        assert lock.read_text().strip() == str(os.getpid())

    def test_garbage_lock_is_taken_over(self, tmp_path: Path) -> None:
        # A PID file that got corrupted (non-integer content) counts as stale.
        lock = tmp_path / "audiobookmaker.lock"
        lock.write_text("not-a-pid\n")

        assert single_instance._acquire_lock_file() is True
        assert lock.read_text().strip() == str(os.getpid())

    def test_empty_lock_is_taken_over(self, tmp_path: Path) -> None:
        lock = tmp_path / "audiobookmaker.lock"
        lock.write_text("")

        assert single_instance._acquire_lock_file() is True
        assert lock.read_text().strip() == str(os.getpid())


# ---------------------------------------------------------------------------
# The TOCTOU race fix: exclusive-create atomicity
# ---------------------------------------------------------------------------


class TestAtomicCreateClosesRace:
    def test_uses_exclusive_create_mode(self, tmp_path: Path) -> None:
        """Regression guard — the fix depends on open(..., 'x').

        If someone edits _acquire_lock_file back to a write-text-without-
        exclusive-mode pattern, this test detects the regression by
        spying on the built-in open and checking that 'x' mode was used.
        """
        real_open = open
        modes_seen: list[str] = []

        def spy_open(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
            if str(file).endswith("audiobookmaker.lock"):
                modes_seen.append(mode)
            return real_open(file, mode, *args, **kwargs)

        with patch("builtins.open", side_effect=spy_open):
            assert single_instance._acquire_lock_file() is True

        assert "x" in modes_seen, (
            f"_acquire_lock_file must use exclusive-create mode; saw {modes_seen!r}"
        )

    def test_concurrent_threads_only_one_wins(self, tmp_path: Path) -> None:
        """Two threads racing on a fresh lock file: exactly one acquires.

        This exercises the core TOCTOU fix. Before the fix, both threads
        could pass the exists() check simultaneously and both write their
        PID; now open(..., "x") makes the create atomic so one thread
        gets FileExistsError and, with the current PID alive, reports
        "not first".
        """
        results: list[bool] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker() -> None:
            # Simulate a second independent "process" by clearing the
            # module-level _lock_file reference each thread sees.
            barrier.wait()
            got_it = single_instance._acquire_lock_file()
            with results_lock:
                results.append(got_it)

        # Kick both threads off as close to simultaneously as possible.
        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one should have acquired. The other should have seen
        # an existing lock whose PID (our own) is alive, so it reports
        # False ("another instance already holds it").
        assert sorted(results) == [False, True], (
            f"Expected exactly one winner, got {results!r}"
        )

        lock = tmp_path / "audiobookmaker.lock"
        assert lock.exists()
        assert lock.read_text().strip() == str(os.getpid())

    def test_race_on_stale_takeover_has_exclusive_winner(
        self, tmp_path: Path
    ) -> None:
        """Two threads both see a stale lock and race to take it over.

        The fix guarantees that even the takeover retry uses open(x), so
        the second racer hits FileExistsError instead of silently
        clobbering the winner's PID.
        """
        lock = tmp_path / "audiobookmaker.lock"
        lock.write_text("999999999")  # dead PID

        results: list[bool] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker() -> None:
            barrier.wait()
            with patch("os.kill", side_effect=ProcessLookupError):
                got_it = single_instance._acquire_lock_file()
            with results_lock:
                results.append(got_it)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one thread wins the takeover.
        assert sorted(results) == [False, True], (
            f"Expected exactly one takeover winner, got {results!r}"
        )
        assert lock.read_text().strip() == str(os.getpid())


# ---------------------------------------------------------------------------
# release()
# ---------------------------------------------------------------------------


class TestRelease:
    def test_release_removes_lock_file(self, tmp_path: Path) -> None:
        assert single_instance._acquire_lock_file() is True
        lock = tmp_path / "audiobookmaker.lock"
        assert lock.exists()

        single_instance.release()
        assert not lock.exists()

    def test_release_is_idempotent(self, tmp_path: Path) -> None:
        assert single_instance._acquire_lock_file() is True
        single_instance.release()
        # Second release must not raise even though the file is already gone.
        single_instance.release()
