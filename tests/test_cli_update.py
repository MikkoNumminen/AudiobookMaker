"""Tests for the `update` CLI subcommand (src/cli/update.py).

All network calls are mocked via unittest.mock.patch so no real HTTP
requests are made.  The tests exercise the CLI module in-process
(calling _run_check / _run_apply directly) rather than via subprocess
so that mock.patch can intercept the auto_updater calls.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
from io import StringIO
from typing import Any
from unittest import mock

import pytest

from src.auto_updater import UpdateInfo
from src.cli import update as update_mod


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _make_update_info(available: bool, latest: str = "99.0.0") -> UpdateInfo:
    return UpdateInfo(
        available=available,
        current_version="3.13.0",
        latest_version=latest if available else "3.13.0",
        download_url="https://example.com/AudiobookMaker-Setup-99.0.0.exe" if available else "",
        release_notes="SHA-256: " + "a" * 64 if available else "",
        asset_size_bytes=1024 if available else 0,
        sha256="a" * 64 if available else "",
    )


def _args(**kwargs: Any) -> argparse.Namespace:
    """Build a minimal Namespace for CLI functions."""
    defaults = {"json": False, "quiet": False, "yes": False}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# update check — up to date
# ---------------------------------------------------------------------------


class TestUpdateCheckUpToDate:
    def test_exits_0(self, capsys):
        info = _make_update_info(available=False)
        with mock.patch("src.cli.update._is_frozen", return_value=True), \
             mock.patch("src.auto_updater.check_for_update", return_value=info):
            rc = update_mod._run_check(_args())
        assert rc == 0

    def test_prints_already_latest(self, capsys):
        info = _make_update_info(available=False)
        with mock.patch("src.cli.update._is_frozen", return_value=True), \
             mock.patch("src.auto_updater.check_for_update", return_value=info):
            update_mod._run_check(_args())
        out = capsys.readouterr().out
        assert "Already on latest" in out
        assert "3.13.0" in out


# ---------------------------------------------------------------------------
# update check — update available
# ---------------------------------------------------------------------------


class TestUpdateCheckAvailable:
    def test_exits_0(self, capsys):
        info = _make_update_info(available=True)
        with mock.patch("src.cli.update._is_frozen", return_value=True), \
             mock.patch("src.auto_updater.check_for_update", return_value=info):
            rc = update_mod._run_check(_args())
        assert rc == 0

    def test_prints_update_available(self, capsys):
        info = _make_update_info(available=True)
        with mock.patch("src.cli.update._is_frozen", return_value=True), \
             mock.patch("src.auto_updater.check_for_update", return_value=info):
            update_mod._run_check(_args())
        out = capsys.readouterr().out
        assert "Update available" in out
        assert "99.0.0" in out
        assert "3.13.0" in out


# ---------------------------------------------------------------------------
# update check --json
# ---------------------------------------------------------------------------


class TestUpdateCheckJson:
    def test_json_up_to_date_parseable(self, capsys):
        info = _make_update_info(available=False)
        with mock.patch("src.cli.update._is_frozen", return_value=True), \
             mock.patch("src.auto_updater.check_for_update", return_value=info):
            rc = update_mod._run_check(_args(json=True))
        assert rc == 0
        out = capsys.readouterr().out.strip()
        obj = json.loads(out)
        assert obj["update_available"] is False
        assert obj["current_version"] == "3.13.0"
        assert "latest_version" in obj
        assert "release_url" in obj

    def test_json_update_available_parseable(self, capsys):
        info = _make_update_info(available=True)
        with mock.patch("src.cli.update._is_frozen", return_value=True), \
             mock.patch("src.auto_updater.check_for_update", return_value=info):
            rc = update_mod._run_check(_args(json=True))
        assert rc == 0
        out = capsys.readouterr().out.strip()
        obj = json.loads(out)
        assert obj["update_available"] is True
        assert obj["latest_version"] == "99.0.0"
        assert "releases/tag/v99.0.0" in obj["release_url"]

    def test_json_fields_present(self, capsys):
        info = _make_update_info(available=True)
        with mock.patch("src.cli.update._is_frozen", return_value=True), \
             mock.patch("src.auto_updater.check_for_update", return_value=info):
            update_mod._run_check(_args(json=True))
        out = capsys.readouterr().out.strip()
        obj = json.loads(out)
        for field in ("current_version", "latest_version", "update_available", "release_url"):
            assert field in obj, f"missing field: {field}"


# ---------------------------------------------------------------------------
# update apply — dev mode (not frozen)
# ---------------------------------------------------------------------------


class TestUpdateApplyDevMode:
    def test_exits_0_in_dev_mode(self, capsys):
        with mock.patch("src.cli.update._is_frozen", return_value=False):
            rc = update_mod._run_apply(_args())
        assert rc == 0

    def test_prints_source_notice(self, capsys):
        with mock.patch("src.cli.update._is_frozen", return_value=False):
            update_mod._run_apply(_args())
        out = capsys.readouterr().out
        assert "running from source" in out

    def test_includes_manual_download_url(self, capsys):
        with mock.patch("src.cli.update._is_frozen", return_value=False):
            update_mod._run_apply(_args())
        out = capsys.readouterr().out
        assert "github.com" in out

    def test_json_dev_mode_parseable(self, capsys):
        with mock.patch("src.cli.update._is_frozen", return_value=False):
            rc = update_mod._run_apply(_args(json=True))
        assert rc == 0
        out = capsys.readouterr().out.strip()
        obj = json.loads(out)
        assert obj["dev_mode"] is True
        assert "message" in obj


# ---------------------------------------------------------------------------
# update apply — up to date (frozen)
# ---------------------------------------------------------------------------


class TestUpdateApplyUpToDate:
    def test_exits_0_when_up_to_date(self, capsys):
        info = _make_update_info(available=False)
        with mock.patch("src.cli.update._is_frozen", return_value=True), \
             mock.patch("src.auto_updater.check_for_update", return_value=info):
            rc = update_mod._run_apply(_args(yes=True))
        assert rc == 0

    def test_prints_already_latest(self, capsys):
        info = _make_update_info(available=False)
        with mock.patch("src.cli.update._is_frozen", return_value=True), \
             mock.patch("src.auto_updater.check_for_update", return_value=info):
            update_mod._run_apply(_args(yes=True))
        out = capsys.readouterr().out
        assert "Already on latest" in out


# ---------------------------------------------------------------------------
# update apply — user cancels prompt
# ---------------------------------------------------------------------------


class TestUpdateApplyCancel:
    def test_exit_3_on_no(self, capsys):
        # EXIT_CANCELLED = 3 per _common.py
        info = _make_update_info(available=True)
        with mock.patch("src.cli.update._is_frozen", return_value=True), \
             mock.patch("src.auto_updater.check_for_update", return_value=info), \
             mock.patch("builtins.input", return_value="n"):
            rc = update_mod._run_apply(_args())
        assert rc == 3

    def test_exit_3_on_empty(self, capsys):
        # EXIT_CANCELLED = 3 per _common.py
        info = _make_update_info(available=True)
        with mock.patch("src.cli.update._is_frozen", return_value=True), \
             mock.patch("src.auto_updater.check_for_update", return_value=info), \
             mock.patch("builtins.input", return_value=""):
            rc = update_mod._run_apply(_args())
        assert rc == 3


# ---------------------------------------------------------------------------
# update apply — SHA-256 mismatch → exit 2
# ---------------------------------------------------------------------------


class TestUpdateApplySha256Mismatch:
    def test_exit_2_on_integrity_failure(self, capsys):
        from src.auto_updater import IntegrityError
        info = _make_update_info(available=True)
        with mock.patch("src.cli.update._is_frozen", return_value=True), \
             mock.patch("src.auto_updater.check_for_update", return_value=info), \
             mock.patch(
                 "src.auto_updater.download_update",
                 side_effect=IntegrityError(
                     "Integrity check failed: expected SHA-256 abc…"
                 ),
             ):
            rc = update_mod._run_apply(_args(yes=True))
        assert rc == 2


# ---------------------------------------------------------------------------
# update apply — network failure → exit 4
# ---------------------------------------------------------------------------


class TestUpdateApplyNetworkFailure:
    def test_exit_4_on_network_error(self, capsys):
        info = _make_update_info(available=True)
        with mock.patch("src.cli.update._is_frozen", return_value=True), \
             mock.patch("src.auto_updater.check_for_update", return_value=info), \
             mock.patch(
                 "src.auto_updater.download_update",
                 side_effect=RuntimeError("Download failed: connection refused"),
             ):
            rc = update_mod._run_apply(_args(yes=True))
        assert rc == 4


# ---------------------------------------------------------------------------
# update apply — successful apply (mocked to not os._exit)
# ---------------------------------------------------------------------------


class TestUpdateApplySuccess:
    def test_exit_0_after_apply(self, capsys):
        from pathlib import Path
        info = _make_update_info(available=True)
        fake_path = Path("/tmp/AudiobookMaker-Setup-99.0.0.exe")
        with mock.patch("src.cli.update._is_frozen", return_value=True), \
             mock.patch("src.auto_updater.check_for_update", return_value=info), \
             mock.patch("src.auto_updater.download_update", return_value=fake_path), \
             mock.patch("src.auto_updater.apply_update") as mock_apply:
            rc = update_mod._run_apply(_args(yes=True))
        assert rc == 0
        mock_apply.assert_called_once_with(fake_path, expected_version="99.0.0")

    def test_yes_flag_skips_prompt(self, capsys):
        from pathlib import Path
        info = _make_update_info(available=True)
        fake_path = Path("/tmp/AudiobookMaker-Setup-99.0.0.exe")
        with mock.patch("src.cli.update._is_frozen", return_value=True), \
             mock.patch("src.auto_updater.check_for_update", return_value=info), \
             mock.patch("src.auto_updater.download_update", return_value=fake_path), \
             mock.patch("src.auto_updater.apply_update"), \
             mock.patch("builtins.input") as mock_input:
            update_mod._run_apply(_args(yes=True))
        mock_input.assert_not_called()


# ---------------------------------------------------------------------------
# update check — network failure → exit 4
# ---------------------------------------------------------------------------


class TestUpdateCheckNetworkFailure:
    """Regression tests: network errors from check_for_update must exit 4."""

    def test_exit_4_on_url_error(self, capsys):
        exc = urllib.error.URLError("Name or service not known")
        with mock.patch(
            "src.auto_updater.check_for_update", side_effect=exc
        ):
            rc = update_mod._run_check(_args())
        assert rc == 4

    def test_stderr_message_on_url_error(self, capsys):
        exc = urllib.error.URLError("Name or service not known")
        with mock.patch(
            "src.auto_updater.check_for_update", side_effect=exc
        ):
            update_mod._run_check(_args())
        err = capsys.readouterr().err
        assert "update check failed" in err
        assert "Name or service not known" in err

    def test_no_stdout_on_url_error(self, capsys):
        exc = urllib.error.URLError("Name or service not known")
        with mock.patch(
            "src.auto_updater.check_for_update", side_effect=exc
        ):
            update_mod._run_check(_args())
        out = capsys.readouterr().out
        assert out == ""

    def test_json_error_shape_on_url_error(self, capsys):
        exc = urllib.error.URLError("Name or service not known")
        with mock.patch(
            "src.auto_updater.check_for_update", side_effect=exc
        ):
            rc = update_mod._run_check(_args(json=True))
        assert rc == 4
        out = capsys.readouterr().out.strip()
        obj = json.loads(out)
        assert obj["kind"] == "error"
        assert obj["exit_code"] == 4
        assert "error" in obj

    def test_json_no_stderr_on_url_error(self, capsys):
        exc = urllib.error.URLError("Name or service not known")
        with mock.patch(
            "src.auto_updater.check_for_update", side_effect=exc
        ):
            update_mod._run_check(_args(json=True))
        err = capsys.readouterr().err
        assert err == ""

    def test_quiet_exit_4_no_stderr(self, capsys):
        exc = urllib.error.URLError("Name or service not known")
        with mock.patch(
            "src.auto_updater.check_for_update", side_effect=exc
        ):
            rc = update_mod._run_check(_args(quiet=True))
        assert rc == 4
        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out == ""

    def test_exit_4_on_connection_error(self, capsys):
        exc = ConnectionError("Connection refused")
        with mock.patch(
            "src.auto_updater.check_for_update", side_effect=exc
        ):
            rc = update_mod._run_check(_args())
        assert rc == 4

    def test_exit_4_on_os_error(self, capsys):
        exc = OSError("Network unreachable")
        with mock.patch(
            "src.auto_updater.check_for_update", side_effect=exc
        ):
            rc = update_mod._run_check(_args())
        assert rc == 4

    def test_success_path_still_exits_0_up_to_date(self, capsys):
        """Control: up-to-date success path must still return 0."""
        info = _make_update_info(available=False)
        with mock.patch("src.auto_updater.check_for_update", return_value=info):
            rc = update_mod._run_check(_args())
        assert rc == 0

    def test_success_path_still_exits_0_update_available(self, capsys):
        """Control: update-available success path must still return 0."""
        info = _make_update_info(available=True)
        with mock.patch("src.auto_updater.check_for_update", return_value=info):
            rc = update_mod._run_check(_args())
        assert rc == 0
