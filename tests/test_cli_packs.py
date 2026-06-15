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

    def test_remove_quiet_without_yes_prompts_and_cancels(self, tmp_path):
        """--quiet alone must NOT bypass the confirmation guard.

        The flag suppresses success output, but destructive operations
        still require explicit --yes or an interactive 'y' answer.
        Feeding 'n' must cancel and leave the pack intact.
        """
        _make_fake_pack(tmp_path, "quiet_keep")
        rc, out, err = _run(tmp_path, "remove", "quiet_keep", "--quiet", input_str="n")
        assert rc == 3, "--quiet without --yes must return EXIT_CANCELLED when user declines"
        assert (tmp_path / "quiet_keep").is_dir(), "pack must still exist after cancellation"

    def test_remove_quiet_with_yes_removes_without_prompting(self, tmp_path):
        """--yes wins even when combined with --quiet: pack is removed silently."""
        import src.cli.packs as packs_mod

        _make_fake_pack(tmp_path, "quiet_gone")
        with mock.patch.object(packs_mod, "_packs_dir", return_value=tmp_path):
            stdout_buf = StringIO()
            stderr_buf = StringIO()
            input_called = mock.Mock(side_effect=AssertionError("input() must not be called"))
            parser, _ = _make_packs_parser(tmp_path)
            with (
                mock.patch("sys.stdout", stdout_buf),
                mock.patch("sys.stderr", stderr_buf),
                mock.patch("builtins.input", input_called),
            ):
                parsed = parser.parse_args(["packs", "remove", "quiet_gone", "--yes", "--quiet"])
                rc = parsed.func(parsed)
        assert rc == 0, "--yes --quiet must remove the pack"
        assert not (tmp_path / "quiet_gone").exists(), "pack must be deleted"
        assert stdout_buf.getvalue().strip() == "", "--quiet must suppress success output"


# ---------------------------------------------------------------------------
# Prompt-on-stderr regression tests (N5)
# ---------------------------------------------------------------------------


class TestPacksRemovePromptOnStderr:
    """The confirmation prompt must go to stderr, not stdout.

    A user piping `packs remove SLUG | jq` must not see prompt text in
    the JSON pipe.  Both the prompt text and the cancellation message
    must land on stderr only.
    """

    def test_prompt_on_stderr_not_stdout(self, tmp_path):
        """Prompt text must appear on stderr, not stdout."""
        _make_fake_pack(tmp_path, "prompt_target")
        rc, out, err = _run(tmp_path, "remove", "prompt_target", input_str="n")
        assert rc == 3, "declining must return EXIT_CANCELLED"
        assert "Remove voice pack" not in out, "prompt must not leak to stdout"
        assert "Remove voice pack" in err, "prompt must appear on stderr"

    def test_stdout_empty_on_cancel(self, tmp_path):
        """stdout must be empty when the user cancels."""
        _make_fake_pack(tmp_path, "cancel_target")
        rc, out, err = _run(tmp_path, "remove", "cancel_target", input_str="n")
        assert rc == 3
        assert out.strip() == "", "stdout must be empty on cancellation"

    def test_cancelled_message_on_stderr(self, tmp_path):
        """The 'Cancelled.' message must appear on stderr."""
        _make_fake_pack(tmp_path, "cancel_msg_target")
        rc, out, err = _run(tmp_path, "remove", "cancel_msg_target", input_str="n")
        assert rc == 3
        assert "Cancelled" in err

    def test_yes_flag_no_prompt_anywhere(self, tmp_path):
        """With --yes, no prompt text must appear on stdout or stderr."""
        import src.cli.packs as packs_mod

        _make_fake_pack(tmp_path, "yes_target")
        with mock.patch.object(packs_mod, "_packs_dir", return_value=tmp_path):
            stdout_buf = StringIO()
            stderr_buf = StringIO()
            input_called = mock.Mock(side_effect=AssertionError("input() must not be called with --yes"))
            parser, _ = _make_packs_parser(tmp_path)
            with (
                mock.patch("sys.stdout", stdout_buf),
                mock.patch("sys.stderr", stderr_buf),
                mock.patch("builtins.input", input_called),
            ):
                parsed = parser.parse_args(["packs", "remove", "yes_target", "--yes"])
                rc = parsed.func(parsed)
        assert rc == 0
        assert "Remove voice pack" not in stdout_buf.getvalue()
        assert "Remove voice pack" not in stderr_buf.getvalue()


# ---------------------------------------------------------------------------
# Path-traversal regression tests
# ---------------------------------------------------------------------------


class TestPacksPathTraversal:
    """A malicious or careless slug must never escape packs_dir.

    Reaching for `..` or an absolute path with `packs remove` would have
    let an unprivileged caller rmtree arbitrary directories on the
    machine. _resolve_pack_dir() rejects any candidate whose resolved
    path is not a direct child of packs_dir; these tests pin that
    behaviour.
    """

    def test_dotdot_slug_is_rejected(self, tmp_path):
        # Create a victim directory next to packs_dir to make the test
        # concrete: if the guard fails, rmtree("..") wipes it.
        victim = tmp_path.parent / "victim_keep_me"
        victim.mkdir(exist_ok=True)
        try:
            rc, out, err = _run(tmp_path, "remove", "..", "--yes")
            assert rc == 1, "remove .. must report not-found, not succeed"
            assert "not found" in err.lower()
            assert victim.exists(), "guard must not let rmtree escape packs_dir"
        finally:
            if victim.exists():
                import shutil
                shutil.rmtree(victim, ignore_errors=True)

    def test_nested_path_slug_is_rejected(self, tmp_path):
        rc, out, err = _run(tmp_path, "remove", "subdir/inner", "--yes")
        assert rc == 1
        assert "not found" in err.lower()

    def test_absolute_path_slug_is_rejected(self, tmp_path):
        absolute = str(tmp_path.parent / "anywhere")
        rc, out, err = _run(tmp_path, "remove", absolute, "--yes")
        assert rc == 1
        assert "not found" in err.lower()

    def test_info_dotdot_slug_is_rejected(self, tmp_path):
        rc, out, err = _run(tmp_path, "info", "..")
        assert rc == 1
        assert "not found" in err.lower()


# ---------------------------------------------------------------------------
# Prompt-routing regression tests (N5)
# ---------------------------------------------------------------------------


def _run_direct(packs_dir: Path, *args: str, input_return: str = "n") -> int:
    """Run packs subcommand without patching sys.stdout/stderr.

    Allows capsys to capture real stdout/stderr.  input() is still
    mocked so tests are non-interactive.
    """
    import src.cli.packs as packs_mod

    parser, _ = _make_packs_parser(packs_dir)
    with mock.patch.object(packs_mod, "_packs_dir", return_value=packs_dir), \
         mock.patch("builtins.input", return_value=input_return):
        parsed = parser.parse_args(["packs"] + list(args))
        return parsed.func(parsed)


class TestPacksRemovePromptRouting:
    """Confirmation prompt text must land on stderr, not stdout."""

    def test_prompt_text_on_stderr(self, tmp_path, capsys):
        _make_fake_pack(tmp_path, "prompt_check")
        _run_direct(tmp_path, "remove", "prompt_check", input_return="n")
        captured = capsys.readouterr()
        assert "prompt_check" in captured.err, "prompt text must appear on stderr"
        assert "[y/N]" in captured.err, "prompt marker must appear on stderr"

    def test_prompt_text_not_on_stdout(self, tmp_path, capsys):
        _make_fake_pack(tmp_path, "prompt_stdout_check")
        _run_direct(tmp_path, "remove", "prompt_stdout_check", input_return="n")
        captured = capsys.readouterr()
        assert "[y/N]" not in captured.out, "prompt must not appear on stdout"

    def test_stdout_empty_on_cancel(self, tmp_path, capsys):
        _make_fake_pack(tmp_path, "cancel_stdout")
        rc = _run_direct(tmp_path, "remove", "cancel_stdout", input_return="n")
        captured = capsys.readouterr()
        assert rc == 3
        assert captured.out == "", "stdout must be empty when user cancels"

    def test_exit_cancelled_on_no(self, tmp_path, capsys):
        from src.cli._common import EXIT_CANCELLED
        _make_fake_pack(tmp_path, "exit_check")
        rc = _run_direct(tmp_path, "remove", "exit_check", input_return="n")
        assert rc == EXIT_CANCELLED


# ---------------------------------------------------------------------------
# packs import — zip source
# ---------------------------------------------------------------------------


class TestPacksImportZip:
    """`packs import` accepts a portable .zip archive, not just a folder."""

    def test_import_from_zip_installs(self, tmp_path):
        from src.voice_pack import export_pack, load_pack

        source = _make_fake_pack(tmp_path / "src_root", "zip_voice")
        archive = export_pack(source, tmp_path / "zip_voice.abvpack.zip")
        installed_root = tmp_path / "installed_zip"

        rc, out, err = _run(installed_root, "import", str(archive))
        assert rc == 0, err
        # Exactly one pack landed, and it's the one we zipped — not just
        # "some file extracted somewhere".
        installed = list(installed_root.iterdir())
        assert len(installed) == 1
        assert load_pack(installed[0]).meta.name == "Test Voice zip_voice"

    def test_import_from_zip_json(self, tmp_path):
        from src.voice_pack import export_pack

        source = _make_fake_pack(tmp_path / "src_root", "zip_json_voice")
        archive = export_pack(source, tmp_path / "zj.abvpack.zip")
        installed_root = tmp_path / "installed_zj"

        rc, out, err = _run(installed_root, "import", str(archive), "--json")
        assert rc == 0, err
        obj = json.loads(out.strip())
        assert obj["ok"] is True
        assert "path" in obj

    def test_import_corrupt_zip_exits_1(self, tmp_path):
        broken = tmp_path / "broken.zip"
        broken.write_bytes(b"not a real zip")
        rc, out, err = _run(tmp_path / "installed", "import", str(broken))
        assert rc == 1

    def test_import_non_zip_file_exits_1(self, tmp_path):
        notpack = tmp_path / "readme.txt"
        notpack.write_text("hello", encoding="utf-8")
        rc, out, err = _run(tmp_path / "installed", "import", str(notpack))
        assert rc == 1


# ---------------------------------------------------------------------------
# packs export
# ---------------------------------------------------------------------------


class TestPacksExport:
    """`packs export <slug>` bundles an installed pack into a .zip."""

    def test_export_default_out(self, tmp_path, monkeypatch):
        _make_fake_pack(tmp_path, "exp_voice")
        # Default --out lands in the CWD; chdir so we don't litter the repo.
        monkeypatch.chdir(tmp_path)

        rc, out, err = _run(tmp_path, "export", "exp_voice")
        assert rc == 0, err
        # Default out is a CWD-relative name; the file lands in tmp_path.
        expected = tmp_path / "exp_voice.abvpack.zip"
        assert expected.exists()
        assert "exp_voice.abvpack.zip" in out

    def test_export_explicit_out(self, tmp_path):
        _make_fake_pack(tmp_path, "exp_out_voice")
        dest = tmp_path / "shared" / "myvoice.zip"

        rc, out, err = _run(tmp_path, "export", "exp_out_voice", "--out", str(dest))
        assert rc == 0, err
        assert dest.exists()

    def test_export_unknown_slug_exits_1(self, tmp_path):
        rc, out, err = _run(tmp_path, "export", "does_not_exist")
        assert rc == 1
        assert "not found" in err.lower()

    def test_export_path_traversal_slug_exits_1(self, tmp_path):
        # A slug that escapes the packs root must be refused, not exported.
        rc, out, err = _run(tmp_path, "export", "..")
        assert rc == 1

    def test_export_quiet_prints_path_only(self, tmp_path):
        _make_fake_pack(tmp_path, "exp_quiet")
        dest = tmp_path / "q.zip"
        rc, out, err = _run(tmp_path, "export", "exp_quiet", "--out", str(dest), "--quiet")
        assert rc == 0, err
        assert out.strip() == str(dest)

    def test_export_json_ok(self, tmp_path):
        _make_fake_pack(tmp_path, "exp_json")
        dest = tmp_path / "j.zip"
        rc, out, err = _run(tmp_path, "export", "exp_json", "--out", str(dest), "--json")
        assert rc == 0, err
        obj = json.loads(out.strip())
        assert obj["ok"] is True
        assert obj["slug"] == "exp_json"

    def test_export_then_import_round_trip(self, tmp_path):
        _make_fake_pack(tmp_path / "origin", "rt_voice")
        archive = tmp_path / "rt.abvpack.zip"

        rc, out, err = _run(tmp_path / "origin", "export", "rt_voice", "--out", str(archive))
        assert rc == 0, err

        target_root = tmp_path / "other_pc"
        rc, out, err = _run(target_root, "import", str(archive))
        assert rc == 0, err
        rc, out, err = _run(target_root, "list", "--quiet")
        assert rc == 0
        assert "rt_voice" in out
