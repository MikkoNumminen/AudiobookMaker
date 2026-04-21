"""Tests for the Settings "Report a bug" handler in :mod:`src.gui_unified`.

The URL builder in :mod:`src.bug_report` is covered by a dedicated unit
test file; this module pins the GUI wiring — specifically, that pressing
the Settings button calls ``webbrowser.open`` exactly once with a URL
carrying the running ``APP_VERSION``, the GitHub new-issue path, and a
URL-encoded body that mentions platform info.

Test instance is constructed via ``UnifiedApp.__new__`` to bypass the
CTk widget tree (same pattern as ``tests/test_gui_drag_drop.py``).
"""

from __future__ import annotations

import urllib.parse
from unittest.mock import MagicMock

import pytest

# ``src.gui_unified`` imports customtkinter at module top; the Chatterbox
# venv intentionally omits CTk, so skip this file cleanly there. The
# full dev suite (py -3) has CTk and runs everything.
pytest.importorskip("customtkinter")


def _make_instance(engine_id: str | None):
    """Throwaway ``UnifiedApp`` with just ``_current_engine_id`` stubbed."""
    from src.gui_unified import UnifiedApp

    inst = UnifiedApp.__new__(UnifiedApp)
    inst._current_engine_id = lambda: engine_id or ""  # type: ignore[attr-defined]
    return inst


def test_report_a_bug_opens_github_issue_url(monkeypatch):
    from src.auto_updater import APP_VERSION
    from src.gui_unified import UnifiedApp

    opened: list[str] = []
    monkeypatch.setattr(
        "webbrowser.open", lambda url, *a, **kw: opened.append(url) or True
    )

    inst = _make_instance(engine_id="chatterbox_fi")
    UnifiedApp._report_a_bug(inst)

    assert len(opened) == 1, "webbrowser.open must be called exactly once"
    url = opened[0]
    assert "github.com/MikkoNumminen/AudiobookMaker/issues/new" in url
    assert APP_VERSION in url

    parsed = urllib.parse.urlparse(url)
    qs = dict(urllib.parse.parse_qsl(parsed.query))
    # Body must include the "Environment" section header and mention the
    # OS line so reports never show up missing platform context.
    body = qs.get("body", "")
    assert "OS:" in body
    assert "platform" in body.lower() or "Environment" in body
    assert "chatterbox_fi" in body
    assert f"v{APP_VERSION}" in qs.get("title", "")


def test_report_a_bug_handles_missing_engine(monkeypatch):
    from src.gui_unified import UnifiedApp

    opened: list[str] = []
    monkeypatch.setattr(
        "webbrowser.open", lambda url, *a, **kw: opened.append(url) or True
    )

    inst = _make_instance(engine_id=None)
    UnifiedApp._report_a_bug(inst)

    assert len(opened) == 1
    parsed = urllib.parse.urlparse(opened[0])
    qs = dict(urllib.parse.parse_qsl(parsed.query))
    assert "(none)" in qs.get("body", "")


def test_report_a_bug_swallows_browser_launch_errors(monkeypatch):
    """A dead or missing browser must not crash the GUI."""
    from src.gui_unified import UnifiedApp

    def _boom(*_a, **_kw):
        raise RuntimeError("no default browser configured")

    monkeypatch.setattr("webbrowser.open", _boom)
    inst = _make_instance(engine_id="edge")

    # Must not raise.
    UnifiedApp._report_a_bug(inst)


def test_report_a_bug_does_not_actually_open_browser(monkeypatch):
    """Belt-and-braces: fail loudly if a refactor ever bypasses the mock."""
    from src.gui_unified import UnifiedApp

    mock_open = MagicMock(return_value=True)
    monkeypatch.setattr("webbrowser.open", mock_open)

    inst = _make_instance(engine_id="edge")
    UnifiedApp._report_a_bug(inst)

    mock_open.assert_called_once()
    args, _kwargs = mock_open.call_args
    assert args[0].startswith("https://github.com/MikkoNumminen/AudiobookMaker/")
