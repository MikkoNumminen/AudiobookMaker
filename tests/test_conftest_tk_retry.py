"""The Tk startup retry in conftest.py retries the runner flake and nothing else."""

from __future__ import annotations

import pytest

from tests.conftest import _tk_init_with_retry


class _TclError(Exception):
    pass


def _flaky_init(failures: list[str]):
    """An __init__ that raises each queued message once, then succeeds."""
    calls = []

    def init(self, *args, **kwargs):
        calls.append((args, kwargs))
        if failures:
            raise _TclError(failures.pop(0))

    return init, calls


INIT_TCL = (
    "Can't find a usable init.tcl in the following directories: "
    'C:/x/tcl/tcl8.6/init.tcl: couldn\'t read file "C:/x/tcl/tcl8.6/init.tcl": No error'
)


def test_init_tcl_failure_is_retried_once_with_the_same_arguments(monkeypatch):
    monkeypatch.setattr("tests.conftest.time.sleep", lambda _s: None)
    init, calls = _flaky_init([INIT_TCL])

    _tk_init_with_retry(init, _TclError)(object(), "arg", className="X")

    assert calls == [(("arg",), {"className": "X"})] * 2


def test_a_second_init_tcl_failure_is_not_swallowed(monkeypatch):
    monkeypatch.setattr("tests.conftest.time.sleep", lambda _s: None)
    init, calls = _flaky_init([INIT_TCL, INIT_TCL])

    with pytest.raises(_TclError, match="init.tcl"):
        _tk_init_with_retry(init, _TclError)(object())
    assert len(calls) == 2


def test_other_tcl_errors_fail_at_once(monkeypatch):
    monkeypatch.setattr("tests.conftest.time.sleep", lambda _s: None)
    init, calls = _flaky_init(["no display name and no $DISPLAY environment variable"])

    with pytest.raises(_TclError, match="DISPLAY"):
        _tk_init_with_retry(init, _TclError)(object())
    assert len(calls) == 1
