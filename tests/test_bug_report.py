"""Tests for :mod:`src.bug_report` — URL construction only. The actual
``webbrowser.open`` call is wired from the GUI and isn't exercised here.
"""

from __future__ import annotations

import urllib.parse

from src.bug_report import build_bug_report_url


def _parse(url: str) -> tuple[str, dict[str, str]]:
    """Split the URL into base + query dict for field-by-field checks."""
    parsed = urllib.parse.urlparse(url)
    qs = dict(urllib.parse.parse_qsl(parsed.query))
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return base, qs


def test_builds_github_issues_new_url():
    url = build_bug_report_url(
        app_version="3.9.1",
        engine_id="chatterbox_fi",
        os_platform="Windows-11",
    )
    base, qs = _parse(url)
    assert base == "https://github.com/MikkoNumminen/AudiobookMaker/issues/new"
    assert "title" in qs
    assert "body" in qs


def test_title_includes_version():
    url = build_bug_report_url(
        app_version="3.9.1",
        engine_id="chatterbox_fi",
        os_platform="Windows-11",
    )
    _, qs = _parse(url)
    assert "v3.9.1" in qs["title"]


def test_body_includes_version_os_and_engine():
    url = build_bug_report_url(
        app_version="3.9.1",
        engine_id="chatterbox_fi",
        os_platform="Windows-11-10.0.26200",
    )
    _, qs = _parse(url)
    body = qs["body"]
    assert "v3.9.1" in body
    assert "Windows-11-10.0.26200" in body
    assert "chatterbox_fi" in body


def test_missing_engine_renders_as_none_placeholder():
    url = build_bug_report_url(
        app_version="3.9.1",
        engine_id=None,
        os_platform="Linux-6.1",
    )
    _, qs = _parse(url)
    assert "(none)" in qs["body"]


def test_empty_engine_renders_as_none_placeholder():
    url = build_bug_report_url(
        app_version="3.9.1",
        engine_id="",
        os_platform="Linux-6.1",
    )
    _, qs = _parse(url)
    assert "(none)" in qs["body"]


def test_os_platform_defaults_to_platform_platform(monkeypatch):
    monkeypatch.setattr(
        "src.bug_report.platform.platform", lambda: "FakeOS-1.0"
    )
    url = build_bug_report_url(
        app_version="3.9.1",
        engine_id="chatterbox_fi",
    )
    _, qs = _parse(url)
    assert "FakeOS-1.0" in qs["body"]


def test_special_characters_in_engine_are_url_encoded():
    # Engine ids that happen to contain characters needing URL-encoding
    # (e.g. a space) must round-trip cleanly through the parser.
    url = build_bug_report_url(
        app_version="3.9.1",
        engine_id="custom engine &feature",
        os_platform="OS",
    )
    _, qs = _parse(url)
    assert "custom engine &feature" in qs["body"]
