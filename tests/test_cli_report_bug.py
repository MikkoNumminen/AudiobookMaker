"""Tests for the `report-bug` CLI subcommand (src/cli/report_bug.py).

All calls to webbrowser.open and build_bug_report_url are mocked so
tests never open a real browser or hit the network.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from io import StringIO
from typing import Any
from unittest import mock

import pytest

from src.cli import report_bug as rb_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_URL = "https://github.com/MikkoNumminen/AudiobookMaker/issues/new?title=test"


def _args(**kwargs: Any) -> argparse.Namespace:
    defaults = {"json": False, "quiet": False, "print_only": False}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _cli(*args: str) -> subprocess.CompletedProcess:
    """Run the CLI via subprocess and return CompletedProcess."""
    env = os.environ.copy()
    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# --print: URL on stdout, browser NOT opened
# ---------------------------------------------------------------------------


class TestReportBugPrint:
    def test_report_bug_print_prints_url(self, capsys):
        with mock.patch(
            "src.cli.report_bug.webbrowser.open",
            side_effect=AssertionError("webbrowser.open must not be called with --print"),
        ), mock.patch(
            "src.bug_report.build_bug_report_url",
            return_value=_FAKE_URL,
        ):
            rc = rb_mod.run(_args(print_only=True))

        out, _ = capsys.readouterr()
        assert rc == 0
        assert _FAKE_URL in out

    def test_report_bug_print_no_extra_noise(self, capsys):
        with mock.patch(
            "src.cli.report_bug.webbrowser.open",
            side_effect=AssertionError("browser must not open"),
        ), mock.patch(
            "src.bug_report.build_bug_report_url",
            return_value=_FAKE_URL,
        ):
            rc = rb_mod.run(_args(print_only=True))

        out, err = capsys.readouterr()
        assert rc == 0
        assert out.strip() == _FAKE_URL
        assert err == ""


# ---------------------------------------------------------------------------
# Default (no flag): URL printed, browser opened
# ---------------------------------------------------------------------------


class TestReportBugDefault:
    def test_report_bug_no_flag_opens_browser(self, capsys):
        browser_calls: list[str] = []
        with mock.patch(
            "src.cli.report_bug.webbrowser.open",
            side_effect=lambda url, **kw: browser_calls.append(url) or True,
        ), mock.patch(
            "src.bug_report.build_bug_report_url",
            return_value=_FAKE_URL,
        ):
            rc = rb_mod.run(_args())

        out, _ = capsys.readouterr()
        assert rc == 0
        assert browser_calls == [_FAKE_URL]
        assert _FAKE_URL in out

    def test_report_bug_browser_failure_returns_0(self, capsys):
        with mock.patch(
            "src.cli.report_bug.webbrowser.open",
            return_value=False,
        ), mock.patch(
            "src.bug_report.build_bug_report_url",
            return_value=_FAKE_URL,
        ):
            rc = rb_mod.run(_args())

        out, err = capsys.readouterr()
        assert rc == 0
        assert _FAKE_URL in out
        assert "Could not open browser" in err

    def test_report_bug_browser_exception_returns_0(self, capsys):
        with mock.patch(
            "src.cli.report_bug.webbrowser.open",
            side_effect=OSError("no browser"),
        ), mock.patch(
            "src.bug_report.build_bug_report_url",
            return_value=_FAKE_URL,
        ):
            rc = rb_mod.run(_args())

        out, err = capsys.readouterr()
        assert rc == 0
        assert _FAKE_URL in out
        assert "Could not open browser" in err


# ---------------------------------------------------------------------------
# --json: URL + fields emitted, browser NOT opened
# ---------------------------------------------------------------------------


class TestReportBugJson:
    def test_report_bug_json_emits_url_and_fields(self, capsys):
        with mock.patch(
            "src.cli.report_bug.webbrowser.open",
            side_effect=AssertionError("browser must not open in --json mode"),
        ), mock.patch(
            "src.bug_report.build_bug_report_url",
            return_value=_FAKE_URL,
        ):
            rc = rb_mod.run(_args(json=True))

        out, _ = capsys.readouterr()
        assert rc == 0
        obj = json.loads(out.strip())
        assert "url" in obj
        assert "fields" in obj
        assert obj["url"] == _FAKE_URL

    def test_report_bug_json_does_not_open_browser(self, capsys):
        browser_called = False

        def _fail(*a, **kw):
            nonlocal browser_called
            browser_called = True
            raise AssertionError("webbrowser.open was called in --json mode")

        with mock.patch("src.cli.report_bug.webbrowser.open", side_effect=_fail), \
             mock.patch("src.bug_report.build_bug_report_url", return_value=_FAKE_URL):
            rb_mod.run(_args(json=True))

        assert not browser_called

    def test_report_bug_json_fields_keys(self, capsys):
        with mock.patch("src.cli.report_bug.webbrowser.open"), \
             mock.patch("src.bug_report.build_bug_report_url", return_value=_FAKE_URL):
            rb_mod.run(_args(json=True))

        out, _ = capsys.readouterr()
        obj = json.loads(out.strip())
        fields = obj["fields"]
        assert "app_version" in fields
        assert "os_platform" in fields
        assert "engine_id" in fields


# ---------------------------------------------------------------------------
# --quiet: URL only, no extra noise
# ---------------------------------------------------------------------------


class TestReportBugQuiet:
    def test_report_bug_quiet_prints_url_only(self, capsys):
        with mock.patch(
            "src.cli.report_bug.webbrowser.open",
            side_effect=AssertionError("browser must not open in --quiet mode"),
        ), mock.patch(
            "src.bug_report.build_bug_report_url",
            return_value=_FAKE_URL,
        ):
            rc = rb_mod.run(_args(quiet=True))

        out, err = capsys.readouterr()
        assert rc == 0
        lines = [l for l in out.splitlines() if l.strip()]
        assert len(lines) == 1
        assert lines[0] == _FAKE_URL
        assert err == ""


# ---------------------------------------------------------------------------
# Error path: build_bug_report_url raises
# ---------------------------------------------------------------------------


class TestReportBugError:
    def test_report_bug_build_error_returns_5(self, capsys):
        with mock.patch(
            "src.bug_report.build_bug_report_url",
            side_effect=RuntimeError("boom"),
        ):
            rc = rb_mod.run(_args())

        _, err = capsys.readouterr()
        assert rc == 5
        assert "Error building bug-report URL" in err


# ---------------------------------------------------------------------------
# Integration: report-bug appears in --help output
# ---------------------------------------------------------------------------


class TestReportBugHelpIntegration:
    def test_report_bug_help_text_in_main_help(self):
        result = _cli("--help")
        assert result.returncode == 0
        assert "report-bug" in result.stdout
