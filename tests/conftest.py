"""Shared pytest fixtures for the AudiobookMaker test suite.

Also globally blocks outbound network access during tests. A test that
accidentally hits the real Edge-TTS service or Hugging Face Hub would
flake CI on every connectivity hiccup, so we hard-fail any un-marked
test that opens a non-loopback socket or calls ``urllib.request.urlopen``.

Tests that genuinely need network (like the Edge-TTS smoke suite) must
carry ``@pytest.mark.network`` — that marker opts out of the guard.

Loopback traffic (127.0.0.1 / ::1) stays allowed because pytest-asyncio,
http.server-based fixtures, and similar infrastructure rely on it.
"""

from __future__ import annotations

import os
import socket
import sys
import urllib.request

import pytest


# ---------------------------------------------------------------------------
# CI-only force-exit after pytest finishes printing
# ---------------------------------------------------------------------------
#
# Many GUI test files transitively import customtkinter, whose import-
# time Tk machinery leaves non-daemon threads alive on headless Windows
# CI runners. After all tests pass and pytest prints its summary, Python
# hangs indefinitely waiting for those Tk threads to die — until the
# CI runner kills the step. Symptoms: release v3.11.1 builds 25615367406
# / 25615715187 / 25615954113 / 25616723346 all printed N passed and
# then either crashed on a late pytest-timeout firing or hung until
# cancelled.
#
# pytest_unconfigure fires AFTER terminal_summary, so by the time we
# os._exit, all stdout/stderr is already flushed and any F/E from a
# real failure is visible in the build log. We only force-exit on a
# zero exit status — a real failure should still be allowed to exit
# normally so its full traceback prints before pytest hands back to
# the shell.
#
# The hook is gated on CI=true (set automatically on every GitHub
# Actions runner) so local dev keeps the natural exit path and the
# pytest-timeout safety net in pytest.ini.
def pytest_unconfigure(config):
    if os.environ.get("CI", "").lower() not in ("true", "1"):
        return
    exit_status = getattr(config, "_test_exit_status", 0)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(exit_status))


def pytest_sessionfinish(session, exitstatus):
    """Capture the final exit status for pytest_unconfigure to use.

    pytest_unconfigure does not receive the exit status directly, but
    it fires later in the lifecycle (after terminal summary). Stash
    the status on config here so the unconfigure hook can read it.
    """
    if os.environ.get("CI", "").lower() in ("true", "1"):
        session.config._test_exit_status = int(exitstatus)

# Eagerly populate the engine registry so every test sees the full
# engine set. Without this import, ``get_engine("chatterbox_fi")`` etc.
# return None in tests that do not themselves import the engine modules.
from src import engine_registry  # noqa: F401
from src.tts_base import _REGISTRY


# ---------------------------------------------------------------------------
# Engine registry isolation
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_registry():
    """Isolate each test from the real engine registry."""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# Network-access guard
# ---------------------------------------------------------------------------


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


def _is_loopback(address) -> bool:
    """Return True if *address* targets the local machine."""
    if address is None:
        return True
    if isinstance(address, (tuple, list)) and address:
        host = address[0]
    else:
        host = address
    if not isinstance(host, str):
        return False
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


class _BlockedSocket(socket.socket):
    """socket.socket subclass that refuses non-loopback connections."""

    def connect(self, address):  # type: ignore[override]
        if not _is_loopback(address):
            raise RuntimeError(
                "network access blocked in tests "
                f"(connect to {address!r}); mark the test with "
                "@pytest.mark.network if it really needs the internet"
            )
        return super().connect(address)

    def connect_ex(self, address):  # type: ignore[override]
        if not _is_loopback(address):
            raise RuntimeError(
                "network access blocked in tests "
                f"(connect_ex to {address!r}); mark the test with "
                "@pytest.mark.network if it really needs the internet"
            )
        return super().connect_ex(address)


def _blocked_urlopen(*args, **kwargs):
    url = args[0] if args else kwargs.get("url")
    raise RuntimeError(
        "network access blocked in tests "
        f"(urlopen to {url!r}); mark the test with "
        "@pytest.mark.network if it really needs the internet"
    )


@pytest.fixture(autouse=True)
def _block_network(request):
    """Block outbound network calls unless the test is marked ``network``.

    We swap ``socket.socket`` for a subclass that refuses non-loopback
    ``connect()``/``connect_ex()`` calls, and we replace
    ``urllib.request.urlopen`` with a version that raises. Both are
    restored on teardown so the override never leaks across tests.
    """
    if request.node.get_closest_marker("network"):
        yield
        return

    real_socket = socket.socket
    real_urlopen = urllib.request.urlopen

    socket.socket = _BlockedSocket  # type: ignore[assignment]
    urllib.request.urlopen = _blocked_urlopen  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket = real_socket  # type: ignore[assignment]
        urllib.request.urlopen = real_urlopen  # type: ignore[assignment]
