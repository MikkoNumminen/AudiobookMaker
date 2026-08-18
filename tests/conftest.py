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

import logging
import socket
import urllib.request

import pytest

# Eagerly populate the engine registry so every test sees the full
# engine set. Without this import, ``get_engine("chatterbox_grandmom")`` etc.
# return None in tests that do not themselves import the engine modules.
from src import engine_registry  # noqa: F401
from src.tts_base import _ALIASES, _REGISTRY


# ---------------------------------------------------------------------------
# Diagnostic-log isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="session")
def _isolate_diagnostic_log(tmp_path_factory):
    """Keep test-induced log records out of the real diagnostic log file.

    ``src.error_log.install()`` attaches a ``RotatingFileHandler`` to the ROOT
    logger pointed at ``~/.audiobookmaker/logs/audiobookmaker.log`` — the same
    file the GUI's "Save error log" button exports for field support. The GUI
    constructor calls ``install()``, so without this redirect every test that
    builds the GUI (or otherwise triggers ``install()``) appends its
    deterministic, fixture-induced ERROR/WARNING records to that real file.
    An exported field log then reads as ~95% test noise (``RuntimeError: boom``,
    "installer locked", the simulated ``LlamaModel`` import failure, ...),
    burying genuine field errors a user actually hit.

    Point the log at a throwaway session temp dir for the whole run, and strip
    any handler that ``install()`` attached so it cannot keep the real file —
    or this temp file — open past the session.
    """
    from src import error_log

    log_dir = tmp_path_factory.mktemp("diagnostic-log")
    saved = (error_log.LOG_DIR, error_log.LOG_FILE, error_log._installed)
    error_log.LOG_DIR = log_dir
    error_log.LOG_FILE = log_dir / "audiobookmaker.log"
    # Force a fresh install against the redirected path even if a prior import
    # already flipped the idempotency latch.
    error_log._installed = False
    try:
        yield
    finally:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, "name", None) == error_log._HANDLER_NAME:
                root.removeHandler(handler)
                handler.close()
        error_log.LOG_DIR, error_log.LOG_FILE, error_log._installed = saved


# ---------------------------------------------------------------------------
# Engine registry isolation
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_registry():
    """Isolate each test from the real engine registry (and alias map)."""
    saved = dict(_REGISTRY)
    saved_aliases = dict(_ALIASES)
    _REGISTRY.clear()
    _ALIASES.clear()
    yield
    _REGISTRY.clear()
    _ALIASES.clear()
    _REGISTRY.update(saved)
    _ALIASES.update(saved_aliases)


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


@pytest.fixture(autouse=True)
def _reset_piper_import_cache():
    """Reset the process-wide piper-import verdict (src.tts_piper) before each
    test, so a test that patches the import to fail can't poison a later test
    that expects the real import to succeed."""
    from src.tts_piper import _reset_piper_probe

    _reset_piper_probe()
    yield


@pytest.fixture(autouse=True)
def _isolate_job_state(tmp_path_factory, monkeypatch):
    """Point the saved-job file at a temp path for every test.

    Two reasons, both discovered the hard way:

    1. Tests must not write to the developer's real
       ``~/.audiobookmaker/last_job.json``. A test that starts the synthesis
       path records a job there, which then survives the run.
    2. A job file left behind by one test changes the behaviour of another:
       an "exit" event auto-resumes when a resumable job exists and reports a
       failure when one does not, so a stray file made the runner-exit tests
       pass alone and fail in the suite.
    """
    from src import job_state

    job_dir = tmp_path_factory.mktemp("job_state")
    monkeypatch.setattr(job_state, "JOB_FILE", job_dir / "last_job.json")
    yield
