"""Pinned-version manifest + Chatterbox venv health probe.

The Chatterbox engine venv must match an exact, validated set of package
versions (see ``installer/requirements-chatterbox.txt``). A drifted venv —
most often a ``transformers`` newer than ``chatterbox-tts==0.1.7`` targets —
fails to load with the cryptic "Could not import module 'LlamaModel'" error,
and only at synthesis time.

This module is the runtime guard against that:

* :func:`pinned_versions` reads the canonical requirements file (the single
  source of truth — no second hardcoded list to drift from).
* :func:`probe_venv` spawns the venv's own Python and loads the engine the
  same way synthesis does, reporting whether it imports AND which installed
  package versions drifted from the pins.

The CLI ``doctor`` command and the GUI startup check call :func:`probe_venv`
so a broken venv surfaces before the user clicks Convert, with an actionable
"repair the engine" message instead of a raw traceback.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

_REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_FILE = _REPO_ROOT / "installer" / "requirements-chatterbox.txt"

# Wait budget for the import-health subprocess. Loading the chatterbox class
# pulls torch, so allow a cold-import budget but stay bounded so a hung probe
# cannot wedge the caller (doctor / GUI startup).
PROBE_TIMEOUT_S = 90


def _normalize(name: str) -> str:
    """Canonical pip project name: lowercase, separators collapsed to '-'."""
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _requirement_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def pinned_versions(path: Path = REQUIREMENTS_FILE) -> dict[str, str]:
    """Return ``{normalized_name: version}`` from the requirements file."""
    out: dict[str, str] = {}
    for line in _requirement_lines(path):
        name, sep, version = line.partition("==")
        if sep:
            out[_normalize(name)] = version.strip()
    return out


def _raw_requirement_names(path: Path = REQUIREMENTS_FILE) -> list[str]:
    """Return the requirement names as written (for metadata lookup)."""
    names: list[str] = []
    for line in _requirement_lines(path):
        name = line.partition("==")[0].strip()
        if name:
            names.append(name)
    return names


@dataclass
class Drift:
    """One package whose installed version differs from the pin."""

    package: str
    expected: str
    installed: Optional[str]  # None = not installed at all

    def describe(self) -> str:
        actual = self.installed if self.installed is not None else "missing"
        return f"{self.package}: {actual} installed, {self.expected} expected"


@dataclass
class VenvHealth:
    """Result of probing a Chatterbox venv."""

    ok: bool = False
    """True iff torch + chatterbox imported successfully."""

    import_error: Optional[str] = None
    """The raw import failure (e.g. the LlamaModel message), if any."""

    drift: list[Drift] = field(default_factory=list)
    """Packages whose installed version differs from the pinned set."""

    probe_failed: Optional[str] = None
    """Set when the probe itself could not run (timeout, no JSON, etc.)."""

    @property
    def healthy(self) -> bool:
        return self.ok and not self.drift and self.probe_failed is None

    def summary(self) -> str:
        if self.probe_failed:
            return f"Engine health probe could not run: {self.probe_failed}"
        if not self.ok:
            base = "Chatterbox engine venv is broken — it could not load."
            if self.drift:
                base += " Drifted packages: " + "; ".join(
                    d.describe() for d in self.drift
                )
            elif self.import_error:
                base += f" ({self.import_error})"
            return base
        if self.drift:
            return "Chatterbox engine loads but package versions drifted: " + \
                "; ".join(d.describe() for d in self.drift)
        return "Chatterbox engine venv is healthy."


# Substrings that mark an error message as a broken/drifted engine venv. The
# transformers _LazyModule wrapper hides the real failure behind "Could not
# import module 'LlamaModel'"; the synthesis runner adds its own wording. Lives
# here (not in the GUI) so it is importable without Tk and unit-testable.
_ENGINE_LOAD_FAILURE_SIGNATURES = (
    "could not import module",        # transformers _LazyModule wrapper
    "llamamodel",
    "incompatible package versions",  # the runner's broken-engine guidance
)


def is_engine_load_failure(message: str) -> bool:
    """True if an error message looks like a broken/drifted Chatterbox venv.

    Lets the GUI offer a one-click repair (open Install engines) instead of a
    dead-end traceback dialog when synthesis fails on a venv that drifted.
    """
    if not message:
        return False
    low = message.lower()
    return any(sig in low for sig in _ENGINE_LOAD_FAILURE_SIGNATURES)


def compare(
    pinned: dict[str, str], installed: dict[str, Optional[str]]
) -> list[Drift]:
    """Return the pinned packages whose installed version differs."""
    drift: list[Drift] = []
    for name, expected in sorted(pinned.items()):
        actual = installed.get(name)
        if actual != expected:
            drift.append(Drift(package=name, expected=expected, installed=actual))
    return drift


# Probe run inside the venv's interpreter. Reports installed versions (via
# importlib.metadata, which works even when the package fails to import) plus
# whether the engine class loads at all — the same import the smoke test and
# the synthesis runner do.
_PROBE_TEMPLATE = r"""
import importlib.metadata as _m, json as _j
def _ver(_n):
    for _c in (_n, _n.replace("_", "-"), _n.replace("-", "_")):
        try:
            return _m.version(_c)
        except Exception:
            pass
    return None
_names = {names!r}
_out = {{"import_ok": False, "import_error": None, "installed": {{}}}}
for _n in _names:
    _out["installed"][_n] = _ver(_n)
try:
    import torch  # noqa
    import torchaudio  # noqa
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # noqa
    _out["import_ok"] = True
except Exception as _e:
    _out["import_error"] = "{{}}: {{}}".format(type(_e).__name__, _e)
print(_j.dumps(_out))
"""


def build_probe_code(names: list[str]) -> str:
    return _PROBE_TEMPLATE.format(names=names)


def parse_probe_output(stdout: str) -> Optional[dict]:
    """Return the probe's JSON payload from stdout, or None if absent.

    The probe prints one JSON object as its last line; torch/chatterbox may
    emit banner noise before it, so scan from the bottom for the first line
    that parses as a dict with the ``import_ok`` key.
    """
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict) and "import_ok" in payload:
            return payload
    return None


def probe_venv(
    venv_python: Union[str, Path],
    *,
    timeout: int = PROBE_TIMEOUT_S,
    path: Path = REQUIREMENTS_FILE,
) -> VenvHealth:
    """Probe a Chatterbox venv for import health + version drift.

    Spawns ``venv_python -c <probe>`` and classifies the result. Never
    raises — every failure mode is folded into the returned VenvHealth.
    """
    pinned = pinned_versions(path)
    code = build_probe_code(_raw_requirement_names(path))
    try:
        proc = subprocess.run(
            [str(venv_python), "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return VenvHealth(probe_failed=f"probe timed out after {timeout}s")
    except Exception as exc:  # noqa: BLE001 — surface any spawn failure
        return VenvHealth(probe_failed=f"probe could not run: {exc}")

    payload = parse_probe_output(proc.stdout)
    if payload is None:
        detail = (proc.stderr or proc.stdout or "").strip()[-400:]
        return VenvHealth(
            probe_failed=f"probe returned no JSON (exit {proc.returncode}): {detail}"
        )

    installed_raw = payload.get("installed", {}) or {}
    installed = {_normalize(k): v for k, v in installed_raw.items()}
    return VenvHealth(
        ok=bool(payload.get("import_ok")),
        import_error=payload.get("import_error"),
        drift=compare(pinned, installed),
    )
