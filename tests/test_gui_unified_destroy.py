"""Unit tests for ``UnifiedApp.destroy()`` teardown behaviour.

These tests run on headless CI without a real Tk window — they bypass
``__init__`` entirely and stub only the attributes that ``destroy()`` reads.

The after-cancel sweep (step 1) cancels exactly the IDs recorded in
``_scheduled_afters`` — the set populated by UnifiedApp's overridden
``after()`` / ``after_idle()`` methods.  It never consults ``tk.call``
for pending IDs, and it never guesses by script-name substring.
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


def _make_stub(scheduled_afters=None):
    """Return a UnifiedApp instance with __init__ bypassed and all Tk
    attributes replaced by MagicMock objects.

    Only the attributes that ``destroy()`` actually reads are set here;
    everything else is irrelevant to these tests.

    *scheduled_afters* seeds ``_scheduled_afters``; defaults to an empty set.
    """
    from src.gui_unified import UnifiedApp

    obj = object.__new__(UnifiedApp)
    obj.tk = MagicMock()
    obj.after_cancel = MagicMock()
    obj._scheduled_afters = set(scheduled_afters) if scheduled_afters else set()
    return obj


# ---------------------------------------------------------------------------
# Tests — after() / after_idle() tracking
# ---------------------------------------------------------------------------


class TestAfterTracking:
    def test_after_method_records_id(self):
        """after() must record the returned ID in ``_scheduled_afters``."""
        from src.gui_unified import UnifiedApp

        obj = object.__new__(UnifiedApp)
        obj._scheduled_afters = set()

        fake_id = "after#42"
        with patch("customtkinter.CTk.after", return_value=fake_id):
            result = obj.after(0, lambda: None)

        assert result == fake_id
        assert fake_id in obj._scheduled_afters

    def test_after_method_handles_missing_set(self):
        """after() must work defensively even if __init__ has not yet set
        ``_scheduled_afters`` (CTk calls after() during its own __init__)."""
        from src.gui_unified import UnifiedApp

        obj = object.__new__(UnifiedApp)
        # Deliberately do NOT set _scheduled_afters.

        fake_id = "after#99"
        with patch("customtkinter.CTk.after", return_value=fake_id):
            result = obj.after(100, lambda: None)

        assert result == fake_id
        assert fake_id in obj._scheduled_afters

    def test_after_idle_records_id(self):
        """after_idle() must record the returned ID in ``_scheduled_afters``."""
        from src.gui_unified import UnifiedApp

        obj = object.__new__(UnifiedApp)
        obj._scheduled_afters = set()

        fake_id = "after#idle1"
        with patch("customtkinter.CTk.after_idle", return_value=fake_id):
            result = obj.after_idle(lambda: None)

        assert result == fake_id
        assert fake_id in obj._scheduled_afters

    def test_after_none_id_not_recorded(self):
        """If super().after() returns None/empty, nothing is added to the set."""
        from src.gui_unified import UnifiedApp

        obj = object.__new__(UnifiedApp)
        obj._scheduled_afters = set()

        with patch("customtkinter.CTk.after", return_value=None):
            obj.after(0, lambda: None)

        assert len(obj._scheduled_afters) == 0


# ---------------------------------------------------------------------------
# Tests — destroy() step 1: cancel from _scheduled_afters
# ---------------------------------------------------------------------------


class TestDestroyAfterCallbackCancellation:
    def test_destroy_cancels_all_scheduled_afters(self):
        """Every ID in ``_scheduled_afters`` must be cancelled by destroy()."""
        obj = _make_stub(scheduled_afters={"after#0", "after#1", "after#2"})

        with patch(_AUDIO_PLAYER_PATH), patch(_SUPER_DESTROY_PATH):
            obj.destroy()

        assert obj.after_cancel.call_count == 3
        called_with = {c.args[0] for c in obj.after_cancel.call_args_list}
        assert called_with == {"after#0", "after#1", "after#2"}

    def test_destroy_clears_scheduled_afters_after_cancel(self):
        """``_scheduled_afters`` must be empty after destroy() completes."""
        obj = _make_stub(scheduled_afters={"after#0"})

        with patch(_AUDIO_PLAYER_PATH), patch(_SUPER_DESTROY_PATH):
            obj.destroy()

        assert obj._scheduled_afters == set()

    def test_destroy_handles_empty_scheduled_afters(self):
        """If ``_scheduled_afters`` is empty, no after_cancel calls are made."""
        obj = _make_stub(scheduled_afters=set())

        with patch(_AUDIO_PLAYER_PATH), patch(_SUPER_DESTROY_PATH):
            obj.destroy()

        obj.after_cancel.assert_not_called()

    def test_destroy_tolerates_already_fired_id(self):
        """If after_cancel raises for an already-fired ID, destroy() must
        continue and still call super().destroy()."""
        obj = _make_stub(scheduled_afters={"after#expired", "after#live"})
        obj.after_cancel.side_effect = lambda id_: (
            (_ for _ in ()).throw(Exception("already fired"))
            if id_ == "after#expired"
            else None
        )

        with patch(_AUDIO_PLAYER_PATH), patch(_SUPER_DESTROY_PATH) as mock_super:
            obj.destroy()  # must not raise

        mock_super.assert_called_once()

    def test_destroy_does_not_touch_tk_call_for_after_info(self):
        """destroy() must NOT query tk.call('after', 'info') — that was the
        old brittle heuristic; the new implementation only uses _scheduled_afters."""
        obj = _make_stub(scheduled_afters=set())

        with patch(_AUDIO_PLAYER_PATH), patch(_SUPER_DESTROY_PATH):
            obj.destroy()

        # Ensure no "after", "info" introspection calls happened.
        for c in obj.tk.call.call_args_list:
            args = c.args
            assert not (len(args) >= 2 and args[0] == "after" and args[1] == "info"), (
                f"destroy() must not call tk.call('after', 'info', ...) — found: {c}"
            )


# ---------------------------------------------------------------------------
# Tests — destroy() step 2: AppearanceModeTracker cleanup
# ---------------------------------------------------------------------------


class TestDestroyAppearanceModeTracker:
    def test_destroy_clears_appearance_mode_tracker_app_list(self):
        """destroy() must clear AppearanceModeTracker.app_list and set
        update_loop_running to False."""
        obj = _make_stub()

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


# ---------------------------------------------------------------------------
# Tests — destroy() resilience
# ---------------------------------------------------------------------------


class TestDestroyContinuesAfterPartialFailure:
    def test_destroy_continues_after_partial_failure(self):
        """If after_cancel raises for every ID in ``_scheduled_afters``
        (simulating Tcl already torn down), the audio player stop and
        super().destroy() must still be called."""
        obj = _make_stub(scheduled_afters={"after#0"})
        obj.after_cancel.side_effect = RuntimeError("tcl gone")

        mock_player = MagicMock()
        mock_audio = MagicMock()
        mock_audio.get_player.return_value = mock_player

        with patch(_SUPER_DESTROY_PATH) as mock_super, \
             patch(_AUDIO_PLAYER_PATH, mock_audio):
            obj.destroy()

        mock_player.stop.assert_called_once()
        mock_super.assert_called_once()
