"""Tests for src.version_manifest — pinned-version parsing + venv health probe."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src import version_manifest as vm


# ---------------------------------------------------------------------------
# pinned_versions / normalization — against the real requirements file
# ---------------------------------------------------------------------------


def test_pinned_versions_reads_real_requirements() -> None:
    pins = vm.pinned_versions()
    # The load-bearing chain must be present and exact.
    assert pins["chatterbox-tts"] == "0.1.7"
    assert pins["transformers"] == "5.2.0"
    assert pins["tokenizers"] == "0.22.2"


def test_pinned_versions_missing_file_is_empty(tmp_path: Path) -> None:
    assert vm.pinned_versions(tmp_path / "nope.txt") == {}


def test_normalize_collapses_separators() -> None:
    assert vm._normalize("huggingface_hub") == "huggingface-hub"
    assert vm._normalize("PyMuPDF") == "pymupdf"
    assert vm._normalize("resemble-perth") == "resemble-perth"


def _write_reqs(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "requirements-chatterbox.txt"
    p.write_text(body, encoding="utf-8")
    return p


def test_pinned_versions_skips_comments_and_blanks(tmp_path: Path) -> None:
    reqs = _write_reqs(
        tmp_path,
        "# header comment\n\ntransformers==5.2.0\n  # indented comment\n"
        "tokenizers==0.22.2\n",
    )
    assert vm.pinned_versions(reqs) == {
        "transformers": "5.2.0",
        "tokenizers": "0.22.2",
    }


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def test_compare_clean_when_all_match() -> None:
    pinned = {"transformers": "5.2.0", "tokenizers": "0.22.2"}
    installed = {"transformers": "5.2.0", "tokenizers": "0.22.2"}
    assert vm.compare(pinned, installed) == []


def test_compare_flags_drifted_and_missing() -> None:
    pinned = {"transformers": "5.2.0", "tokenizers": "0.22.2"}
    installed = {"transformers": "5.4.0", "tokenizers": None}
    drift = vm.compare(pinned, installed)
    by_pkg = {d.package: d for d in drift}
    assert by_pkg["transformers"].installed == "5.4.0"
    assert by_pkg["transformers"].expected == "5.2.0"
    assert by_pkg["tokenizers"].installed is None  # missing
    assert "missing" in by_pkg["tokenizers"].describe()


# ---------------------------------------------------------------------------
# parse_probe_output
# ---------------------------------------------------------------------------


def test_parse_probe_output_extracts_last_json_after_noise() -> None:
    payload = {"import_ok": True, "import_error": None, "installed": {}}
    stdout = (
        "loaded PerthNet (Implicit) at step 250,000\n"
        "some banner line\n" + json.dumps(payload) + "\n"
    )
    assert vm.parse_probe_output(stdout) == payload


def test_parse_probe_output_none_when_no_json() -> None:
    assert vm.parse_probe_output("just noise\nno json here\n") is None


def test_parse_probe_output_ignores_unrelated_json() -> None:
    # A JSON object without import_ok is not the probe payload.
    assert vm.parse_probe_output('{"something": 1}\n') is None


# ---------------------------------------------------------------------------
# probe_venv — mocked subprocess
# ---------------------------------------------------------------------------


def _completed(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["python", "-c", "..."], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_probe_venv_healthy(tmp_path: Path) -> None:
    reqs = _write_reqs(tmp_path, "transformers==5.2.0\ntokenizers==0.22.2\n")
    payload = {
        "import_ok": True,
        "import_error": None,
        "installed": {"transformers": "5.2.0", "tokenizers": "0.22.2"},
    }
    with patch.object(vm.subprocess, "run", return_value=_completed(json.dumps(payload))):
        health = vm.probe_venv("py.exe", path=reqs)
    assert health.ok is True
    assert health.drift == []
    assert health.healthy is True
    assert "healthy" in health.summary()


def test_probe_venv_detects_llamamodel_drift(tmp_path: Path) -> None:
    """The exact failure mode: import fails with the LlamaModel message and
    the probe names transformers as the drifted package."""
    reqs = _write_reqs(tmp_path, "transformers==5.2.0\ntokenizers==0.22.2\n")
    payload = {
        "import_ok": False,
        "import_error": (
            "RuntimeError: Could not import module 'LlamaModel'. "
            "Are this object's requirements defined correctly?"
        ),
        "installed": {"transformers": "5.4.0", "tokenizers": "0.22.2"},
    }
    with patch.object(vm.subprocess, "run", return_value=_completed(json.dumps(payload))):
        health = vm.probe_venv("py.exe", path=reqs)
    assert health.ok is False
    assert health.healthy is False
    assert "LlamaModel" in (health.import_error or "")
    drifted = {d.package for d in health.drift}
    assert "transformers" in drifted
    # The drift is named in the human summary.
    assert "transformers" in health.summary()


def test_probe_venv_timeout_is_probe_failed(tmp_path: Path) -> None:
    reqs = _write_reqs(tmp_path, "transformers==5.2.0\n")
    with patch.object(
        vm.subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="py", timeout=90)
    ):
        health = vm.probe_venv("py.exe", path=reqs, timeout=90)
    assert health.ok is False
    assert health.probe_failed is not None
    assert "timed out" in health.probe_failed


def test_probe_venv_no_json_is_probe_failed(tmp_path: Path) -> None:
    reqs = _write_reqs(tmp_path, "transformers==5.2.0\n")
    with patch.object(
        vm.subprocess, "run", return_value=_completed("crashed\n", returncode=1, stderr="boom")
    ):
        health = vm.probe_venv("py.exe", path=reqs)
    assert health.probe_failed is not None
    assert health.ok is False


def test_probe_venv_spawn_error_is_probe_failed(tmp_path: Path) -> None:
    reqs = _write_reqs(tmp_path, "transformers==5.2.0\n")
    with patch.object(vm.subprocess, "run", side_effect=OSError("no such file")):
        health = vm.probe_venv("py.exe", path=reqs)
    assert health.probe_failed is not None
    assert "could not run" in health.probe_failed


# ---------------------------------------------------------------------------
# build_probe_code is valid Python
# ---------------------------------------------------------------------------


def test_build_probe_code_compiles() -> None:
    code = vm.build_probe_code(["transformers", "huggingface_hub"])
    # Must be syntactically valid so the subprocess does not crash on parse.
    compile(code, "<probe>", "exec")
    assert "import_ok" in code


# ---------------------------------------------------------------------------
# is_engine_load_failure — the GUI repair-offer trigger
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "[error] Could not import module 'LlamaModel'. Are this object's requirements defined correctly?",
        "Could not import module 'LlamaModel'",
        "RuntimeError: ... LlamaModel ...",
        "The Chatterbox engine could not load. Its Python environment ... has incompatible package versions",
    ],
)
def test_is_engine_load_failure_true_for_broken_venv(message: str) -> None:
    assert vm.is_engine_load_failure(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "",
        "Not enough disk space at the output path.",
        "Subprocess failed to start: file not found",
        "User cancelled synthesis.",
    ],
)
def test_is_engine_load_failure_false_for_unrelated_errors(message: str) -> None:
    assert vm.is_engine_load_failure(message) is False
