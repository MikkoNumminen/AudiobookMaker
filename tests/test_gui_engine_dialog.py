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

        # Attach before the call so mock_calls captures the order.
        manager = MagicMock()
        manager.attach_mock(dialog.clipboard_clear, "clear")
        manager.attach_mock(dialog.clipboard_append, "append")
        manager.attach_mock(dialog.update_idletasks, "update")

        with patch("src.gui_engine_dialog.messagebox.showerror") as showerror:
            dialog._handle_progress(p)

        # All three clipboard ops must fire.
        dialog.clipboard_clear.assert_called_once()
        dialog.clipboard_append.assert_called_once_with(error_text)
        dialog.update_idletasks.assert_called_once()

        # Order: clear before append before update_idletasks.
        assert manager.mock_calls == [
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


# ---------------------------------------------------------------------------
# Repair path: the Repair button, _on_repair, start_repair, and the
# install-vs-repair operation selection inside _on_install's worker.
# ---------------------------------------------------------------------------


class _InlineThread:
    """Thread stand-in that runs the target synchronously on ``.start()``.

    Lets us drive ``_on_install``'s background worker inline so we can assert
    which installer entry point (install vs force_reinstall) it invoked,
    without real threads, Tk, or a display.
    """

    def __init__(self, target=None, daemon=None, name=None):
        self._target = target

    def start(self):
        if self._target:
            self._target()

    def is_alive(self):
        return False


def _make_install_dialog(ui_lang: str = "fi") -> EngineManagerDialog:
    """Bare dialog with only the attributes ``_on_install`` touches.

    No ``_close_btn`` is set, so ``_on_install`` takes the embedded-view grid
    branch; every widget is a MagicMock and ``after`` is stubbed so no Tk
    event loop runs.
    """
    dialog = EngineManagerDialog.__new__(EngineManagerDialog)
    dialog._ui_lang = ui_lang
    dialog._strings = _ENGINE_MGR_STRINGS[ui_lang]
    dialog._progress_frame = MagicMock()
    dialog._progress_step_lbl = MagicMock()
    dialog._progress_msg_lbl = MagicMock()
    dialog._progress_bar = MagicMock()
    dialog._progress_row = 3
    dialog._engine_rows = {}
    dialog._progress_queue = MagicMock()
    dialog.after = MagicMock()
    return dialog


class TestRepairPath:
    """One-click repair wiring: the GUI's only route to force_reinstall."""

    def test_on_repair_calls_on_install_with_repair_true(self):
        dialog = EngineManagerDialog.__new__(EngineManagerDialog)
        dialog._on_install = MagicMock()
        installer = MagicMock()

        dialog._on_repair(installer)

        dialog._on_install.assert_called_once_with(installer, repair=True)

    def test_start_repair_resolves_installer_and_repairs(self):
        dialog = EngineManagerDialog.__new__(EngineManagerDialog)
        dialog._ui_lang = "en"
        dialog._on_repair = MagicMock()
        fake_installer = MagicMock()

        with patch(
            "src.engine_installer.get_installer", return_value=fake_installer
        ) as get_installer:
            dialog.start_repair("chatterbox_grandmom")

        get_installer.assert_called_once_with("chatterbox_grandmom")
        # ui_lang is propagated so the installer's error strings localize.
        assert fake_installer.ui_lang == "en"
        dialog._on_repair.assert_called_once_with(fake_installer)

    def test_start_repair_unknown_engine_noops(self):
        dialog = EngineManagerDialog.__new__(EngineManagerDialog)
        dialog._ui_lang = "fi"
        dialog._on_repair = MagicMock()

        with patch("src.engine_installer.get_installer", return_value=None):
            dialog.start_repair("does_not_exist")

        dialog._on_repair.assert_not_called()

    def test_on_install_repair_true_runs_force_reinstall(self):
        dialog = _make_install_dialog()
        installer = MagicMock()
        installer.engine_id = "chatterbox_grandmom"
        installer.check_prerequisites.return_value = []

        with patch("src.gui_engine_dialog.threading.Thread", _InlineThread):
            dialog._on_install(installer, repair=True)

        installer.force_reinstall.assert_called_once()
        installer.install.assert_not_called()

    def test_on_install_default_runs_plain_install(self):
        dialog = _make_install_dialog()
        installer = MagicMock()
        installer.engine_id = "piper"
        installer.check_prerequisites.return_value = []

        with patch("src.gui_engine_dialog.threading.Thread", _InlineThread):
            dialog._on_install(installer)

        installer.install.assert_called_once()
        installer.force_reinstall.assert_not_called()

    def test_on_install_prereq_failure_aborts_before_worker(self):
        dialog = _make_install_dialog()
        installer = MagicMock()
        installer.check_prerequisites.return_value = ["No NVIDIA GPU"]

        with patch("src.gui_engine_dialog.messagebox.showerror") as showerror, \
             patch("src.gui_engine_dialog.threading.Thread", _InlineThread):
            dialog._on_install(installer, repair=True)

        showerror.assert_called_once()
        installer.install.assert_not_called()
        installer.force_reinstall.assert_not_called()


class TestIsInstalling:
    """is_installing() gates the main window's Convert during an install."""

    def test_reflects_thread_state(self):
        dialog = EngineManagerDialog.__new__(EngineManagerDialog)

        dialog._install_thread = None
        assert dialog.is_installing() is False

        alive = MagicMock()
        alive.is_alive.return_value = True
        dialog._install_thread = alive
        assert dialog.is_installing() is True

        dead = MagicMock()
        dead.is_alive.return_value = False
        dialog._install_thread = dead
        assert dialog.is_installing() is False


class TestSynthRunningBlocksInstall:
    """_on_install/_on_repair must refuse while the host has a synth running —
    the reverse of the Convert-during-install corruption."""

    def test_install_blocked_while_synth_running(self):
        dialog = EngineManagerDialog.__new__(EngineManagerDialog)
        dialog._ui_lang = "fi"
        dialog._strings = _ENGINE_MGR_STRINGS["fi"]
        host = MagicMock()
        host._synth_running = True
        dialog._host = host
        installer = MagicMock()

        with patch("src.gui_engine_dialog.messagebox.showerror") as showerror:
            dialog._on_install(installer)

        showerror.assert_called_once()
        # Blocked before doing any installer work.
        installer.check_prerequisites.assert_not_called()
        installer.install.assert_not_called()
        installer.force_reinstall.assert_not_called()

    def test_install_proceeds_when_no_synth_running(self):
        dialog = EngineManagerDialog.__new__(EngineManagerDialog)
        dialog._ui_lang = "fi"
        dialog._strings = _ENGINE_MGR_STRINGS["fi"]
        host = MagicMock()
        host._synth_running = False
        dialog._host = host
        installer = MagicMock()
        installer.check_prerequisites.return_value = ["stop here"]

        with patch("src.gui_engine_dialog.messagebox.showerror"):
            dialog._on_install(installer)

        # Got past the synth guard to the prereq check.
        installer.check_prerequisites.assert_called_once()
