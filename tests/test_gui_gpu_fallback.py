"""The GUI tells the user when the runner fell back to the CPU.

When the GPU has no kernels in the installed torch build, the runner keeps
converting on the CPU — 10x slower or worse. A yellow log line is easy to
miss, so the bridge turns the runner's "[setup] gpu fallback" line into a
``gpu_fallback`` event and the GUI shows it in a dialog, in the UI language,
with the fix.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.launcher_bridge import ChatterboxLineParser, ProgressEvent
from src.tts_base import _REGISTRY

_LINE = ("[setup] gpu fallback: gpu=NVIDIA GeForce RTX 5070; capability=12.0; "
         "torch=2.6.0+cu124")


@pytest.fixture(scope="module")
def _shared_app():
    from src.tts_edge import EdgeTTSEngine
    from src.tts_piper import PiperTTSEngine
    from src.gui_unified import UnifiedApp

    if "edge" not in _REGISTRY:
        _REGISTRY["edge"] = EdgeTTSEngine
    if "piper" not in _REGISTRY:
        _REGISTRY["piper"] = PiperTTSEngine

    instance = UnifiedApp()
    instance.update_idletasks()
    yield instance
    instance.destroy()


@pytest.fixture
def app(_shared_app, clean_registry):
    _shared_app._synth_running = True
    _shared_app._is_sample_run = False
    _shared_app._chatterbox_runner = None
    while not _shared_app._event_queue.empty():
        _shared_app._event_queue.get_nowait()
    yield _shared_app
    _shared_app._ui_lang = "fi"


def _dialog_for(app, line: str):
    """Pump one parsed line; return the (title, message) of the dialog shown."""
    app._event_queue.put(ChatterboxLineParser().parse(line))
    deferred: list = []
    with patch.object(app, "after", side_effect=lambda *a, **k: deferred.append(a)):
        app._pump_events()
    shown: list = []
    with patch("src.gui_unified.messagebox.showwarning",
               side_effect=lambda title, msg, **k: shown.append((title, msg))):
        for args in deferred:
            if args[0] == 0:
                args[1]()
    return shown


def test_the_bridge_parses_the_runner_line() -> None:
    ev = ChatterboxLineParser().parse(_LINE)
    assert ev == ProgressEvent(
        kind="gpu_fallback", gpu_name="NVIDIA GeForce RTX 5070",
        gpu_capability="12.0", torch_build="2.6.0+cu124", raw_line=_LINE,
    )


@pytest.mark.parametrize(
    "lang, words",
    [("fi", ["suorittimella", "Korjaa", "Asenna moottoreita"]),
     ("en", ["CPU", "Repair", "Install engines"])],
)
def test_dialog_names_the_gpu_the_build_and_the_fix(app, lang, words) -> None:
    app._ui_lang = lang
    shown = _dialog_for(app, _LINE)
    assert len(shown) == 1
    _title, message = shown[0]
    assert "NVIDIA GeForce RTX 5070" in message
    assert "12.0" in message
    assert "2.6.0+cu124" in message
    for word in words:
        assert word in message


def test_other_setup_lines_show_no_dialog(app) -> None:
    assert _dialog_for(app, "[setup] device=cuda") == []
