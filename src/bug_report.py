"""Build a pre-filled GitHub issue URL so users can report bugs from
Settings without having to remember which build/OS/engine they were on.

The button in the Settings panel opens the returned URL in the user's
default browser. GitHub renders the query-string title + body into a new
issue form so the user only has to add what went wrong.
"""

from __future__ import annotations

import platform
import urllib.parse

from src.auto_updater import GITHUB_REPO

_ISSUE_BASE_URL = f"https://github.com/{GITHUB_REPO}/issues/new"


def build_bug_report_url(
    *,
    app_version: str,
    engine_id: str | None,
    os_platform: str | None = None,
) -> str:
    """Build a GitHub "new issue" URL with title + body pre-filled.

    Args:
        app_version: The running app's version string (e.g. ``"3.9.1"``).
        engine_id: Active engine id (e.g. ``"chatterbox_fi"``) or
            ``None`` / empty string if no engine is selected.
        os_platform: Platform description, defaults to
            ``platform.platform()``. Overridable for tests.

    Returns:
        A fully-formed ``https://github.com/.../issues/new?title=…&body=…``
        URL. Title and body are URL-encoded; opening the URL drops the
        user into GitHub's new-issue form with those fields populated.
    """
    if os_platform is None:
        os_platform = platform.platform()
    engine_display = engine_id if engine_id else "(none)"

    title = f"[v{app_version}] "
    body = (
        "## What happened?\n\n"
        "(Please describe the bug — what you did, what you expected, "
        "what actually happened.)\n\n"
        "## Environment\n\n"
        f"- App version: v{app_version}\n"
        f"- OS: {os_platform}\n"
        f"- Engine: {engine_display}\n"
    )
    query = urllib.parse.urlencode({"title": title, "body": body})
    return f"{_ISSUE_BASE_URL}?{query}"
