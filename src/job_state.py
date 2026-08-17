"""Remember the last conversion so a failed one can be picked up again.

A book-length conversion is a many-hour job. When one dies partway through,
the synthesized chunks are all still on disk — the runner caches every chunk
as a WAV and skips the healthy ones on a re-run — so continuing costs minutes
instead of hours. Before this module existed, none of that was reachable from
the GUI: the user had to know to re-select the same source file AND the same
output folder, and nothing told them either of those mattered. A tester lost a
14-hour run this way and started over with a different file.

So the job is written to disk when it starts, updated as it progresses, and
cleared when it finishes. Anything left behind in a non-final state is a job
that can be continued.

The saved state is deliberately the INPUT to a run, not a description of one:
continuing is re-running with the same arguments and letting the runner's chunk
cache do the work. That is why ``out_dir`` is saved even though the GUI could
re-derive it — a re-derived path that differs by one character silently misses
the entire cache, which is the failure this module exists to prevent.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

JOB_DIR = Path.home() / ".audiobookmaker"
JOB_FILE = JOB_DIR / "last_job.json"

# A job in one of these states has nothing left to do.
_FINAL_STATES = frozenset({"done", "cancelled"})

# How many times the app may silently resume a failed job before it stops and
# asks. One is deliberate: it recovers a transient death overnight without the
# user present, while a deterministic failure (the kind that dies identically
# every time) surfaces after one wasted attempt rather than several.
MAX_AUTO_RETRIES = 1


@dataclass
class JobState:
    """Everything needed to start the same conversion again."""

    # --- what to convert -------------------------------------------------
    input_mode: str = "pdf"
    pdf_path: Optional[str] = None
    input_text: Optional[str] = None

    # --- how to convert it -----------------------------------------------
    language: str = ""
    voice_pack_path: Optional[str] = None
    reference_audio: Optional[str] = None
    chunk_chars: int = 300
    engine_id: str = ""

    # --- where it goes ---------------------------------------------------
    # Saved rather than re-derived. The runner keys its chunk cache off this
    # directory, so a path that differs at all is a full restart.
    output_path_hint: Optional[str] = None
    out_dir: Optional[str] = None

    # --- lifecycle -------------------------------------------------------
    status: str = "running"
    auto_retries: int = 0
    total_done: int = 0
    total_chunks: int = 0
    updated_at: str = field(default_factory=lambda: _now())

    def is_resumable(self) -> bool:
        """True when this job has work left and enough state to restart it.

        A job still marked ``running`` counts: that means the process died
        without ever reporting an outcome, which is precisely the case worth
        recovering. It is also what the app finds after a crash or a power cut.
        """
        if self.status in _FINAL_STATES:
            return False
        if self.input_mode == "pdf":
            if not self.pdf_path or not Path(self.pdf_path).is_file():
                return False
        elif not (self.input_text or "").strip():
            return False
        return True

    def may_auto_retry(self) -> bool:
        """True when the app should silently resume rather than ask."""
        return self.is_resumable() and self.auto_retries < MAX_AUTO_RETRIES

    def progress_fraction(self) -> float:
        """How much was already done, 0.0-1.0. Zero when unknown."""
        if self.total_chunks <= 0:
            return 0.0
        return max(0.0, min(1.0, self.total_done / self.total_chunks))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(state: JobState, path: Optional[Path] = None) -> None:
    """Write the job state atomically.

    Atomic because this is written while a conversion is running: a torn file
    from a mid-write crash would be indistinguishable from no job at all, and
    losing the pointer to the cache is exactly the outcome to avoid. A failure
    to save is logged and swallowed — it must never take down a running
    conversion.
    """
    path = path or JOB_FILE
    state.updated_at = _now()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(asdict(state), fh, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            # os.replace can fail on Windows if something holds the target.
            # Clean up the temp file so the config dir does not fill with them.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:
        logger.warning("could not save job state to %s", path, exc_info=True)


def load(path: Optional[Path] = None) -> Optional[JobState]:
    """Read the saved job, or None if there is none or it is unreadable.

    Unknown keys are ignored so a file written by a newer version does not
    crash an older one, and a truncated or hand-edited file is treated as
    absent rather than raising.
    """
    path = path or JOB_FILE
    try:
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        known = {f for f in JobState.__dataclass_fields__}
        return JobState(**{k: v for k, v in raw.items() if k in known})
    except Exception:
        logger.warning("could not load job state from %s", path, exc_info=True)
        return None


def clear(path: Optional[Path] = None) -> None:
    """Forget the saved job. Called when one finishes or is abandoned."""
    path = path or JOB_FILE
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        logger.warning("could not clear job state at %s", path, exc_info=True)


def load_resumable(path: Optional[Path] = None) -> Optional[JobState]:
    """Return the saved job only when it is worth offering to continue."""
    state = load(path or JOB_FILE)
    if state is None or not state.is_resumable():
        return None
    return state
