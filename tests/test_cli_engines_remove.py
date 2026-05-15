"""Tests for engines remove --json and --quiet flags.

Drives the CLI via src.cli.__main__.main() (in-process) so argument
parsing and dispatch are exercised end-to-end without network or disk I/O.
The engine installer's remove() method is mocked throughout.
"""

from __future__ import annotations

import io
import json
import sys
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_installer(engine_id: str = "piper", *, remove_return: bool = True):
    """Return a minimal fake installer object that satisfies _run_remove."""
    inst = mock.MagicMock()
    inst.is_installed.return_value = True
    inst.remove.return_value = remove_return
    return inst


def _run(args: list[str]) -> tuple[int, str, str]:
    """Call main() with captured stdout/stderr; return (rc, stdout, stderr)."""
    from src.cli.__main__ import main

    buf_out = io.StringIO()
    buf_err = io.StringIO()
    with mock.patch("sys.stdout", buf_out), mock.patch("sys.stderr", buf_err):
        rc = main(args)
    return rc, buf_out.getvalue(), buf_err.getvalue()


# ---------------------------------------------------------------------------
# --yes --json: removes engine, emits {"ok": true, "id": ...}, exit 0
# ---------------------------------------------------------------------------


class TestEnginesRemoveJson:
    def test_yes_json_exit_0(self):
        """--yes --json removes engine and exits 0."""
        pytest.importorskip("src.engine_installer")
        import src.engine_installer as ei

        fake = _make_fake_installer()
        with mock.patch.object(ei, "get_installer", return_value=fake):
            rc, out, _ = _run(["engines", "remove", "--yes", "--json", "piper"])

        assert rc == 0

    def test_yes_json_stdout_shape(self):
        """--yes --json emits {"ok": true, "id": "piper"} on stdout."""
        pytest.importorskip("src.engine_installer")
        import src.engine_installer as ei

        fake = _make_fake_installer()
        with mock.patch.object(ei, "get_installer", return_value=fake):
            _, out, _ = _run(["engines", "remove", "--yes", "--json", "piper"])

        lines = [l for l in out.splitlines() if l.strip()]
        assert lines, "Expected JSON output on stdout"
        obj = json.loads(lines[-1])
        assert obj == {"ok": True, "id": "piper"}

    def test_json_no_prompt_bypass(self):
        """--json alone bypasses the confirmation prompt (non-interactive consumer).

        JSON callers cannot answer y/N interactively, so --json implies --yes
        for the prompt only. This mirrors the packs remove pattern from PR #39.
        The engine IS removed and {"ok": true} is emitted.
        """
        pytest.importorskip("src.engine_installer")
        import src.engine_installer as ei

        fake = _make_fake_installer()
        with mock.patch.object(ei, "get_installer", return_value=fake):
            rc, out, _ = _run(["engines", "remove", "--json", "piper"])

        assert rc == 0
        obj = json.loads(out.strip())
        assert obj["ok"] is True
        assert obj["id"] == "piper"
        fake.remove.assert_called_once()

    def test_json_unknown_engine_error_shape(self):
        """--json on unknown engine emits {"ok": false, "error": ..., "exit_code": 1}."""
        pytest.importorskip("src.engine_installer")
        import src.engine_installer as ei

        with mock.patch.object(ei, "get_installer", return_value=None):
            rc, out, _ = _run(["engines", "remove", "--json", "no_such_engine"])

        assert rc == 1
        obj = json.loads(out.strip())
        assert obj["ok"] is False
        assert "error" in obj
        assert obj["exit_code"] == 1

    def test_json_not_installed_error_shape(self):
        """--json on not-installed engine emits {"ok": false, ...}, exit 1."""
        pytest.importorskip("src.engine_installer")
        import src.engine_installer as ei

        fake = _make_fake_installer(remove_return=False)
        fake.is_installed.return_value = True  # passes first guard
        fake.remove.return_value = False        # remove() says nothing removed

        with mock.patch.object(ei, "get_installer", return_value=fake):
            rc, out, _ = _run(["engines", "remove", "--json", "piper"])

        assert rc == 1
        obj = json.loads(out.strip())
        assert obj["ok"] is False


# ---------------------------------------------------------------------------
# --yes --quiet: removes engine, no stdout, exit 0
# ---------------------------------------------------------------------------


class TestEnginesRemoveQuiet:
    def test_yes_quiet_exit_0(self):
        """--yes --quiet exits 0."""
        pytest.importorskip("src.engine_installer")
        import src.engine_installer as ei

        fake = _make_fake_installer()
        with mock.patch.object(ei, "get_installer", return_value=fake):
            rc, out, _ = _run(["engines", "remove", "--yes", "--quiet", "piper"])

        assert rc == 0

    def test_yes_quiet_no_stdout(self):
        """--yes --quiet produces no stdout output."""
        pytest.importorskip("src.engine_installer")
        import src.engine_installer as ei

        fake = _make_fake_installer()
        with mock.patch.object(ei, "get_installer", return_value=fake):
            _, out, _ = _run(["engines", "remove", "--yes", "--quiet", "piper"])

        assert out.strip() == ""

    def test_quiet_does_not_bypass_prompt(self):
        """--quiet alone does NOT bypass the confirmation prompt.

        Cosmetic flags must not change destructive behaviour — this is the
        lesson from M6 (packs remove bug, fixed in PR #39). With --quiet
        but no --yes, feeding 'n' to the prompt must cancel the operation.
        """
        pytest.importorskip("src.engine_installer")
        import src.engine_installer as ei

        fake = _make_fake_installer()
        with mock.patch.object(ei, "get_installer", return_value=fake):
            with mock.patch("builtins.input", return_value="n"):
                rc, _, _ = _run(["engines", "remove", "--quiet", "piper"])

        assert rc == 3  # EXIT_CANCELLED
        fake.remove.assert_not_called()

    def test_quiet_prompt_eof_cancels(self):
        """--quiet alone with EOF on the prompt cancels cleanly (exit 3)."""
        pytest.importorskip("src.engine_installer")
        import src.engine_installer as ei

        fake = _make_fake_installer()

        def _raise_eof(prompt=""):
            raise EOFError

        with mock.patch.object(ei, "get_installer", return_value=fake):
            with mock.patch("builtins.input", side_effect=_raise_eof):
                rc, _, _ = _run(["engines", "remove", "--quiet", "piper"])

        assert rc == 3  # EXIT_CANCELLED
        fake.remove.assert_not_called()


# ---------------------------------------------------------------------------
# Backwards-compatibility: --yes without --json/--quiet
# ---------------------------------------------------------------------------


class TestEnginesRemoveBackcompat:
    def test_yes_prints_removed_line(self):
        """--yes (no --json, no --quiet) prints 'Removed: <id>' to stdout."""
        pytest.importorskip("src.engine_installer")
        import src.engine_installer as ei

        fake = _make_fake_installer()
        with mock.patch.object(ei, "get_installer", return_value=fake):
            rc, out, _ = _run(["engines", "remove", "--yes", "piper"])

        assert rc == 0
        assert "piper" in out
        assert "Removed" in out or "removed" in out.lower()

    def test_yes_exit_0(self):
        pytest.importorskip("src.engine_installer")
        import src.engine_installer as ei

        fake = _make_fake_installer()
        with mock.patch.object(ei, "get_installer", return_value=fake):
            rc, _, _ = _run(["engines", "remove", "--yes", "piper"])

        assert rc == 0
