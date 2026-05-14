"""Tests for scripts/render_cli_help.py."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Repo root and script path
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "render_cli_help.py"
_CLI_MD = _REPO_ROOT / "docs" / "CLI.md"

_EXPECTED_SUBCOMMANDS = [
    "convert",
    "sample",
    "preview",
    "voices",
    "engines",
    "packs",
    "config",
    "update",
    "doctor",
]

_BEGIN_MARKER = "<!-- BEGIN_GENERATED_REFERENCE -->"
_END_MARKER = "<!-- END_GENERATED_REFERENCE -->"


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run the render script with the given extra args."""
    return subprocess.run(
        [sys.executable, str(_SCRIPT)] + args,
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Basic import / invocation
# ---------------------------------------------------------------------------


def test_script_exists():
    assert _SCRIPT.exists(), f"Script not found: {_SCRIPT}"


def test_stdout_mode_runs_without_error():
    """--stdout must exit 0 and produce non-empty output."""
    result = _run(["--stdout"])
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert result.stdout.strip(), "stdout was empty"


# ---------------------------------------------------------------------------
# --stdout content
# ---------------------------------------------------------------------------


def test_stdout_contains_all_subcommands():
    """The rendered block must mention every subcommand."""
    result = _run(["--stdout"])
    assert result.returncode == 0, f"stderr: {result.stderr}"
    output = result.stdout
    missing = [cmd for cmd in _EXPECTED_SUBCOMMANDS if cmd not in output]
    assert not missing, f"Missing subcommands in --stdout output: {missing}"


def test_stdout_contains_markdown_headings():
    """The rendered block must include ### headings."""
    result = _run(["--stdout"])
    assert result.returncode == 0
    assert "###" in result.stdout


def test_stdout_contains_flag_table():
    """The rendered block must include at least one markdown flag table."""
    result = _run(["--stdout"])
    assert result.returncode == 0
    # A flag table row looks like "| `--..."
    assert "| `--" in result.stdout


# ---------------------------------------------------------------------------
# --check with real docs/CLI.md
# ---------------------------------------------------------------------------


def test_check_passes_after_render(tmp_path: Path):
    """--check exits 0 when docs/CLI.md already contains the current output."""
    # Copy CLI.md to a temp location, run the renderer against it, then --check.
    tmp_doc = tmp_path / "CLI.md"
    shutil.copy(_CLI_MD, tmp_doc)

    # First, render into the temp copy so it is up to date.
    result_render = _run(["--doc", str(tmp_doc)])
    assert result_render.returncode == 0, f"render failed: {result_render.stderr}"

    # Now --check should pass.
    result_check = _run(["--check", "--doc", str(tmp_doc)])
    assert result_check.returncode == 0, (
        f"--check returned non-zero after rendering:\n"
        f"stdout: {result_check.stdout}\nstderr: {result_check.stderr}"
    )


def test_check_fails_when_block_is_stale(tmp_path: Path):
    """--check exits 1 when the generated block is outdated."""
    tmp_doc = tmp_path / "CLI.md"
    # Write a file with markers but stale content between them.
    original = _CLI_MD.read_text(encoding="utf-8")

    # Inject obviously stale content between the markers.
    begin_idx = original.find(_BEGIN_MARKER)
    end_idx = original.find(_END_MARKER)
    assert begin_idx != -1 and end_idx != -1, "Markers not found in docs/CLI.md"

    stale_content = (
        original[: begin_idx + len(_BEGIN_MARKER)]
        + "\nThis content is deliberately stale and will not match.\n"
        + original[end_idx:]
    )
    tmp_doc.write_text(stale_content, encoding="utf-8")

    result = _run(["--check", "--doc", str(tmp_doc)])
    assert result.returncode == 1, (
        f"--check should have returned 1 for stale content, got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# --check with missing markers
# ---------------------------------------------------------------------------


def test_check_fails_when_markers_absent(tmp_path: Path):
    """--check exits 1 with a clear error when the marker block is missing."""
    tmp_doc = tmp_path / "CLI.md"
    tmp_doc.write_text("# CLI\n\nNo markers here.\n", encoding="utf-8")

    result = _run(["--check", "--doc", str(tmp_doc)])
    assert result.returncode == 1
    # The error message must tell the user what to add.
    combined = result.stdout + result.stderr
    assert "BEGIN_GENERATED_REFERENCE" in combined, (
        f"Expected marker name in error message, got:\n{combined}"
    )


# ---------------------------------------------------------------------------
# In-place rewrite
# ---------------------------------------------------------------------------


def test_inplace_rewrite_is_idempotent(tmp_path: Path):
    """Running the renderer twice should not change the file the second time."""
    tmp_doc = tmp_path / "CLI.md"
    shutil.copy(_CLI_MD, tmp_doc)

    result1 = _run(["--doc", str(tmp_doc)])
    assert result1.returncode == 0, f"First run failed: {result1.stderr}"
    content_after_first = tmp_doc.read_text(encoding="utf-8")

    result2 = _run(["--doc", str(tmp_doc)])
    assert result2.returncode == 0, f"Second run failed: {result2.stderr}"
    content_after_second = tmp_doc.read_text(encoding="utf-8")

    assert content_after_first == content_after_second, (
        "Renderer is not idempotent — file content changed on the second run."
    )


def test_inplace_does_not_mutate_outside_markers(tmp_path: Path):
    """Content outside the marker block must not be touched."""
    tmp_doc = tmp_path / "CLI.md"
    shutil.copy(_CLI_MD, tmp_doc)
    original = tmp_doc.read_text(encoding="utf-8")

    result = _run(["--doc", str(tmp_doc)])
    assert result.returncode == 0

    updated = tmp_doc.read_text(encoding="utf-8")
    begin_idx_orig = original.find(_BEGIN_MARKER)
    end_idx_orig = original.find(_END_MARKER) + len(_END_MARKER)
    begin_idx_new = updated.find(_BEGIN_MARKER)
    end_idx_new = updated.find(_END_MARKER) + len(_END_MARKER)

    # Text before and after the markers should be identical.
    assert original[:begin_idx_orig] == updated[:begin_idx_new]
    assert original[end_idx_orig:] == updated[end_idx_new:]
