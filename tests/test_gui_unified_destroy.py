"""Unit tests for ``UnifiedApp.destroy()`` teardown behaviour.

These tests run on headless CI without a real Tk window — they bypass
``__init__`` entirely and stub only the attributes that ``destroy()`` reads.

The after-cancel sweep (step 1) only cancels callbacks whose info script name
contains "update" (the AppearanceModeTracker rescheduling loop).  It skips
CTk-internal callbacks (e.g. _windows_set_titlebar_icon) because cancelling
those causes a _tkinter.TclError: can't delete Tcl command during
super().destroy() widget teardown.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers — build a stub instance without spawning a real Tk window
# ---------------------------------------------------------------------------

_TRACKER_PATH = (
    "customtkinter.windows.widgets.appearance_mode"
    ".appearance_mode_tracker.AppearanceModeTracker"
)
_AUDIO_PLAYER_PATH = "src.gui_unified._audio_player"
_SUPER_DESTROY_PATH = "customtkinter.CTk.destroy"


def _make_stub():
    """Return a UnifiedApp instance with __init__ bypassed and all Tk
    attributes replaced by MagicMock objects.

    Only the attributes that ``destroy()`` actually reads are set here;
    everything else is irrelevant to these tests.
    """
    from src.gui_unified import UnifiedApp

    obj = object.__new__(UnifiedApp)
    obj.tk = MagicMock()
    obj.after_cancel = MagicMock()
    return obj


def _make_tk_call(ids, infos):
    """Build a tk.call side_effect that handles both:

    - ``("after", "info")`` → returns *ids* (str, tuple, or empty)
    - ``("after", "info", id)`` → returns the matching entry from *infos*

    *ids* may be a str (space-separated), tuple, or empty str/tuple.
    *infos* is a dict mapping each id to its info tuple, e.g.
    ``{"after#0": ("12345update", "timer")}``.
    """
    def side_effect(*args):
        if args == ("after", "info"):
            return ids
        if len(args) == 3 and args[0] == "after" and args[1] == "info":
            return infos.get(args[2], ())
        return MagicMock()
    return side_effect


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDestroyAfterCallbackCancellation:
    def test_destroy_cancels_update_callback_string_form(self):
        """When tk.call returns a space-separated string, the callback whose
        info contains 'update' is cancelled; others are skipped."""
        obj = _make_stub()
        infos = {
            "after#0": ("12345update", "timer"),
            "after#1": ("67890_windows_set_titlebar_icon", "timer"),
        }
        obj.tk.call.side_effect = _make_tk_call("after#0 after#1", infos)

        with patch(_AUDIO_PLAYER_PATH), patch(_SUPER_DESTROY_PATH):
            obj.destroy()

        obj.after_cancel.assert_called_once_with("after#0")

    def test_destroy_cancels_pending_after_callbacks_tuple_form(self):
        """Regression test for fix #1: a tuple return from tk.call must not
        become garbage via str(tuple).split() — the IDs must be iterated
        correctly regardless of whether they look like 'update' callbacks."""
        obj = _make_stub()
        infos = {
            "after#1": ("12345update", "timer"),
            "after#2": ("67890update", "timer"),
        }
        obj.tk.call.side_effect = _make_tk_call(("after#1", "after#2"), infos)

        with patch(_AUDIO_PLAYER_PATH), patch(_SUPER_DESTROY_PATH):
            obj.destroy()

        # Both IDs contain 'update' so both are cancelled.
        obj.after_cancel.assert_any_call("after#1")
        obj.after_cancel.assert_any_call("after#2")
        assert obj.after_cancel.call_count == 2

    def test_destroy_handles_empty_after_info(self):
        """An empty string return must not trigger any after_cancel calls."""
        obj = _make_stub()
        obj.tk.call.side_effect = _make_tk_call("", {})

        with patch(_AUDIO_PLAYER_PATH), patch(_SUPER_DESTROY_PATH):
            obj.destroy()

        obj.after_cancel.assert_not_called()

    def test_destroy_skips_non_update_callbacks(self):
        """CTk-internal callbacks (e.g. _windows_set_titlebar_icon) must NOT
        be cancelled — cancelling them causes TclError during super().destroy()
        because CTk tracks and tries to deletecommand() them itself."""
        obj = _make_stub()
        infos = {
            "after#1": ("12345_windows_set_titlebar_icon", "timer"),
        }
        obj.tk.call.side_effect = _make_tk_call(("after#1",), infos)

        with patch(_AUDIO_PLAYER_PATH), patch(_SUPER_DESTROY_PATH):
            obj.destroy()

        obj.after_cancel.assert_not_called()


class TestDestroyAppearanceModeTracker:
    def test_destroy_clears_appearance_mode_tracker_app_list(self):
        """destroy() must clear AppearanceModeTracker.app_list and set
        update_loop_running to False."""
        obj = _make_stub()
        obj.tk.call.side_effect = _make_tk_call((), {})

        mock_tracker = MagicMock()
        mock_tracker.app_list = MagicMock()

        with patch(_SUPER_DESTROY_PATH), patch(_AUDIO_PLAYER_PATH), \
             patch(
                 "customtkinter.windows.widgets.appearance_mode"
                 ".appearance_mode_tracker.AppearanceModeTracker",
                 mock_tracker,
             ):
            obj.destroy()

        mock_tracker.app_list.clear.assert_called_once()
        assert mock_tracker.update_loop_running is False

    def test_destroy_logs_warning_on_appearance_tracker_failure(self, caplog):
        """Regression test for fix #2: when AppearanceModeTracker raises (e.g.
        after a customtkinter upgrade renames the class), destroy() must log at
        WARNING level, not DEBUG."""
        obj = _make_stub()
        obj.tk.call.side_effect = _make_tk_call((), {})

        # Make the tracker's app_list.clear() blow up to trigger the except branch.
        mock_tracker = MagicMock()
        mock_tracker.app_list.clear.side_effect = AttributeError("no such attr in new CTk")

        with caplog.at_level(logging.WARNING, logger="src.gui_unified"), \
             patch(_SUPER_DESTROY_PATH), patch(_AUDIO_PLAYER_PATH), \
             patch(
                 "customtkinter.windows.widgets.appearance_mode"
                 ".appearance_mode_tracker.AppearanceModeTracker",
                 mock_tracker,
             ):
            obj.destroy()

        warning_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "AppearanceModeTracker" in r.message
        ]
        assert warning_records, (
            "Expected a WARNING about AppearanceModeTracker; got: "
            + str([r.message for r in caplog.records])
        )


class TestDestroyContinuesAfterPartialFailure:
    def test_destroy_continues_after_partial_failure(self):
        """If tk.call raises (Tcl already torn down), the audio player stop
        and super().destroy() must still be called."""
        obj = _make_stub()
        obj.tk.call.side_effect = RuntimeError("tcl gone")

        mock_player = MagicMock()
        mock_audio = MagicMock()
        mock_audio.get_player.return_value = mock_player

        with patch(_SUPER_DESTROY_PATH) as mock_super, \
             patch(_AUDIO_PLAYER_PATH, mock_audio):
            obj.destroy()

        mock_player.stop.assert_called_once()
        mock_super.assert_called_once()
