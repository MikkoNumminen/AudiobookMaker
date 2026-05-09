"""Unit tests for EngineManagerDialog._handle_progress clipboard pre-load.

All tests use Option A: EngineManagerDialog.__new__ to bypass __init__ and
avoid any Tk/CTk machinery.  No real window is created; no display is needed.
"""

from unittest.mock import MagicMock, call, patch

import pytest

from src.engine_installer import InstallProgress
from src.gui_engine_dialog import EngineManagerDialog, _ENGINE_MGR_STRINGS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dialog(ui_lang: str = "fi") -> EngineManagerDialog:
    """Return a bare EngineManagerDialog instance with no Tk window.

    Only the attributes that ``_handle_progress`` actually reads are set.
    Clipboard and lifecycle methods are replaced with MagicMocks so we can
    assert call counts without touching any real GUI machinery.
    """
    dialog = EngineManagerDialog.__new__(EngineManagerDialog)
    dialog._ui_lang = ui_lang
    dialog._strings = _ENGINE_MGR_STRINGS[ui_lang]
    dialog._install_thread = None
    dialog.clipboard_clear = MagicMock()
    dialog.clipboard_append = MagicMock()
    dialog.update_idletasks = MagicMock()
    dialog._install_finished = MagicMock()
    return dialog


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHandleProgressClipboardPreLoad:
    """Clipboard pre-load behaviour on install error."""

    def test_clipboard_ops_called_in_order(self):
        """clear → append → update_idletasks, then showerror, then _install_finished."""
        dialog = _make_dialog()
        error_text = "ImportError: DLL load failed: ..."
        p = InstallProgress(error=error_text)

        with patch("src.gui_engine_dialog.messagebox.showerror") as showerror:
            dialog._handle_progress(p)

        # All three clipboard ops must fire.
        dialog.clipboard_clear.assert_called_once()
        dialog.clipboard_append.assert_called_once_with(error_text)
        dialog.update_idletasks.assert_called_once()

        # Order: clear before append before update_idletasks.
        manager = MagicMock()
        manager.attach_mock(dialog.clipboard_clear, "clear")
        manager.attach_mock(dialog.clipboard_append, "append")
        manager.attach_mock(dialog.update_idletasks, "update")
        # Re-run on a fresh dialog to capture ordered calls.
        dialog2 = _make_dialog()
        mgr2 = MagicMock()
        mgr2.attach_mock(dialog2.clipboard_clear, "clear")
        mgr2.attach_mock(dialog2.clipboard_append, "append")
        mgr2.attach_mock(dialog2.update_idletasks, "update")
        with patch("src.gui_engine_dialog.messagebox.showerror"):
            dialog2._handle_progress(p)
        assert mgr2.mock_calls == [
            call.clear(),
            call.append(error_text),
            call.update(),
        ]

        # messagebox was called exactly once.
        showerror.assert_called_once()

    def test_messagebox_body_contains_clipboard_hint_and_raw_error(self):
        """showerror body must include localised clipboard hint AND the raw error."""
        dialog = _make_dialog(ui_lang="fi")
        error_text = "RuntimeError: CUDA out of memory"
        p = InstallProgress(error=error_text)

        with patch("src.gui_engine_dialog.messagebox.showerror") as showerror:
            dialog._handle_progress(p)

        _, kwargs = showerror.call_args[0], showerror.call_args[1] if showerror.call_args[1] else {}
        # body is the second positional argument
        body = showerror.call_args[0][1]
        assert _ENGINE_MGR_STRINGS["fi"]["error_on_clipboard"] in body
        assert error_text in body

    def test_clipboard_failure_falls_through_to_messagebox(self):
        """If clipboard_clear raises, the messagebox must still be shown."""
        dialog = _make_dialog()
        dialog.clipboard_clear.side_effect = RuntimeError("no display")
        p = InstallProgress(error="some traceback")

        with patch("src.gui_engine_dialog.messagebox.showerror") as showerror:
            dialog._handle_progress(p)  # must not propagate

        showerror.assert_called_once()
        dialog._install_finished.assert_called_once()

    def test_no_error_no_clipboard_ops_but_done_shows_info(self):
        """p.error falsy + p.done=True → no clipboard, showinfo fires."""
        dialog = _make_dialog()
        p = InstallProgress(done=True)

        with patch("src.gui_engine_dialog.messagebox.showerror") as showerror, \
             patch("src.gui_engine_dialog.messagebox.showinfo") as showinfo:
            dialog._handle_progress(p)

        dialog.clipboard_clear.assert_not_called()
        dialog.clipboard_append.assert_not_called()
        dialog.update_idletasks.assert_not_called()
        showerror.assert_not_called()
        showinfo.assert_called_once()
        dialog._install_finished.assert_called_once()

    def test_messagebox_body_uses_finnish_clipboard_string(self):
        """ui_lang='fi' → body contains the Finnish clipboard-hint string."""
        dialog = _make_dialog(ui_lang="fi")
        p = InstallProgress(error="traceback fi")
        fi_hint = "Virhe on kopioitu leikepöydälle"

        with patch("src.gui_engine_dialog.messagebox.showerror") as showerror:
            dialog._handle_progress(p)

        body = showerror.call_args[0][1]
        assert fi_hint in body

    def test_messagebox_body_uses_english_clipboard_string(self):
        """ui_lang='en' → body contains the English clipboard-hint string."""
        dialog = _make_dialog(ui_lang="en")
        p = InstallProgress(error="traceback en")
        en_hint = "Error copied to clipboard"

        with patch("src.gui_engine_dialog.messagebox.showerror") as showerror:
            dialog._handle_progress(p)

        body = showerror.call_args[0][1]
        assert en_hint in body
