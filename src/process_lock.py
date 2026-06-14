"""Cross-process exclusive lock for heavy ML subprocesses (GPU discipline).

CLAUDE.md ("Resource discipline — never run two heavy ML pipelines at once"):
only ONE voice-pack analyze / synthesize / clone / train subprocess may run
per machine. Each loads faster-whisper + pyannote + Chatterbox (~6 GB VRAM +
~2 GB RAM), and two at once swap-thrash the GPU allocator into system RAM and
freeze the OS (observed 2026-05-10). That rule was documentation-only; this
module mechanizes it.

The lock is an OS-level advisory lock (`fcntl.flock` on POSIX, `msvcrt.locking`
on Windows) held on a file under the temp dir. Using the kernel's lock — rather
than a PID written into a file — buys two things a hand-rolled PID file cannot:

  * **Atomic mutual exclusion.** Exactly one process can hold the lock; a
    second `acquire()` is refused with `LockHeld`. There is no read-then-write
    window for two processes to both "reclaim" a stale file and proceed.
  * **Automatic release on death.** The kernel drops the lock when the holder's
    file descriptor closes — including when the process crashes — so a dead run
    can never wedge the machine. No PID liveness probing, no stale recovery.

Use the context manager so the lock is always released:

    from src.process_lock import single_ml_subprocess_lock, LockHeld

    try:
        with single_ml_subprocess_lock():
            run_pipeline()
    except LockHeld as exc:
        print(exc); return 1
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

# One shared name across analyze / train / synthesize / clone so any two of
# them are mutually exclusive — the constraint is one heavy GPU process total,
# not one-per-kind.
DEFAULT_LOCK_NAME = "audiobookmaker-ml-subprocess"


class LockHeld(RuntimeError):
    """Another process already holds the ML-subprocess lock."""

    def __init__(self, name: str, owner_pid: int) -> None:
        self.name = name
        self.owner_pid = owner_pid
        owner = f"PID {owner_pid}" if owner_pid and owner_pid > 0 else "another process"
        super().__init__(
            f"a heavy ML subprocess ({owner}) is already running. Only one "
            f"voice-pack analyze/synthesize/clone/train may run at a time — "
            f"two would swap-thrash the GPU and freeze the machine (CLAUDE.md "
            f"resource discipline). Wait for it to finish or stop it first."
        )


def _lock_path(name: str) -> Path:
    return Path(tempfile.gettempdir()) / f"{name}.lock"


# Windows msvcrt locks are MANDATORY (a locked byte range can't even be read by
# another process), unlike POSIX flock which is advisory. So on Windows we lock
# a single byte far past any real data and keep the PID at offset 0 unlocked —
# that way a contending process can still read the owner PID for the diagnostic
# message. The offset itself never gets written, so the file stays a few bytes.
_WIN_LOCK_OFFSET = 0x4000_0000  # 1 GiB


def _try_lock(fd: int) -> bool:
    """Non-blocking exclusive OS lock on ``fd``. True if acquired, False if
    another process holds it."""
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        import msvcrt

        try:
            os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        import msvcrt

        try:
            os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


class _MlSubprocessLock:
    def __init__(self, name: str) -> None:
        self._name = name
        self._path = _lock_path(name)
        self._fd: Optional[int] = None

    def acquire(self) -> "_MlSubprocessLock":
        flags = os.O_RDWR | os.O_CREAT
        # Refuse to follow a pre-planted symlink at the predictable lock path
        # (a write through it could clobber an arbitrary file on a shared tmp).
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self._path, flags, 0o644)
        except OSError as exc:
            # Symlink (ELOOP under O_NOFOLLOW) or a permission error on another
            # user's lockfile — treat as held/hostile and refuse (fail safe:
            # refusing a run is recoverable; freezing the box is not).
            raise LockHeld(self._name, -1) from exc

        if not _try_lock(fd):
            owner = self._read_owner(fd)
            os.close(fd)
            raise LockHeld(self._name, owner)

        # We hold the kernel lock until this fd closes (on release or on death).
        # Record our PID in the file purely for the diagnostic message.
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode("ascii"))
        except OSError:
            pass
        self._fd = fd
        return self

    def _read_owner(self, fd: int) -> int:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            data = os.read(fd, 64).decode("ascii", "ignore").strip()
        except OSError:
            return -1
        try:
            return int(data)
        except ValueError:
            return -1

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        _unlock(fd)
        try:
            os.close(fd)
        except OSError:
            pass
        # Intentionally do NOT unlink the file. The flock — released on close —
        # is the lock, not the file's existence; leaving the (now-unlocked)
        # file avoids an unlink/reopen race where two processes could end up
        # locking two different inodes at the same path. A stray empty lock
        # file under the temp dir is harmless.

    def __enter__(self) -> "_MlSubprocessLock":
        return self.acquire()

    def __exit__(self, *exc_info: object) -> bool:
        self.release()
        return False


def single_ml_subprocess_lock(name: str = DEFAULT_LOCK_NAME) -> _MlSubprocessLock:
    """A context-manager lock enforcing one heavy ML subprocess at a time."""
    return _MlSubprocessLock(name)
