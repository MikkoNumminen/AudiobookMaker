"""Copyright-hygiene regression for :meth:`UnifiedApp._clone_voice_from_file`.

CLAUDE.md P0 rule: the raw source path / filename must never appear in
log output. The controller layer (:func:`run_clone_voice_job`) has its
own regression in :mod:`tests.test_gui_clone_voice`. This file covers
the layer above — the GUI wiring that turns a user-picked path into
analyze-pipeline inputs and forwards controller progress events to the
main log box.

Two guarantees pinned here:

1. :meth:`UnifiedApp._clone_voice_from_file` redacts the basename via
   :func:`safe_source_display_name` before handing the job to
   :func:`run_clone_voice_job`. The controller's ``wav_display_name``
   argument is the only identifier the controller ever sees — the
   raw path is kept inside :class:`CloneVoiceJobConfig` and never
   echoed outward.
2. The ``_progress`` closure that forwards events to the log helpers
   passes ``event.message`` through verbatim — no path information is
   spliced back in. Messages the controller produced with the redacted
   display name stay redacted when they land in the log box.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ``src.gui_unified`` imports customtkinter at module top; the Chatterbox
# venv intentionally omits CTk. Skip this file cleanly there.
pytest.importorskip("customtkinter")


def _make_unified_app(log_captures: list) -> object:
    """Return a minimal UnifiedApp stand-in without running __init__.

    Only attaches what :meth:`_clone_voice_from_file` actually touches
    in the unit under test — language, log helpers, voice-list refresh,
    and an immediate-mode ``after`` so the worker thread's callbacks
    execute synchronously during the test.
    """
    from src.gui_unified import UnifiedApp

    inst = UnifiedApp.__new__(UnifiedApp)
    inst._ui_lang = "fi"  # type: ignore[attr-defined]
    inst._append_log = lambda m: log_captures.append(("info", m))  # type: ignore[attr-defined]
    inst._append_log_error = lambda m: log_captures.append(("error", m))  # type: ignore[attr-defined]
    inst._append_log_warning = lambda m: log_captures.append(("warn", m))  # type: ignore[attr-defined]
    inst._append_log_success = lambda m: log_captures.append(("success", m))  # type: ignore[attr-defined]
    inst._refresh_voice_list = lambda: None  # type: ignore[attr-defined]

    # Immediate-mode after(): execute the callback synchronously so
    # forwarded log-writes land in log_captures before the test asserts.
    def _sync_after(_ms, fn=None, *args):
        if fn is not None:
            fn(*args)
        return "after-id"

    inst.after = _sync_after  # type: ignore[attr-defined]
    return inst


class TestCloneVoiceFromFileHygiene:
    def _sensitive_path(self, tmp_path: Path) -> Path:
        """Filename the ``safe_source_display_name`` redactor must flag."""
        p = tmp_path / "Some_Copyrighted_Audiobook_Ch1_Unabridged.m4b"
        p.write_bytes(b"fake audio data")
        return p

    def test_redacted_display_name_reaches_controller(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        """The controller receives ``wav_display_name='source_audio'``,
        not the raw basename. Proves the redaction happens at the GUI
        boundary, not inside run_clone_voice_job."""
        from src.gui_unified import UnifiedApp

        sensitive = self._sensitive_path(tmp_path)
        logs: list = []
        inst = _make_unified_app(logs)

        # Skip the "voice cloner not installed" early-out.
        inst._voice_cloner_installed = lambda: True  # type: ignore[attr-defined]

        # Pre-analyze modal: return a valid choice without opening Tk.
        class _FakePreAnalyze:
            def __init__(self, *a, **kw) -> None: ...
            def show(self):  # noqa: D401
                return ("fi", 1, None, None)

        monkeypatch.setattr(
            "src.gui_clone_voice.PreAnalyzeModal", _FakePreAnalyze,
        )

        # Capture the exact keyword arguments the controller receives.
        captured: dict = {}

        def _fake_run_job(config, **kwargs):
            captured["config"] = config
            captured["wav_display_name"] = kwargs.get("wav_display_name")
            captured["progress_cb"] = kwargs.get("progress_cb")
            # Don't drive any events — we care about the arguments here.
            return MagicMock(ok=True)

        monkeypatch.setattr(
            "src.gui_clone_voice.run_clone_voice_job", _fake_run_job,
        )

        # Force the worker thread to run synchronously so captured[...]
        # is populated by the time _clone_voice_from_file returns.
        class _InlineThread:
            def __init__(self, target=None, daemon=True, name="", **kw):
                self._target = target

            def start(self) -> None:
                if self._target is not None:
                    self._target()

        monkeypatch.setattr("threading.Thread", _InlineThread)

        UnifiedApp._clone_voice_from_file(inst, path_override=str(sensitive))

        assert captured["wav_display_name"] == "source_audio", (
            "Raw basename must be redacted to 'source_audio' before "
            "reaching the controller."
        )
        # And the raw path IS passed inside the config (the controller
        # needs it for subprocess input) but nowhere else.
        assert captured["config"].wav_path == sensitive

    def test_progress_forwarder_does_not_splice_in_raw_path(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        """The _progress closure forwards event.message verbatim — no
        path context is added. Events the controller emits with the
        redacted display name reach the log helpers unchanged."""
        from src.gui_clone_voice import (
            STAGE_ANALYZE,
            STAGE_DONE,
            STAGE_ERROR,
            STAGE_SPEAKER_DONE,
            STAGE_SPEAKER_SKIPPED,
            CloneVoiceProgress,
        )
        from src.gui_unified import UnifiedApp

        sensitive = self._sensitive_path(tmp_path)
        logs: list = []
        inst = _make_unified_app(logs)
        inst._voice_cloner_installed = lambda: True  # type: ignore[attr-defined]

        class _FakePreAnalyze:
            def __init__(self, *a, **kw) -> None: ...
            def show(self):
                return ("fi", 1, None, None)

        monkeypatch.setattr(
            "src.gui_clone_voice.PreAnalyzeModal", _FakePreAnalyze,
        )

        # Fire one event per severity tier so we exercise every branch
        # of _progress (info / error / success / warning).
        def _fake_run_job(config, **kwargs):
            progress_cb = kwargs["progress_cb"]
            progress_cb(CloneVoiceProgress(
                stage=STAGE_ANALYZE, message="Analysing source_audio…",
            ))
            progress_cb(CloneVoiceProgress(
                stage=STAGE_SPEAKER_DONE, message="Registered: Narrator 1",
            ))
            progress_cb(CloneVoiceProgress(
                stage=STAGE_SPEAKER_SKIPPED, message="Skipped speaker",
            ))
            progress_cb(CloneVoiceProgress(
                stage=STAGE_ERROR, message="Something went wrong",
            ))
            progress_cb(CloneVoiceProgress(
                stage=STAGE_DONE, message="Done.",
            ))
            return MagicMock(ok=True)

        monkeypatch.setattr(
            "src.gui_clone_voice.run_clone_voice_job", _fake_run_job,
        )

        class _InlineThread:
            def __init__(self, target=None, **kw):
                self._target = target

            def start(self) -> None:
                if self._target is not None:
                    self._target()

        monkeypatch.setattr("threading.Thread", _InlineThread)

        UnifiedApp._clone_voice_from_file(inst, path_override=str(sensitive))

        # Five events → five log entries, routed to the right severity.
        severities = [sev for sev, _ in logs]
        assert "info" in severities
        assert "success" in severities
        assert "warn" in severities
        assert "error" in severities

        # Critical check: no raw path / no raw stem / no raw basename
        # appears in any of the forwarded log messages.
        raw_path = str(sensitive)
        raw_stem = sensitive.stem
        raw_basename = sensitive.name
        for _sev, msg in logs:
            assert raw_path not in msg, f"Raw path leaked into log: {msg!r}"
            assert raw_stem not in msg, f"Stem leaked into log: {msg!r}"
            assert raw_basename not in msg, (
                f"Basename leaked into log: {msg!r}"
            )

    def test_worker_exception_logs_without_raw_path(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        """Even the fallback exception handler — the one that catches
        unexpected run_clone_voice_job crashes — must not name the source
        file in its error line."""
        from src.gui_unified import UnifiedApp

        sensitive = self._sensitive_path(tmp_path)
        logs: list = []
        inst = _make_unified_app(logs)
        inst._voice_cloner_installed = lambda: True  # type: ignore[attr-defined]

        class _FakePreAnalyze:
            def __init__(self, *a, **kw) -> None: ...
            def show(self):
                return ("fi", 1, None, None)

        monkeypatch.setattr(
            "src.gui_clone_voice.PreAnalyzeModal", _FakePreAnalyze,
        )

        def _crashing_job(config, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "src.gui_clone_voice.run_clone_voice_job", _crashing_job,
        )

        class _InlineThread:
            def __init__(self, target=None, **kw):
                self._target = target

            def start(self) -> None:
                if self._target is not None:
                    self._target()

        monkeypatch.setattr("threading.Thread", _InlineThread)

        UnifiedApp._clone_voice_from_file(inst, path_override=str(sensitive))

        error_msgs = [m for sev, m in logs if sev == "error"]
        assert error_msgs, "A crashing worker must surface an error line."
        for msg in error_msgs:
            assert str(sensitive) not in msg
            assert sensitive.stem not in msg
            assert sensitive.name not in msg


class TestScratchDirLocation:
    """CLAUDE.md: scratch files live under ``.local/clone_scratch/<ts>/``.

    The directory name is under ``.local/`` so ``.gitignore`` catches it
    and it can never be staged by accident. This test pins the path
    shape so a future refactor doesn't silently move scratch files to
    the repo root, the cwd, or ``/tmp`` where they might leak.
    """

    def test_scratch_dir_lives_under_dot_local_clone_scratch(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        from src.gui_unified import UnifiedApp

        sensitive = tmp_path / "input.wav"
        sensitive.write_bytes(b"data")
        logs: list = []
        inst = _make_unified_app(logs)
        inst._voice_cloner_installed = lambda: True  # type: ignore[attr-defined]

        class _FakePreAnalyze:
            def __init__(self, *a, **kw) -> None: ...
            def show(self):
                return ("fi", 1, None, None)

        monkeypatch.setattr(
            "src.gui_clone_voice.PreAnalyzeModal", _FakePreAnalyze,
        )

        captured: dict = {}

        def _fake_run_job(config, **kwargs):
            captured["scratch_dir"] = config.scratch_dir
            return MagicMock(ok=True)

        monkeypatch.setattr(
            "src.gui_clone_voice.run_clone_voice_job", _fake_run_job,
        )

        class _InlineThread:
            def __init__(self, target=None, **kw):
                self._target = target

            def start(self) -> None:
                if self._target is not None:
                    self._target()

        monkeypatch.setattr("threading.Thread", _InlineThread)

        # Pin cwd so the assertion's path prefix is deterministic.
        monkeypatch.chdir(tmp_path)

        UnifiedApp._clone_voice_from_file(inst, path_override=str(sensitive))

        scratch = captured["scratch_dir"]
        # Path shape: <cwd>/.local/clone_scratch/<timestamp>/
        parts = scratch.parts
        assert ".local" in parts, scratch
        assert "clone_scratch" in parts, scratch
        # Must be under tmp_path (our mocked cwd) — NOT /tmp, NOT home, NOT repo root.
        assert scratch.is_relative_to(tmp_path), scratch
