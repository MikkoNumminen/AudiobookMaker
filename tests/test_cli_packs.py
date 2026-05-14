"""Tests for the 'packs' CLI subcommand (src/cli/packs.py).

Calls the subcommand module directly (add_parser + argparse dispatch) so
tests exercise the real argument-parsing and dispatch without requiring
the subcommand to be registered in __main__.py.

Pack directory is redirected to tmp_path via monkeypatching _packs_dir so
tests never touch ~/.audiobookmaker.
"""

from __future__ import annotations

import argparse
import json
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_packs_parser(packs_dir: Path):
    """Build a minimal ArgumentParser with the packs subcommand attached.

    Monkeypatches src.cli.packs._packs_dir to return packs_dir so every
    sub-subcommand in the test uses an isolated temporary directory.
    """
    import src.cli.packs as packs_mod

    parser = argparse.ArgumentParser(prog="audiobookmaker")
    subparsers = parser.add_subparsers(dest="command")
    packs_mod.add_parser(subparsers)
    return parser, packs_mod


def _run(packs_dir: Path, *args: str, input_str: str | None = None) -> tuple[int, str, str]:
    """Parse *args through the packs subcommand and call run().

    Returns (returncode, stdout, stderr).
    Captures stdout/stderr via capsys-style patching.
    """
    import src.cli.packs as packs_mod
    import sys

    parser, _ = _make_packs_parser(packs_dir)

    with mock.patch.object(packs_mod, "_packs_dir", return_value=packs_dir):
        stdout_buf = StringIO()
        stderr_buf = StringIO()
        # Also patch builtins.input for remove confirmation tests.
        input_patch = mock.patch("builtins.input", return_value=input_str or "")
        with mock.patch("sys.stdout", stdout_buf), mock.patch("sys.stderr", stderr_buf), input_patch:
            parsed = parser.parse_args(["packs"] + list(args))
            rc = parsed.func(parsed)

    return rc, stdout_buf.getvalue(), stderr_buf.getvalue()


def _make_fake_pack(packs_root: Path, slug: str, *, tier: str = "few_shot") -> Path:
    """Create a minimal valid voice pack directory under packs_root/slug."""
    pack_dir = packs_root / slug
    pack_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "name": f"Test Voice {slug}",
        "language": "fi",
        "tier": tier,
        "tier_reason": "short source",
        "total_source_minutes": 3.5,
        "format_version": 1,
    }
    (pack_dir / "meta.yaml").write_text(yaml.dump(meta), encoding="utf-8")
    (pack_dir / "sample.wav").write_bytes(b"RIFF" + b"\x00" * 36)

    if tier == "few_shot":
        (pack_dir / "reference.wav").write_bytes(b"RIFF" + b"\x00" * 36)
    elif tier in ("full_lora", "reduced_lora"):
        (pack_dir / "adapter.pt").write_bytes(b"\x80\x02")

    return pack_dir


# ---------------------------------------------------------------------------
# packs list — empty dir
# ---------------------------------------------------------------------------


class TestPacksListEmpty:
    def test_exit_0_empty(self, tmp_path):
        rc, out, err = _run(tmp_path, "list")
        assert rc == 0

    def test_human_mode_prints_message(self, tmp_path):
        rc, out, err = _run(tmp_path, "list")
        assert "No voice packs installed" in out

    def test_quiet_mode_prints_nothing(self, tmp_path):
        rc, out, err = _run(tmp_path, "list", "--quiet")
        assert rc == 0
        assert out.strip() == ""

    def test_json_mode_zero_lines(self, tmp_path):
        rc, out, err = _run(tmp_path, "list", "--json")
        assert rc == 0
        lines = [l for l in out.splitlines() if l.strip()]
        assert lines == []


# ---------------------------------------------------------------------------
# packs list — with a fake pack
# ---------------------------------------------------------------------------


class TestPacksListWithPack:
    def test_pack_appears_in_list(self, tmp_path):
        _make_fake_pack(tmp_path, "my_test_voice")
        rc, out, err = _run(tmp_path, "list")
        assert rc == 0
        assert "my_test_voice" in out

    def test_quiet_prints_slug(self, tmp_path):
        _make_fake_pack(tmp_path, "my_test_voice")
        rc, out, err = _run(tmp_path, "list", "--quiet")
        assert rc == 0
        assert "my_test_voice" in out.splitlines()

    def test_json_emits_valid_object(self, tmp_path):
        _make_fake_pack(tmp_path, "my_test_voice")
        rc, out, err = _run(tmp_path, "list", "--json")
        assert rc == 0
        lines = [l for l in out.splitlines() if l.strip()]
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["slug"] == "my_test_voice"
        assert "name" in obj
        assert "language" in obj
        assert "tier" in obj
        assert "path" in obj


# ---------------------------------------------------------------------------
# packs info
# ---------------------------------------------------------------------------


class TestPacksInfo:
    def test_unknown_slug_exits_1(self, tmp_path):
        rc, out, err = _run(tmp_path, "info", "nonexistent_slug")
        assert rc == 1

    def test_known_slug_exits_0(self, tmp_path):
        _make_fake_pack(tmp_path, "info_test")
        rc, out, err = _run(tmp_path, "info", "info_test")
        assert rc == 0

    def test_human_mode_shows_fields(self, tmp_path):
        _make_fake_pack(tmp_path, "info_test")
        rc, out, err = _run(tmp_path, "info", "info_test")
        assert "info_test" in out
        assert "fi" in out
        assert "few_shot" in out

    def test_json_mode_has_slug(self, tmp_path):
        _make_fake_pack(tmp_path, "info_test")
        rc, out, err = _run(tmp_path, "info", "info_test", "--json")
        assert rc == 0
        obj = json.loads(out.strip())
        assert obj["slug"] == "info_test"
        assert obj["language"] == "fi"


# ---------------------------------------------------------------------------
# packs import
# ---------------------------------------------------------------------------


class TestPacksImport:
    def test_missing_dir_exits_1(self, tmp_path):
        rc, out, err = _run(tmp_path, "import", str(tmp_path / "no_such_dir"))
        assert rc == 1

    def test_invalid_pack_exits_1(self, tmp_path):
        empty_dir = tmp_path / "empty_pack"
        empty_dir.mkdir()
        rc, out, err = _run(tmp_path, "import", str(empty_dir))
        assert rc == 1

    def test_valid_pack_installs(self, tmp_path):
        source = tmp_path / "source_packs" / "src_voice"
        _make_fake_pack(source.parent, source.name)
        installed_root = tmp_path / "installed"
        import src.cli.packs as packs_mod
        with mock.patch.object(packs_mod, "_packs_dir", return_value=installed_root):
            stdout_buf = StringIO()
            stderr_buf = StringIO()
            parser, _ = _make_packs_parser(installed_root)
            with mock.patch("sys.stdout", stdout_buf), mock.patch("sys.stderr", stderr_buf):
                parsed = parser.parse_args(["packs", "import", str(source)])
                rc = parsed.func(parsed)
        assert rc == 0
        assert installed_root.exists()
        assert any(installed_root.iterdir())

    def test_quiet_prints_path(self, tmp_path):
        source = tmp_path / "source_packs" / "q_voice"
        _make_fake_pack(source.parent, source.name)
        installed_root = tmp_path / "installed_q"
        import src.cli.packs as packs_mod
        with mock.patch.object(packs_mod, "_packs_dir", return_value=installed_root):
            stdout_buf = StringIO()
            stderr_buf = StringIO()
            parser, _ = _make_packs_parser(installed_root)
            with mock.patch("sys.stdout", stdout_buf), mock.patch("sys.stderr", stderr_buf):
                parsed = parser.parse_args(["packs", "import", str(source), "--quiet"])
                rc = parsed.func(parsed)
        assert rc == 0
        assert stdout_buf.getvalue().strip() != ""

    def test_json_emits_ok_true(self, tmp_path):
        source = tmp_path / "source_packs" / "j_voice"
        _make_fake_pack(source.parent, source.name)
        installed_root = tmp_path / "installed_j"
        import src.cli.packs as packs_mod
        with mock.patch.object(packs_mod, "_packs_dir", return_value=installed_root):
            stdout_buf = StringIO()
            stderr_buf = StringIO()
            parser, _ = _make_packs_parser(installed_root)
            with mock.patch("sys.stdout", stdout_buf), mock.patch("sys.stderr", stderr_buf):
                parsed = parser.parse_args(["packs", "import", str(source), "--json"])
                rc = parsed.func(parsed)
        assert rc == 0
        obj = json.loads(stdout_buf.getvalue().strip())
        assert obj["ok"] is True
        assert "path" in obj


# ---------------------------------------------------------------------------
# packs remove
# ---------------------------------------------------------------------------


class TestPacksRemove:
    def test_unknown_slug_exits_1(self, tmp_path):
        rc, out, err = _run(tmp_path, "remove", "ghost", "--yes")
        assert rc == 1

    def test_remove_with_yes_flag(self, tmp_path):
        _make_fake_pack(tmp_path, "deleteme")
        rc, out, err = _run(tmp_path, "remove", "deleteme", "--yes")
        assert rc == 0
        assert not (tmp_path / "deleteme").exists()

    def test_remove_json_mode_no_prompt(self, tmp_path):
        _make_fake_pack(tmp_path, "rm_json")
        rc, out, err = _run(tmp_path, "remove", "rm_json", "--json")
        assert rc == 0
        obj = json.loads(out.strip())
        assert obj["ok"] is True
        assert obj["slug"] == "rm_json"
        assert not (tmp_path / "rm_json").exists()
