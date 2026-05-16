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


# ---------------------------------------------------------------------------
# _leaf_parsers — direct unit tests on the dedup + alias-attribution
# ---------------------------------------------------------------------------
#
# These exercise the helper directly (importing it) so a future
# refactor can't silently emit duplicate sections per alias or lose
# alias attribution from the canonical entry's tuple.  The other tests
# in this file only exercise the renderer's CLI surface; they would
# pass on a broken implementation that swallows aliases.


# Make `scripts/` importable for the in-process tests below.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import argparse as _argparse  # noqa: E402

from scripts.render_cli_help import _leaf_parsers  # noqa: E402


def _root_with(*names_and_aliases: tuple[str, list[str]]):
    """Build a tiny root parser with one subcommand per entry."""
    root = _argparse.ArgumentParser(prog="x")
    sub = root.add_subparsers(dest="cmd")
    for name, aliases in names_and_aliases:
        sub.add_parser(name, aliases=aliases)
    return root


class TestLeafParsersAliases:
    """`_leaf_parsers` collapses alias keys into the canonical entry
    and returns the alias names in the third tuple element."""

    def test_single_subcommand_no_alias_returns_one_leaf(self):
        root = _root_with(("foo", []))
        leaves = _leaf_parsers(root, [])
        assert len(leaves) == 1
        crumb, _, aliases = leaves[0]
        assert crumb == ["foo"]
        assert aliases == []

    def test_subcommand_with_one_alias_yields_one_leaf(self):
        """`add_parser("convert", aliases=["c"])` must yield ONE entry,
        not two — argparse stores both keys in `choices` but they point
        at the same parser, so the renderer must dedupe by identity."""
        root = _root_with(("convert", ["c"]))
        leaves = _leaf_parsers(root, [])
        assert len(leaves) == 1, (
            f"expected 1 leaf, got {len(leaves)}: "
            f"{[c for c, _, _ in leaves]}"
        )
        crumb, _, aliases = leaves[0]
        assert crumb == ["convert"]
        assert aliases == ["c"]

    def test_multiple_aliases_preserved_in_insertion_order(self):
        root = _root_with(("foo", ["f", "fo", "foof"]))
        (crumb, _, aliases), = _leaf_parsers(root, [])
        assert crumb == ["foo"]
        assert aliases == ["f", "fo", "foof"]

    def test_each_subcommand_keeps_its_own_aliases(self):
        root = _root_with(
            ("convert", ["c"]),
            ("sample", ["s"]),
            ("preview", ["p"]),
        )
        leaves = _leaf_parsers(root, [])
        by_canonical = {crumb[0]: aliases for crumb, _, aliases in leaves}
        assert by_canonical == {
            "convert": ["c"],
            "sample": ["s"],
            "preview": ["p"],
        }


class TestLeafParsersNested:
    """Subcommands with their own sub-parsers are recursed into.
    The parent isn't emitted as a leaf; its children are."""

    def test_nested_subcommands_emit_per_child(self):
        root = _argparse.ArgumentParser(prog="x")
        sub = root.add_subparsers(dest="cmd")
        parent = sub.add_parser("engines")
        parent_sub = parent.add_subparsers(dest="esub")
        parent_sub.add_parser("list")
        parent_sub.add_parser("install")
        parent_sub.add_parser("remove")

        leaves = _leaf_parsers(root, [])
        breadcrumbs = sorted(tuple(crumb) for crumb, _, _ in leaves)
        assert breadcrumbs == [
            ("engines", "install"),
            ("engines", "list"),
            ("engines", "remove"),
        ]
        # No aliases anywhere in this tree.
        assert all(aliases == [] for _, _, aliases in leaves)

    def test_parent_aliases_do_not_propagate_to_nested_leaves(self):
        """Known limitation: aliases declared on a non-leaf parent are
        NOT pushed down to its leaf children. The parent's aliases are
        simply lost from the doc surface for the children. Pinned here
        so a future refactor that "fixes" this is intentional."""
        root = _argparse.ArgumentParser(prog="x")
        sub = root.add_subparsers(dest="cmd")
        parent = sub.add_parser("engines", aliases=["e"])
        parent_sub = parent.add_subparsers(dest="esub")
        parent_sub.add_parser("list")
        parent_sub.add_parser("install")

        leaves = _leaf_parsers(root, [])
        # Two leaves (list, install), neither carries parent's alias `e`.
        assert len(leaves) == 2
        for crumb, _, aliases in leaves:
            assert crumb[0] == "engines"
            assert aliases == [], (
                f"parent alias unexpectedly propagated: crumb={crumb}, "
                f"aliases={aliases}"
            )


class TestLeafParsersMixedShape:
    """Realistic shape: aliased leaves + non-aliased leaves + nested
    subparsers in one tree. Exercises the full _leaf_parsers contract
    as it operates on the actual src.cli surface."""

    def test_realistic_cli_shape(self):
        root = _argparse.ArgumentParser(prog="x")
        sub = root.add_subparsers(dest="cmd")
        sub.add_parser("convert", aliases=["c"])
        sub.add_parser("sample", aliases=["s"])
        engines = sub.add_parser("engines")  # no alias
        e_sub = engines.add_subparsers(dest="esub")
        e_sub.add_parser("list")
        e_sub.add_parser("install")
        sub.add_parser("doctor")  # leaf, no alias

        leaves = _leaf_parsers(root, [])
        canonicals = [tuple(crumb) for crumb, _, _ in leaves]
        by_crumb = {tuple(crumb): aliases for crumb, _, aliases in leaves}

        assert canonicals == [
            ("convert",),
            ("sample",),
            ("engines", "list"),
            ("engines", "install"),
            ("doctor",),
        ]
        assert by_crumb == {
            ("convert",): ["c"],
            ("sample",): ["s"],
            ("engines", "list"): [],
            ("engines", "install"): [],
            ("doctor",): [],
        }
