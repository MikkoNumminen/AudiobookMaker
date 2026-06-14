"""Tests for src/process_lock.py — the one-heavy-ML-subprocess-at-a-time lock.

The load-bearing properties are validated with a REAL child process (a
cross-process lock can't be honestly tested in-process): a live holder is
refused, and the lock is released automatically when that holder dies — the
behaviour that lets a crashed run never wedge the machine.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.process_lock import (
    LockHeld,
    _lock_path,
    single_ml_subprocess_lock,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Child that acquires the lock, announces it, then blocks holding it.
_HOLDER = (
    "import sys, time; sys.path.insert(0, sys.argv[1]); "
    "from src.process_lock import single_ml_subprocess_lock; "
    "single_ml_subprocess_lock(sys.argv[2]).acquire(); "
    "print('ACQUIRED', flush=True); time.sleep(30)"
)


@pytest.fixture
def lock_name(request: pytest.FixtureRequest) -> str:
    name = f"abm-test-{request.node.name}-{os.getpid()}"
    yield name
    try:
        _lock_path(name).unlink()
    except OSError:
        pass


def test_live_holder_is_refused_then_released_on_death(lock_name: str) -> None:
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(_REPO_ROOT), lock_name],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ACQUIRED"
        # Mutual exclusion: cannot acquire while the child holds it.
        with pytest.raises(LockHeld) as excinfo:
            single_ml_subprocess_lock(lock_name).acquire()
        # owner_pid is the diagnostic PID read from the file; -1 if it couldn't
        # be read (tolerated — the LockHeld itself is the load-bearing assertion).
        assert excinfo.value.owner_pid in (holder.pid, -1)
    finally:
        holder.kill()
        holder.wait()

    # The child is dead — the kernel released the lock — so we can acquire it.
    # (No PID reclaim; no stale-lock wedge.)
    lock = single_ml_subprocess_lock(lock_name)
    lock.acquire()
    lock.release()


def test_acquire_release_allows_reacquire(lock_name: str) -> None:
    lock = single_ml_subprocess_lock(lock_name)
    lock.acquire()
    lock.release()
    again = single_ml_subprocess_lock(lock_name)
    again.acquire()  # must succeed after a clean release
    again.release()


def test_context_manager_releases(lock_name: str) -> None:
    with single_ml_subprocess_lock(lock_name):
        pass
    # released — a fresh acquire succeeds
    lock = single_ml_subprocess_lock(lock_name)
    lock.acquire()
    lock.release()


def test_leftover_unlocked_file_does_not_wedge(lock_name: str) -> None:
    # A stray lock file from a previous run holds no kernel lock, so acquiring
    # must succeed (junk content included — the PID body is diagnostic only).
    _lock_path(lock_name).write_text("99999 not-a-pid", encoding="utf-8")
    lock = single_ml_subprocess_lock(lock_name)
    lock.acquire()
    lock.release()


def test_release_is_idempotent(lock_name: str) -> None:
    lock = single_ml_subprocess_lock(lock_name)
    lock.acquire()
    lock.release()
    lock.release()  # no error on a second release
