"""Unified AudiobookMaker GUI — single window replacing gui.py and launcher.py.

Combines the simple launcher's one-click workflow with the advanced settings
panel from the original gui.py. All engines are dispatched through the same
queue-based event loop: Chatterbox runs as a subprocess via launcher_bridge,
while Edge-TTS / Piper / VoxCPM2 run in-process on a background thread.

Entry point::

    python -m src.gui_unified
    # or: from src.gui_unified import run; run()

Supports Finnish and English UI languages.
Uses CustomTkinter for a modern look with dark/light mode support.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk

from src import app_config
from src.auto_updater import check_for_update, download_update, apply_update, APP_VERSION, UpdateInfo
from src.gui_synth_mixin import SynthMixin
from src.gui_update_mixin import UpdateMixin
from src.ffmpeg_path import get_ffmpeg_dir, setup_ffmpeg_path
from src.launcher_bridge import ChatterboxRunner, ProgressEvent, resolve_chatterbox_python
from src.pdf_parser import parse_pdf
from src.tts_base import EngineStatus, TTSEngine, Voice, get_engine, list_engines
from src.tts_engine import TTSConfig, chapters_to_speech

# Import engine modules for their register_engine() side effects.
from src import tts_edge  # noqa: F401
from src import tts_piper  # noqa: F401

# VoxCPM2 is developer-only — don't show it in the installed exe.
if not getattr(sys, "frozen", False):
    try:
        from src import tts_voxcpm  # noqa: F401
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Repo root (needed for Chatterbox runner script resolution)
# ---------------------------------------------------------------------------

# In dev mode, this is the repo root. In a PyInstaller bundle, this is
# the _MEIPASS temp directory (or the app directory for onedir builds).
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _APP_ROOT = Path(sys._MEIPASS)
else:
    _APP_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _APP_ROOT

# ---------------------------------------------------------------------------
# CustomTkinter appearance
# ---------------------------------------------------------------------------

ctk.set_appearance_mode("system")  # follows OS dark/light
ctk.set_default_color_theme("blue")

def _detect_system_language() -> str:
    """Return 'fi' if the system locale is Finnish, 'en' otherwise."""
    import locale
    try:
        lang = locale.getdefaultlocale()[0] or ""
        if lang.lower().startswith("fi"):
            return "fi"
    except Exception:
        pass
    return "en"


# ---------------------------------------------------------------------------
# UI constants
# ---------------------------------------------------------------------------

WINDOW_TITLE = f"AudiobookMaker v{APP_VERSION}"
WINDOW_MIN_W = 780
WINDOW_MIN_H = 860

LANGUAGES = {
    "Suomi": "fi",
    "English": "en",
}

SPEED_OPTIONS = {
    "fi": {
        "Hidas (-25%)": "-25%",
        "Normaali": "+0%",
        "Nopea (+25%)": "+25%",
        "Erittäin nopea (+50%)": "+50%",
    },
    "en": {
        "Slow (-25%)": "-25%",
        "Normal": "+0%",
        "Fast (+25%)": "+25%",
        "Very fast (+50%)": "+50%",
    },
}

OUTPUT_MODES = {
    "fi": {
        "Yksi MP3": "single",
        "Yksi MP3 per luku": "chapters",
    },
    "en": {
        "Single MP3": "single",
        "One MP3 per chapter": "chapters",
    },
}

# Short sample sentences for voice preview.
_SAMPLE_TEXT = {
    "fi": "Tämä on ääninäyte valitulla äänellä.",
    "en": "This is a voice sample with the selected voice.",
}

# Engine status colours.
_CLR_READY = "green"
_CLR_NEEDS_SETUP = "orange"
_CLR_UNAVAILABLE = "red"

# ---------------------------------------------------------------------------
# Translatable UI strings
# ---------------------------------------------------------------------------

_STRINGS = {
    "fi": {
        "window_title": "AudiobookMaker",
        "tab_pdf": "PDF-tiedosto",
        "tab_text": "Teksti",
        "text_placeholder": "Kirjoita tai liitä teksti tähän...",
        "settings_frame": "Asetukset",
        "engine_label": "Moottori:",
        "install_engines": "Asenna moottoreita\u2026",
        "status_ready": "Valmis",
        "language_label": "Kieli:",
        "speed_label": "Nopeus:",
        "voice_label": "Ääni:",
        "test_voice": "Testaa ääni",
        "ref_audio_label": "Ref. ääni:",
        "browse": "Selaa\u2026",
        "clear": "Tyhjennä",
        "voice_desc_label": "Äänityyli:",
        "output_mode_label": "Tuloste:",
        "output_single": "Yksi MP3",
        "output_chapters": "Yksi MP3 per luku",
        "save_label": "Tallenna:",
        "status_label": "Tila:",
        "status_ready_msg": "Valitse PDF tai kirjoita teksti aloittaaksesi.",
        "convert": "Muunna",
        "cancel": "Peruuta",
        "open_folder": "Avaa kansio",
        "show_log": "Näytä loki",
        "hide_log": "Piilota loki",
        "ui_language": "Käyttöliittymä:",
        "converting": "Muunnetaan...",
        "cancelling": "Peruutetaan\u2026",
        "done": "Valmis!",
        "error": "Virhe",
        "no_pdf": "Valitse ensin PDF-tiedosto.",
        "no_text": "Kirjoita tai liitä ensin teksti.",
        "select_pdf": "Valitse PDF-tiedosto",
        "save_as": "Tallenna nimellä",
        "speed_slow": "Hidas",
        "speed_normal": "Normaali",
        "speed_fast": "Nopea",
        "speed_very_fast": "Erittäin nopea",
        "engine_manager_title": "Moottoreiden hallinta",
        "no_file_selected": "Ei tiedostoa valittu",
        "no_output_selected": "Ei valittu",
        "select_input_prompt": "Valitse syöte ja paina Muunna.",
        "update_available": "Versio {version} saatavilla.",
        "update_now": "Päivitä nyt",
        "update_downloading": "Ladataan päivitystä...",
        "update_installing": "Asennetaan päivitys...",
        "update_failed": "Päivitys epäonnistui.",
        "update_error_detail": "Päivitys epäonnistui: {error}",
        "listen": "Esikuuntele",
        "listening": "Toistetaan...",
        "listen_no_text": "Kirjoita ensin teksti Teksti-välilehdelle.",
        "pdf_no_text": "PDF ei sisällä tekstiä (tiedosto voi olla skannattu). Kokeile ensin OCR-muunnosta.",
    },
    "en": {
        "window_title": "AudiobookMaker",
        "tab_pdf": "PDF file",
        "tab_text": "Text",
        "text_placeholder": "Type or paste text here...",
        "settings_frame": "Settings",
        "engine_label": "Engine:",
        "install_engines": "Install engines\u2026",
        "status_ready": "Ready",
        "language_label": "Language:",
        "speed_label": "Speed:",
        "voice_label": "Voice:",
        "test_voice": "Test voice",
        "ref_audio_label": "Ref. audio:",
        "browse": "Browse\u2026",
        "clear": "Clear",
        "voice_desc_label": "Voice style:",
        "output_mode_label": "Output:",
        "output_single": "Single MP3",
        "output_chapters": "One MP3 per chapter",
        "save_label": "Save to:",
        "status_label": "Status:",
        "status_ready_msg": "Select a PDF or enter text to begin.",
        "convert": "Convert",
        "cancel": "Cancel",
        "open_folder": "Open folder",
        "show_log": "Show log",
        "hide_log": "Hide log",
        "ui_language": "Interface:",
        "converting": "Converting...",
        "cancelling": "Cancelling\u2026",
        "done": "Done!",
        "error": "Error",
        "no_pdf": "Please select a PDF file first.",
        "no_text": "Please enter or paste text first.",
        "select_pdf": "Select PDF file",
        "save_as": "Save as",
        "speed_slow": "Slow",
        "speed_normal": "Normal",
        "speed_fast": "Fast",
        "speed_very_fast": "Very fast",
        "engine_manager_title": "Engine Manager",
        "no_file_selected": "No file selected",
        "no_output_selected": "Not selected",
        "select_input_prompt": "Select input and press Convert.",
        "update_available": "Version {version} available.",
        "update_now": "Update now",
        "update_downloading": "Downloading update...",
        "update_installing": "Installing update...",
        "update_failed": "Update failed.",
        "update_error_detail": "Update failed: {error}",
        "listen": "Preview",
        "listening": "Playing...",
        "listen_no_text": "Enter text in the Text tab first.",
        "pdf_no_text": "PDF contains no extractable text (it may be scanned). Try OCR first.",
    },
}


# ---------------------------------------------------------------------------
# Engine Manager dialog
# ---------------------------------------------------------------------------


_ENGINE_MGR_STRINGS = {
    "fi": {
        "title": "Moottoreiden hallinta",
        "system": "Järjestelmä",
        "gpu": "Näytönohjain",
        "no_gpu": "Ei NVIDIA-GPU:ta",
        "disk": "Levytila",
        "python": "Python 3.11",
        "py_found": "Asennettu",
        "py_missing": "Ei asennettu (asentuu Chatterboxin yhteydessä)",
        "engines": "Moottorit",
        "installed": "Asennettu",
        "not_installed": "Ei asennettu",
        "available": "Käytettävissä",
        "install_btn": "Asenna",
        "uninstall_btn": "Poista",
        "cancel_btn": "Peruuta asennus",
        "installing": "Asennetaan...",
        "step": "Vaihe",
        "of": "/",
        "close": "Sulje",
        "prereq_fail": "Esivaatimukset eivät täyty:",
        "confirm_uninstall": "Haluatko varmasti poistaa moottorin?",
        "uninstall_done": "Poistettu.",
        "install_done": "Asennus valmis.",
        "install_failed": "Asennus epäonnistui:",
    },
    "en": {
        "title": "Engine manager",
        "system": "System",
        "gpu": "Graphics card",
        "no_gpu": "No NVIDIA GPU",
        "disk": "Disk space",
        "python": "Python 3.11",
        "py_found": "Installed",
        "py_missing": "Not installed (installed with Chatterbox)",
        "engines": "Engines",
        "installed": "Installed",
        "not_installed": "Not installed",
        "available": "Available",
        "install_btn": "Install",
        "uninstall_btn": "Uninstall",
        "cancel_btn": "Cancel install",
        "installing": "Installing...",
        "step": "Step",
        "of": "/",
        "close": "Close",
        "prereq_fail": "Prerequisites not met:",
        "confirm_uninstall": "Really uninstall this engine?",
        "uninstall_done": "Uninstalled.",
        "install_done": "Install complete.",
        "install_failed": "Install failed:",
    },
}


class EngineManagerDialog(ctk.CTkToplevel):
    """Modal dialog for installing/managing TTS engines."""

    def __init__(self, parent, ui_lang: str = "fi") -> None:
        super().__init__(parent)
        self._ui_lang = ui_lang
        self._strings = _ENGINE_MGR_STRINGS.get(ui_lang, _ENGINE_MGR_STRINGS["fi"])
        self._cancel_event: Optional[threading.Event] = None
        self._install_thread: Optional[threading.Thread] = None
        self._progress_queue: queue.Queue = queue.Queue()
        self._engine_rows: dict[str, dict] = {}

        self.title(self._strings["title"])
        self.geometry("640x520")
        self.minsize(560, 460)

        self._build_ui()
        self._refresh_system_info()
        self._refresh_engine_rows()

    def _s(self, key: str) -> str:
        return self._strings.get(key, key)

    def _build_ui(self) -> None:
        # System info section
        sys_frame = ctk.CTkFrame(self)
        sys_frame.pack(fill=tk.X, padx=12, pady=(12, 6))
        sys_frame.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            sys_frame, text=self._s("system"),
            font=ctk.CTkFont(weight="bold", size=14),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4))

        # Rows for GPU / disk / python (filled in _refresh_system_info)
        self._gpu_label = ctk.CTkLabel(sys_frame, text="...", anchor="w")
        self._gpu_label.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=2)
        self._disk_label = ctk.CTkLabel(sys_frame, text="...", anchor="w")
        self._disk_label.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=2)
        self._py_label = ctk.CTkLabel(sys_frame, text="...", anchor="w")
        self._py_label.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(2, 8))

        # Engines section
        eng_frame = ctk.CTkFrame(self)
        eng_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        eng_frame.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            eng_frame, text=self._s("engines"),
            font=ctk.CTkFont(weight="bold", size=14),
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))

        self._engines_container = ctk.CTkFrame(eng_frame, fg_color="transparent")
        self._engines_container.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self._engines_container.columnconfigure(0, weight=1)
        eng_frame.rowconfigure(1, weight=1)

        # Progress section (hidden until install starts)
        self._progress_frame = ctk.CTkFrame(self)
        self._progress_step_lbl = ctk.CTkLabel(
            self._progress_frame, text="", anchor="w",
        )
        self._progress_step_lbl.pack(fill=tk.X, padx=8, pady=(8, 2))
        self._progress_bar = ctk.CTkProgressBar(self._progress_frame)
        self._progress_bar.pack(fill=tk.X, padx=8, pady=2)
        self._progress_bar.set(0)
        self._progress_msg_lbl = ctk.CTkLabel(
            self._progress_frame, text="", anchor="w",
        )
        self._progress_msg_lbl.pack(fill=tk.X, padx=8, pady=(2, 8))

        # Close button
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill=tk.X, padx=12, pady=(6, 12))
        self._close_btn = ctk.CTkButton(
            btn_row, text=self._s("close"), command=self.destroy, width=120,
        )
        self._close_btn.pack(side=tk.RIGHT)

    def _refresh_system_info(self) -> None:
        from src.system_checks import detect_gpu, check_disk_space, find_python311

        gpu = detect_gpu()
        if gpu.has_nvidia:
            vram_gb = gpu.vram_mb / 1024
            self._gpu_label.configure(
                text=f"  {self._s('gpu')}: {gpu.gpu_name}  ({vram_gb:.1f} GB VRAM)",
                text_color="green",
            )
        else:
            self._gpu_label.configure(
                text=f"  {self._s('gpu')}: {self._s('no_gpu')}",
                text_color="gray",
            )

        disk = check_disk_space(str(Path.home()))
        self._disk_label.configure(
            text=f"  {self._s('disk')}: {disk.free_gb:.1f} GB / {disk.total_gb:.1f} GB",
            text_color="green" if disk.free_gb >= 16 else "orange",
        )

        py = find_python311()
        if py.found:
            self._py_label.configure(
                text=f"  {self._s('python')}: {self._s('py_found')} ({py.version})",
                text_color="green",
            )
        else:
            self._py_label.configure(
                text=f"  {self._s('python')}: {self._s('py_missing')}",
                text_color="gray",
            )

    def _engine_size_text(self, installer) -> str:
        """Return a human-readable size for an installer.

        Installed: actual disk usage of the voice/model directory.
        Not installed: sum of estimated_size_mb across planned steps.
        """
        try:
            if installer.is_installed():
                # Known install locations by engine id.
                root: Optional[Path] = None
                if getattr(installer, "_voice_dir", None) is not None:
                    root = installer._voice_dir
                elif getattr(installer, "_venv_path", None) is not None:
                    root = installer._venv_path
                if root and Path(root).exists():
                    total_bytes = 0
                    for r, _d, files in os.walk(str(root)):
                        for f in files:
                            try:
                                total_bytes += os.path.getsize(os.path.join(r, f))
                            except OSError:
                                pass
                    return self._fmt_size_mb(total_bytes / (1024 * 1024))
                return ""
            # Not installed — sum the planned step sizes.
            steps = installer.get_steps()
            est = sum(getattr(s, "estimated_size_mb", 0) or 0 for s in steps)
            if est <= 0:
                return ""
            prefix = "~"  # estimate marker
            return f"{prefix}{self._fmt_size_mb(est)}"
        except Exception:
            return ""

    @staticmethod
    def _fmt_size_mb(size_mb: float) -> str:
        if size_mb >= 1024:
            return f"{size_mb / 1024:.1f} GB"
        if size_mb >= 10:
            return f"{size_mb:.0f} MB"
        return f"{size_mb:.1f} MB"

    def _refresh_engine_rows(self) -> None:
        # Clear existing rows
        for child in self._engines_container.winfo_children():
            child.destroy()
        self._engine_rows.clear()

        from src.engine_installer import list_installable

        for i, installer in enumerate(list_installable()):
            row = ctk.CTkFrame(self._engines_container)
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=4)
            row.columnconfigure(1, weight=1)

            name_lbl = ctk.CTkLabel(
                row, text=installer.display_name, anchor="w",
                font=ctk.CTkFont(weight="bold"),
            )
            name_lbl.grid(row=0, column=0, sticky="w", padx=(8, 4), pady=4)

            installed = installer.is_installed()
            status_text = self._s("installed") if installed else self._s("not_installed")
            status_color = "green" if installed else "gray"
            status_lbl = ctk.CTkLabel(
                row, text=status_text, text_color=status_color, anchor="w",
            )
            status_lbl.grid(row=0, column=1, sticky="w", padx=4, pady=4)

            # Size (installed: actual on disk; not installed: estimate).
            size_text = self._engine_size_text(installer)
            size_lbl = ctk.CTkLabel(
                row, text=size_text, text_color=("gray40", "gray70"),
                anchor="e",
            )
            size_lbl.grid(row=0, column=2, sticky="e", padx=(4, 8), pady=4)

            if installed:
                btn = ctk.CTkButton(
                    row, text=self._s("uninstall_btn"),
                    command=lambda inst=installer: self._on_uninstall(inst),
                    width=110,
                )
            else:
                btn = ctk.CTkButton(
                    row, text=self._s("install_btn"),
                    command=lambda inst=installer: self._on_install(inst),
                    width=110,
                )
            btn.grid(row=0, column=3, padx=(4, 8), pady=4)

            self._engine_rows[installer.engine_id] = {
                "row": row, "status": status_lbl, "size": size_lbl, "btn": btn,
            }

    def _on_install(self, installer) -> None:
        # Check prerequisites
        issues = installer.check_prerequisites()
        if issues:
            msg = self._s("prereq_fail") + "\n\n" + "\n".join(f"\u2022 {x}" for x in issues)
            messagebox.showerror(self._s("title"), msg, parent=self)
            return

        # Show progress UI. Dialog uses pack; the embedded view uses grid.
        if hasattr(self, "_close_btn"):
            self._progress_frame.pack(
                fill=tk.X, padx=12, pady=6, before=self._close_btn.master,
            )
        else:
            self._progress_frame.grid(
                row=getattr(self, "_progress_row", 3), column=0,
                sticky="ew", padx=12, pady=6,
            )
        self._progress_step_lbl.configure(text=self._s("installing"))
        self._progress_msg_lbl.configure(text="")
        self._progress_bar.set(0)

        # Disable all install buttons, change one to Cancel
        for row in self._engine_rows.values():
            row["btn"].configure(state="disabled")

        self._cancel_event = threading.Event()

        def worker() -> None:
            try:
                installer.install(
                    progress_cb=lambda p: self._progress_queue.put(p),
                    cancel_event=self._cancel_event,
                )
            except Exception as exc:
                from src.engine_installer import InstallProgress
                self._progress_queue.put(InstallProgress(
                    error=str(exc), done=True,
                ))

        self._install_thread = threading.Thread(
            target=worker, daemon=True, name=f"install-{installer.engine_id}",
        )
        self._install_thread.start()
        self.after(100, self._poll_progress)

    def _poll_progress(self) -> None:
        try:
            while True:
                p = self._progress_queue.get_nowait()
                self._handle_progress(p)
        except queue.Empty:
            pass

        if self._install_thread and self._install_thread.is_alive():
            self.after(100, self._poll_progress)

    def _handle_progress(self, p) -> None:
        if p.error:
            messagebox.showerror(
                self._s("title"),
                f"{self._s('install_failed')}\n\n{p.error}",
                parent=self,
            )
            self._install_finished()
            return
        if p.done:
            messagebox.showinfo(
                self._s("title"), self._s("install_done"), parent=self,
            )
            self._install_finished()
            return

        # Update progress UI
        if p.total_steps:
            head = f"{self._s('step')} {p.step}{self._s('of')}{p.total_steps}: {p.step_label}"
        else:
            head = p.step_label or self._s("installing")
        self._progress_step_lbl.configure(text=head)
        if p.percent:
            self._progress_bar.set(p.percent / 100.0)
        self._progress_msg_lbl.configure(text=p.message or "")

    def _install_finished(self) -> None:
        self._cancel_event = None
        self._install_thread = None
        # Hide via whichever geometry manager placed it.
        try:
            self._progress_frame.pack_forget()
        except Exception:
            pass
        try:
            self._progress_frame.grid_forget()
        except Exception:
            pass
        self._refresh_engine_rows()

    def _on_uninstall(self, installer) -> None:
        if not messagebox.askyesno(
            self._s("title"), self._s("confirm_uninstall"), parent=self,
        ):
            return
        # Best-effort uninstall: remove installer's known directories.
        try:
            if hasattr(installer, "_voice_dir") and installer._voice_dir.exists():
                shutil.rmtree(installer._voice_dir, ignore_errors=True)
            # For Chatterbox, the venv path is the install marker.
            if hasattr(installer, "_venv_path"):
                p = installer._venv_path
                if p.exists():
                    shutil.rmtree(p, ignore_errors=True)
            messagebox.showinfo(
                self._s("title"), self._s("uninstall_done"), parent=self,
            )
        except Exception as exc:
            messagebox.showerror(self._s("title"), str(exc), parent=self)
        finally:
            self._refresh_engine_rows()


# ---------------------------------------------------------------------------
# Engine manager — in-place view (replaces the Toplevel dialog)
# ---------------------------------------------------------------------------


class EngineManagerView(ctk.CTkFrame):
    """Embedded settings page with the same content as EngineManagerDialog.

    Lives in the root window's stacked view container and is swapped in
    via tkraise() instead of opening a new Toplevel. A back-arrow button
    returns to the main audiobook view.
    """

    def __init__(self, parent, ui_lang: str = "fi", on_back=None) -> None:
        super().__init__(parent, fg_color="transparent")
        self._ui_lang = ui_lang
        self._strings = _ENGINE_MGR_STRINGS.get(ui_lang, _ENGINE_MGR_STRINGS["fi"])
        self._on_back = on_back
        self._cancel_event: Optional[threading.Event] = None
        self._install_thread: Optional[threading.Thread] = None
        self._progress_queue: "queue.Queue" = queue.Queue()
        self._engine_rows: dict[str, dict] = {}

        self._build_ui()

    # Convenience alias so external callers can request a data refresh.
    def refresh(self) -> None:
        self._refresh_system_info()
        self._refresh_engine_rows()

    def _s(self, key: str) -> str:
        return self._strings.get(key, key)

    # Reuse the dialog's UI/logic by delegation — identical methods
    # with a different parent. Below is the full in-place version.

    def _build_ui(self) -> None:
        # Header row with back arrow + title.
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        header.columnconfigure(1, weight=1)

        back_btn = ctk.CTkButton(
            header,
            text="\u2190  " + (self._s("close") if self._ui_lang == "en" else "Takaisin"),
            width=120, height=32,
            command=self._on_back_click,
            anchor="w",
        )
        back_btn.grid(row=0, column=0, sticky="w")

        title_lbl = ctk.CTkLabel(
            header, text=self._s("title"),
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        title_lbl.grid(row=0, column=1, sticky="w", padx=(12, 0))

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)  # Engines section stretches.

        # System info section
        sys_frame = ctk.CTkFrame(self)
        sys_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        sys_frame.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            sys_frame, text=self._s("system"),
            font=ctk.CTkFont(weight="bold", size=14),
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))

        self._gpu_label = ctk.CTkLabel(sys_frame, text="...", anchor="w")
        self._gpu_label.grid(row=1, column=0, sticky="ew", padx=8, pady=2)
        self._disk_label = ctk.CTkLabel(sys_frame, text="...", anchor="w")
        self._disk_label.grid(row=2, column=0, sticky="ew", padx=8, pady=2)
        self._py_label = ctk.CTkLabel(sys_frame, text="...", anchor="w")
        self._py_label.grid(row=3, column=0, sticky="ew", padx=8, pady=(2, 8))

        # Engines section
        eng_frame = ctk.CTkFrame(self)
        eng_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=6)
        eng_frame.columnconfigure(0, weight=1)
        eng_frame.rowconfigure(1, weight=1)

        ctk.CTkLabel(
            eng_frame, text=self._s("engines"),
            font=ctk.CTkFont(weight="bold", size=14),
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))

        self._engines_container = ctk.CTkFrame(eng_frame, fg_color="transparent")
        self._engines_container.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self._engines_container.columnconfigure(0, weight=1)

        # Progress section (hidden until install starts)
        self._progress_frame = ctk.CTkFrame(self)
        self._progress_step_lbl = ctk.CTkLabel(
            self._progress_frame, text="", anchor="w",
        )
        self._progress_step_lbl.pack(fill=tk.X, padx=8, pady=(8, 2))
        self._progress_bar = ctk.CTkProgressBar(self._progress_frame)
        self._progress_bar.pack(fill=tk.X, padx=8, pady=2)
        self._progress_bar.set(0)
        self._progress_msg_lbl = ctk.CTkLabel(
            self._progress_frame, text="", anchor="w",
        )
        self._progress_msg_lbl.pack(fill=tk.X, padx=8, pady=(2, 8))
        # Grid row reserved; shown via .grid() during install.
        self._progress_row = 3

        # Initial content.
        self._refresh_system_info()
        self._refresh_engine_rows()

    def _on_back_click(self) -> None:
        if self._on_back:
            self._on_back()

    # --- Shared logic (same as EngineManagerDialog) ---
    # Import-by-reference keeps both classes in sync.
    _engine_size_text = EngineManagerDialog._engine_size_text
    _fmt_size_mb = EngineManagerDialog._fmt_size_mb
    _refresh_system_info = EngineManagerDialog._refresh_system_info
    _refresh_engine_rows = EngineManagerDialog._refresh_engine_rows
    _on_install = EngineManagerDialog._on_install
    _poll_progress = EngineManagerDialog._poll_progress
    _handle_progress = EngineManagerDialog._handle_progress
    _install_finished = EngineManagerDialog._install_finished
    _on_uninstall = EngineManagerDialog._on_uninstall


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------


class UnifiedApp(SynthMixin, UpdateMixin, ctk.CTk):
    """Single-window AudiobookMaker GUI."""

    POLL_INTERVAL_MS = 100

    def __init__(self) -> None:
        super().__init__()
        self.title(WINDOW_TITLE)
        self.minsize(WINDOW_MIN_W, WINDOW_MIN_H)
        self._center_window()

        # Set goat icon on the window title bar.
        self._set_window_icon()

        # ---- internal state ----
        self._pdf_path: Optional[str] = None
        self._output_path: Optional[str] = None
        self._synth_running = False
        self._cancel_requested = False
        self._cancel_flag = threading.Event()
        self._testing_voice = False
        self._listening = False
        self._listen_proc: Optional[subprocess.Popen] = None
        self._listen_temp_path: Optional[str] = None
        self._chatterbox_runner: Optional[ChatterboxRunner] = None
        self._chatterbox_last_mp3: str = ""
        self._event_queue: "queue.Queue[ProgressEvent]" = queue.Queue()
        self._log_visible = True
        self._pending_update: Optional[UpdateInfo] = None
        # True if the user explicitly chose the output path via Vaihda…
        # Auto-paths get bumped to the next free number before each run
        # so repeated Muunna presses never overwrite the previous MP3.
        self._output_user_chosen: bool = False

        # Load persisted preferences.
        self._user_cfg = app_config.load()
        self._ui_lang: str = self._user_cfg.ui_language or _detect_system_language()

        # Maps populated during engine list init.
        self._engine_display_to_id: dict[str, str] = {}

        # Build all widgets.
        self._build_ui()

        # Restore settings from config.
        self._apply_loaded_config()

        # Apply UI language (updates all widget texts).
        self._apply_ui_language()

        # Check for updates in background (only in frozen/installed mode).
        self._update_queue: "queue.Queue[UpdateInfo]" = queue.Queue()
        if getattr(sys, "frozen", False):
            # Self-heal: if the last in-app update didn't take effect,
            # offer a visible-installer fallback.
            self.after(800, self._check_pending_update_marker)

            threading.Thread(
                target=self._check_update_worker, daemon=True, name="update-check",
            ).start()
            self.after(500, self._poll_update_check)

            # Check for old installs / orphan shortcuts shortly after launch.
            # Only in frozen mode — dev mode shouldn't trigger cleanup.
            self.after(1500, self._check_for_old_installs)

    # ------------------------------------------------------------------
    # Window helpers
    # ------------------------------------------------------------------

    def _center_window(self) -> None:
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - WINDOW_MIN_W) // 2
        y = (sh - WINDOW_MIN_H) // 2
        self.geometry(f"{WINDOW_MIN_W}x{WINDOW_MIN_H}+{x}+{y}")

    def _set_window_icon(self) -> None:
        """Set the goat icon on the window title bar and taskbar."""
        # Set AppUserModelID so Windows shows our icon in the taskbar
        # instead of the default Python icon.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "AudiobookMaker.AudiobookMaker"
            )
        except Exception:
            pass

        try:
            icon_path = _APP_ROOT / "assets" / "icon.ico"
            if icon_path.exists():
                self.iconbitmap(str(icon_path))
                return
            # Fallback: try PNG via PhotoImage (works on some platforms).
            png_path = _APP_ROOT / "assets" / "icon.png"
            if png_path.exists():
                icon_img = tk.PhotoImage(file=str(png_path))
                self.iconphoto(True, icon_img)
        except Exception:
            pass  # Non-critical — default icon is fine as fallback.

    # ------------------------------------------------------------------
    # UI language helpers
    # ------------------------------------------------------------------

    def _s(self, key: str) -> str:
        """Return the UI string for *key* in the current UI language."""
        return _STRINGS.get(self._ui_lang, _STRINGS["fi"]).get(key, key)

    def _on_ui_language_changed(self, selection: str = "") -> None:
        """Handle the UI language combobox change."""
        sel = self._ui_lang_cb.get()
        self._ui_lang = "en" if sel == "English" else "fi"
        self._apply_ui_language()
        # Persist immediately.
        self._save_current_config()

    def _apply_ui_language(self) -> None:
        """Update ALL widget texts to match ``self._ui_lang``."""
        s = lambda key: self._s(key)  # noqa: E731 — local shorthand

        # Window title — suffix with the running version so the user can
        # tell at a glance which build they're on (and post-update verify).
        self.title(f"{s('window_title')} v{APP_VERSION}")

        # Input tabview tabs — CTkTabview can't rename tabs, so the internal
        # names are always the Finnish originals ("PDF-tiedosto", "Teksti").
        # The map must use those original names, not translated ones.

        # PDF browse button.
        self._pdf_browse_btn.configure(text=s("browse"))

        # Text placeholder (only update if placeholder is currently shown).
        self._text_placeholder = s("text_placeholder")
        if self._text_has_placeholder:
            self._text_widget.delete("1.0", tk.END)
            self._text_widget.insert("1.0", self._text_placeholder)

        # PDF var — if no file selected, update the placeholder text.
        if not self._pdf_path:
            self._pdf_entry.configure(state="normal")
            self._pdf_entry.delete(0, tk.END)
            self._pdf_entry.insert(0, s("no_file_selected"))
            self._pdf_entry.configure(state="disabled")

        # Settings header button (collapsible label).
        if hasattr(self, "_settings_header_btn"):
            arrow = "\u25BE" if self._settings_open else "\u25B8"
            self._settings_header_btn.configure(
                text=f"{arrow} {s('settings_frame')}"
            )

        # Engine label + engine manager button (header bar).
        self._engine_label.configure(text=s("engine_label"))
        self._install_engines_btn.configure(text=s("install_engines"))

        # TTS language + speed labels.
        self._tts_lang_label.configure(text=s("language_label"))
        self._speed_label.configure(text=s("speed_label"))

        # Speed combobox: translate values while preserving the selected rate.
        old_speed_opts = SPEED_OPTIONS["fi" if self._ui_lang == "en" else "en"]
        new_speed_opts = SPEED_OPTIONS[self._ui_lang]
        current_rate = old_speed_opts.get(self._speed_cb.get())
        if current_rate is None:
            # Might already be in current language — look up directly.
            current_rate = new_speed_opts.get(self._speed_cb.get(), "+0%")
        self._speed_cb.configure(values=list(new_speed_opts.keys()))
        # Find the label for the current rate in the new language.
        new_label = next(
            (lbl for lbl, val in new_speed_opts.items() if val == current_rate),
            list(new_speed_opts.keys())[1],  # fallback to Normal
        )
        self._speed_cb.set(new_label)

        # Voice label + test button.
        self._voice_label.configure(text=s("voice_label"))
        self._test_btn.configure(text=s("test_voice"))

        # Reference audio widgets.
        self._ref_label.configure(text=s("ref_audio_label"))
        self._ref_browse_btn.configure(text=s("browse").rstrip("\u2026"))
        self._ref_clear_btn.configure(text=s("clear"))

        # Voice description label.
        self._desc_label.configure(text=s("voice_desc_label"))

        # Output mode: translate values while preserving the selected mode.
        old_out_opts = OUTPUT_MODES["fi" if self._ui_lang == "en" else "en"]
        new_out_opts = OUTPUT_MODES[self._ui_lang]
        current_mode = old_out_opts.get(self._output_mode_cb.get())
        if current_mode is None:
            current_mode = new_out_opts.get(self._output_mode_cb.get(), "single")
        self._output_mode_cb.configure(values=list(new_out_opts.keys()))
        new_mode_label = next(
            (lbl for lbl, val in new_out_opts.items() if val == current_mode),
            list(new_out_opts.keys())[0],
        )
        self._output_mode_cb.set(new_mode_label)
        self._output_mode_label.configure(text=s("output_mode_label"))

        # Save label + browse button.
        self._save_label.configure(text=s("save_label"))
        self._out_browse_btn.configure(text=s("browse"))

        # Output var — if no output selected, update placeholder.
        if not self._output_path:
            self._out_entry.configure(state="normal")
            self._out_entry.delete(0, tk.END)
            self._out_entry.insert(0, s("no_output_selected"))
            self._out_entry.configure(state="disabled")

        # Status line (only if not mid-conversion).
        if not self._synth_running:
            self._status_label_val.configure(text=s("select_input_prompt"))

        # Listen / convert / cancel / open folder buttons.
        if not self._listening:
            self._listen_btn.configure(text=s("listen"))
        self._convert_btn.configure(text=s("convert"))
        if not self._cancel_requested:
            self._cancel_btn.configure(text=s("cancel"))
        self._open_folder_btn.configure(text=s("open_folder"))

        # Log toggle button.
        if self._log_visible:
            self._log_toggle_btn.configure(text=s("hide_log"))
        else:
            self._log_toggle_btn.configure(text=s("show_log"))

        # Update banner.
        if self._pending_update and self._pending_update.available:
            self._update_label.configure(
                text=s("update_available").format(
                    version=self._pending_update.latest_version
                )
            )
            self._update_btn.configure(text=s("update_now"))

        # UI lang combobox — keep it in sync.
        self._ui_lang_cb.set("Suomi" if self._ui_lang == "fi" else "English")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Root container holding two stacked views we can swap with tkraise().
        # Keeps the whole app in a single window — no Toplevel popups for
        # settings / engine manager.
        self._view_container = ctk.CTkFrame(self)
        self._view_container.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._view_container.columnconfigure(0, weight=1)
        self._view_container.rowconfigure(0, weight=1)

        # Main view — the normal "create audiobook" screen.
        main = ctk.CTkFrame(self._view_container, fg_color="transparent")
        main.grid(row=0, column=0, sticky="nsew")
        self._main_view = main
        main.columnconfigure(0, weight=1)
        # Let log panel row stretch.
        main.rowconfigure(8, weight=1)

        # Track current input mode for tab renaming during language changes.
        self._input_mode_raw = "pdf"
        self._tab_name_map: dict[str, str] = {}

        # Main view layout (top → bottom):
        #   0  Update banner (hidden by default)
        #   1  Header bar (UI language + Moottorit… back-nav)
        #   2  Input tabs (PDF / Text)
        #   3  Engine + voice bar (ALWAYS visible — primary model picker)
        #   4  Action row (big Muunna + small secondaries + progress)
        #   5  Asetukset header (collapse / expand)
        #   6  Asetukset body (language, speed, ref audio, voice style, output path)
        #   7  Log toggle
        #   8  Log panel (visible by default)
        self._build_update_banner(main, row=0)
        self._build_header_bar(main, row=1)
        self._build_input_tabs(main, row=2)
        self._build_engine_bar(main, row=3)
        self._build_action_row(main, row=4)
        self._build_settings_frame(main, row=5)  # header at row=5, body at row=6
        self._build_log_panel(main, row=7, stretch_row=8)

        # Settings view — in-place alternative to the old Toplevel popup.
        # Stacked in the same grid cell; switched via tkraise().
        self._settings_view = EngineManagerView(
            self._view_container, ui_lang=self._ui_lang,
            on_back=self._show_main_view,
        )
        self._settings_view.grid(row=0, column=0, sticky="nsew")
        main.tkraise()

    # ---- View switching (single-window flow, no popups) --------------

    def _show_settings_view(self) -> None:
        """Swap to the engine manager view."""
        self._settings_view.refresh()
        self._settings_view.tkraise()

    def _show_main_view(self) -> None:
        """Return to the main audiobook creation view."""
        # Engine status may have changed (install/uninstall); refresh the dot.
        self._populate_engine_list()
        self._main_view.tkraise()

    # ---- Engine + voice picker bar (ALWAYS visible) -------------------

    def _build_engine_bar(self, parent: ctk.CTkFrame, row: int) -> None:
        """Compact always-visible row: engine + voice dropdowns.

        The user asked for the model picker to be close at hand when
        creating speech. Placing engine + voice right above Muunna keeps
        both one click away without opening Asetukset.
        """
        bar = ctk.CTkFrame(parent)
        bar.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        bar.columnconfigure(1, weight=1)
        bar.columnconfigure(3, weight=1)

        self._engine_label = ctk.CTkLabel(bar, text="Moottori:")
        self._engine_label.grid(row=0, column=0, sticky="w", padx=(8, 6), pady=6)
        self._engine_cb = ctk.CTkComboBox(
            bar, state="readonly",
            command=self._on_engine_changed,
        )
        self._engine_cb.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=6)
        self._populate_engine_list()

        self._voice_label = ctk.CTkLabel(bar, text="Ääni:")
        self._voice_label.grid(row=0, column=2, sticky="w", padx=(0, 6), pady=6)
        self._voice_cb = ctk.CTkComboBox(bar, state="readonly")
        self._voice_cb.grid(row=0, column=3, sticky="ew", padx=(0, 4), pady=6)

        self._test_btn = ctk.CTkButton(
            bar, text="Testaa ääni", command=self._on_test_voice,
            width=110,
        )
        self._test_btn.grid(row=0, column=4, padx=(0, 8), pady=6)

    # ---- Header bar (language toggle, engine manager, about) ----------

    def _build_header_bar(self, parent: ctk.CTkFrame, row: int) -> None:
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        bar.columnconfigure(0, weight=1)

        # Right-aligned icon buttons.
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")

        # Language toggle — compact combobox (kept as _ui_lang_cb so existing
        # language-change handler keeps working). Replaces the old
        # "Käyttöliittymä" row in Asetukset.
        self._ui_lang_cb = ctk.CTkComboBox(
            right,
            values=["Suomi", "English"], state="readonly", width=100,
            command=self._on_ui_language_changed,
        )
        self._ui_lang_cb.set("Suomi" if self._ui_lang == "fi" else "English")
        self._ui_lang_cb.grid(row=0, column=0, padx=(0, 6))

        # Engine manager — now an in-place view (no Toplevel popup).
        self._install_engines_btn = ctk.CTkButton(
            right, text="Moottorit\u2026",
            command=self._show_settings_view, width=120,
        )
        self._install_engines_btn.grid(row=0, column=1)

    # ---- Primary action row (big Muunna + secondaries + progress) ----

    def _build_action_row(self, parent: ctk.CTkFrame, row: int) -> None:
        ar = ctk.CTkFrame(parent, fg_color="transparent")
        ar.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        ar.columnconfigure(0, weight=1)

        # Top: big primary + small secondaries.
        btn_row = ctk.CTkFrame(ar, fg_color="transparent")
        btn_row.grid(row=0, column=0, sticky="ew")
        btn_row.columnconfigure(0, weight=1)

        # The star of the show — wide, bold, clearly the primary action.
        self._convert_btn = ctk.CTkButton(
            btn_row, text="Muunna", command=self._on_convert_click,
            height=44,
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        self._convert_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self._listen_btn = ctk.CTkButton(
            btn_row, text="Esikuuntele", command=self._on_listen_click,
            height=44, width=120,
        )
        self._listen_btn.grid(row=0, column=1, padx=(0, 6))

        self._cancel_btn = ctk.CTkButton(
            btn_row, text="Peruuta", command=self._request_cancel,
            height=44, width=100,
            fg_color=("#c62828", "#8b0000"),
            hover_color=("#8b0000", "#5c0000"),
        )
        self._cancel_btn.grid(row=0, column=2, padx=(0, 6))
        self._cancel_btn.grid_remove()  # Only visible while running.

        self._open_folder_btn = ctk.CTkButton(
            btn_row, text="Avaa kansio", command=self._open_output_folder,
            height=44, width=120, state="disabled",
        )
        self._open_folder_btn.grid(row=0, column=3)

        # Bottom: progress bar + inline status (small, right-aligned).
        progress_row = ctk.CTkFrame(ar, fg_color="transparent")
        progress_row.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        progress_row.columnconfigure(0, weight=1)

        self._progress_bar = ctk.CTkProgressBar(progress_row)
        self._progress_bar.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._progress_bar.set(0)

        self._status_label_val = ctk.CTkLabel(
            progress_row, text="", text_color="gray", width=180, anchor="e",
        )
        self._status_label_val.grid(row=0, column=1, sticky="e")

        # ETA label (kept for existing code paths; placed unobtrusively).
        self._eta_label = ctk.CTkLabel(ar, text="", text_color="gray")
        self._eta_label.grid(row=2, column=0, sticky="e", pady=(2, 0))

    # ---- 0. Update banner ---------------------------------------------

    def _build_update_banner(self, parent: ctk.CTkFrame, row: int) -> None:
        self._update_banner = ctk.CTkFrame(
            parent, fg_color=("green", "darkgreen"), corner_radius=8
        )
        self._update_banner.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        self._update_banner.columnconfigure(0, weight=1)

        self._update_label = ctk.CTkLabel(
            self._update_banner, text="", text_color="white"
        )
        self._update_label.grid(row=0, column=0, sticky="w", padx=(8, 8))

        self._update_btn = ctk.CTkButton(
            self._update_banner, text=self._s("update_now"),
            command=self._on_update_click,
        )
        self._update_btn.grid(row=0, column=1, padx=(0, 8), pady=4)

        # Hidden by default.
        self._update_banner.grid_remove()

    # ---- 1. Input tabs ------------------------------------------------

    def _build_input_tabs(self, parent: ctk.CTkFrame, row: int) -> None:
        self._input_nb = ctk.CTkTabview(
            parent, height=200, command=self._on_tab_changed
        )
        self._input_nb.grid(row=row, column=0, sticky="ew", pady=(0, 8))

        # PDF tab
        pdf_tab_name = "PDF-tiedosto"
        pdf_frame = self._input_nb.add(pdf_tab_name)
        pdf_frame.columnconfigure(0, weight=1)

        self._pdf_entry = ctk.CTkEntry(pdf_frame, state="disabled")
        self._pdf_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        # Set initial placeholder text.
        self._pdf_entry.configure(state="normal")
        self._pdf_entry.insert(0, "Ei tiedostoa valittu")
        self._pdf_entry.configure(state="disabled")

        self._pdf_browse_btn = ctk.CTkButton(
            pdf_frame, text="Selaa\u2026", command=self._browse_pdf, width=80
        )
        self._pdf_browse_btn.grid(row=0, column=1)

        # Text tab
        text_tab_name = "Teksti"
        text_frame = self._input_nb.add(text_tab_name)
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        self._text_widget = ctk.CTkTextbox(
            text_frame, height=160, wrap="word", font=ctk.CTkFont(size=13)
        )
        self._text_widget.grid(row=0, column=0, sticky="nsew")

        # Placeholder handling.
        self._text_placeholder = "Kirjoita tai liitä teksti tähän..."
        self._text_has_placeholder = True
        self._text_widget.insert("1.0", self._text_placeholder)
        self._text_widget.configure(text_color="gray")
        self._text_widget.bind("<FocusIn>", self._on_text_focus_in)
        self._text_widget.bind("<FocusOut>", self._on_text_focus_out)

        # Build the tab name map.
        self._tab_name_map = {
            pdf_tab_name: "pdf",
            text_tab_name: "text",
        }

    def _on_text_focus_in(self, _event: object = None) -> None:
        if self._text_has_placeholder:
            self._text_widget.delete("1.0", tk.END)
            self._text_widget.configure(text_color=("black", "white"))
            self._text_has_placeholder = False

    def _on_text_focus_out(self, _event: object = None) -> None:
        content = self._text_widget.get("1.0", tk.END).strip()
        if not content:
            self._text_widget.insert("1.0", self._text_placeholder)
            self._text_widget.configure(text_color="gray")
            self._text_has_placeholder = True

    # ---- 2. Settings frame --------------------------------------------

    def _build_settings_frame(self, parent: ctk.CTkFrame, row: int) -> None:
        # Collapsible header bar + hidden body.
        self._settings_open = False

        header_frame = ctk.CTkFrame(parent, fg_color="transparent")
        header_frame.grid(row=row, column=0, sticky="ew", pady=(4, 2))
        header_frame.columnconfigure(0, weight=1)

        self._settings_header_btn = ctk.CTkButton(
            header_frame,
            text="\u25B8 Asetukset",
            command=self._toggle_settings,
            anchor="w",
            height=32,
            fg_color="transparent",
            text_color=("gray20", "gray80"),
            hover_color=("gray90", "gray25"),
        )
        self._settings_header_btn.grid(row=0, column=0, sticky="ew")

        settings_outer = ctk.CTkFrame(parent)
        settings_outer.grid(row=row + 1, column=0, sticky="ew", pady=(0, 8))
        settings_outer.columnconfigure(0, weight=1)
        self._settings_outer = settings_outer
        settings_outer.grid_remove()  # Collapsed by default

        self._settings_frame = ctk.CTkFrame(settings_outer, fg_color="transparent")
        self._settings_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        self._settings_frame.columnconfigure(1, weight=1)
        self._settings_frame.columnconfigure(3, weight=1)
        settings = self._settings_frame

        srow = 0

        # Hidden compatibility widget for legacy engine-status hook points.
        self._engine_status_lbl = ctk.CTkLabel(
            settings, text="", text_color=_CLR_READY, wraplength=560,
        )
        # Not gridded — other code can still call .configure(text=...).

        # Row 0: Language + Speed
        self._tts_lang_label = ctk.CTkLabel(settings, text="Kieli:")
        self._tts_lang_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6)
        )
        self._lang_cb = ctk.CTkComboBox(
            settings,
            values=list(LANGUAGES.keys()), state="readonly", width=140,
            command=self._on_language_changed,
        )
        self._lang_cb.set("Suomi")
        self._lang_cb.grid(row=srow, column=1, sticky="w")

        self._speed_label = ctk.CTkLabel(settings, text="Nopeus:")
        self._speed_label.grid(
            row=srow, column=2, sticky="w", padx=(16, 6)
        )
        self._speed_cb = ctk.CTkComboBox(
            settings,
            values=list(SPEED_OPTIONS["fi"].keys()), state="readonly", width=200,
        )
        self._speed_cb.set("Normaali")
        self._speed_cb.grid(row=srow, column=3, sticky="w")
        srow += 1

        # Row 3: Reference audio (voice cloning) — hidden when unsupported
        self._ref_label = ctk.CTkLabel(settings, text="Ref. ääni:")
        self._ref_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6), pady=(6, 0)
        )
        self._ref_frame = ctk.CTkFrame(settings, fg_color="transparent")
        self._ref_frame.grid(
            row=srow, column=1, columnspan=3, sticky="ew", pady=(6, 0)
        )
        self._ref_frame.columnconfigure(0, weight=1)
        self._ref_audio_var = tk.StringVar(value="")
        self._ref_entry = ctk.CTkEntry(
            self._ref_frame, textvariable=self._ref_audio_var, state="disabled"
        )
        self._ref_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._ref_browse_btn = ctk.CTkButton(
            self._ref_frame, text="Selaa", command=self._browse_reference_audio,
            width=60,
        )
        self._ref_browse_btn.grid(row=0, column=1)
        self._ref_clear_btn = ctk.CTkButton(
            self._ref_frame, text="Tyhjennä", command=self._clear_reference_audio,
            width=80,
        )
        self._ref_clear_btn.grid(row=0, column=2, padx=(4, 0))
        srow += 1

        # Row 4: Voice description — hidden when unsupported
        self._desc_label = ctk.CTkLabel(settings, text="Äänityyli:")
        self._desc_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6), pady=(6, 0)
        )
        self._voice_desc_var = tk.StringVar(value="")
        self._voice_desc_entry = ctk.CTkEntry(
            settings, textvariable=self._voice_desc_var
        )
        self._voice_desc_entry.grid(
            row=srow, column=1, columnspan=3, sticky="ew", pady=(6, 0)
        )
        srow += 1

        # Row 5: Tallenna + Tuloste on the SAME row (merged output controls).
        self._save_label = ctk.CTkLabel(settings, text="Tallenna:")
        self._save_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6), pady=(6, 0)
        )
        out_frame = ctk.CTkFrame(settings, fg_color="transparent")
        out_frame.grid(
            row=srow, column=1, columnspan=3, sticky="ew", pady=(6, 0)
        )
        out_frame.columnconfigure(0, weight=1)

        self._out_entry = ctk.CTkEntry(out_frame, state="disabled")
        self._out_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self._out_browse_btn = ctk.CTkButton(
            out_frame, text="Vaihda\u2026", command=self._browse_output,
            width=80,
        )
        self._out_browse_btn.grid(row=0, column=1, padx=(0, 8))

        self._output_mode_label = ctk.CTkLabel(out_frame, text="Tuloste:")
        self._output_mode_label.grid(row=0, column=2, sticky="w", padx=(0, 4))

        self._output_mode_cb = ctk.CTkComboBox(
            out_frame,
            values=list(OUTPUT_MODES["fi"].keys()), state="readonly",
            width=140,
        )
        self._output_mode_cb.set("Yksi MP3")
        self._output_mode_cb.grid(row=0, column=3, sticky="w")
        # Set initial auto-generated path.
        self._auto_output_path()

        # Initially hide capability-specific widgets.
        self._ref_label.grid_remove()
        self._ref_frame.grid_remove()
        self._desc_label.grid_remove()
        self._voice_desc_entry.grid_remove()

    def _toggle_settings(self) -> None:
        """Show/hide the Asetukset body."""
        self._settings_open = not self._settings_open
        if self._settings_open:
            self._settings_outer.grid()
            self._settings_header_btn.configure(text="\u25BE Asetukset")
        else:
            self._settings_outer.grid_remove()
            self._settings_header_btn.configure(text="\u25B8 Asetukset")

    # ---- 5. Log panel (collapsible) -----------------------------------

    def _build_log_panel(
        self, parent: ctk.CTkFrame, row: int, stretch_row: int
    ) -> None:
        toggle_frame = ctk.CTkFrame(parent, fg_color="transparent")
        toggle_frame.grid(row=row, column=0, sticky="ew", pady=(4, 0))

        self._log_toggle_btn = ctk.CTkButton(
            toggle_frame, text="Piilota loki", command=self._toggle_log,
            width=120,
        )
        self._log_toggle_btn.grid(row=0, column=0, sticky="w")

        self._log_frame = ctk.CTkFrame(parent)
        # Placed in stretch_row so it can grow.
        self._log_frame.grid(
            row=stretch_row, column=0, sticky="nsew", pady=(4, 0)
        )
        self._log_frame.columnconfigure(0, weight=1)
        self._log_frame.rowconfigure(0, weight=1)

        self._log_text = ctk.CTkTextbox(
            self._log_frame, height=200, wrap="word",
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self._log_text.grid(row=0, column=0, sticky="nsew")
        self._log_text.configure(state="disabled")

        # Visible by default (log panel shown on launch).

    # ------------------------------------------------------------------
    # Engine list population
    # ------------------------------------------------------------------

    def _populate_engine_list(self) -> None:
        """Fill the engine combobox from the registry + Chatterbox check.

        Runs check_status() on every engine so the dropdown label
        reflects current availability (e.g. "Piper (ladattava)" when
        voices are missing).
        """
        self._engine_display_to_id.clear()

        # Status dots give the user a glanceable read of each engine:
        #   🟢 ready    🟡 needs download (voice or model)    🔴 not available
        for engine in list_engines():
            dot = "\U0001F7E2"  # green circle — default: ready
            try:
                status = engine.check_status()
                if not status.available:
                    dot = "\U0001F534"  # red circle
                elif status.needs_download:
                    dot = "\U0001F7E1"  # yellow circle
            except Exception:
                pass
            label = f"{dot}  {engine.display_name}"
            self._engine_display_to_id[label] = engine.id

        # Chatterbox-Finnish via subprocess bridge.
        chatterbox_py = resolve_chatterbox_python()
        runner_script = _REPO_ROOT / "scripts" / "generate_chatterbox_audiobook.py"
        if chatterbox_py is not None and runner_script.exists():
            label = "\U0001F7E2  Chatterbox Finnish (paras laatu, NVIDIA)"
            self._engine_display_to_id[label] = "chatterbox_fi"

        labels = list(self._engine_display_to_id.keys())
        self._engine_cb.configure(values=labels)
        if labels and not self._engine_cb.get():
            self._engine_cb.set(labels[0])

    def _current_engine_id(self) -> str:
        display = self._engine_cb.get()
        return self._engine_display_to_id.get(display, "")

    def _current_engine(self) -> Optional[TTSEngine]:
        eid = self._current_engine_id()
        if not eid or eid == "chatterbox_fi":
            return None
        return get_engine(eid)

    def _current_language(self) -> str:
        return LANGUAGES.get(self._lang_cb.get(), "fi")

    def _current_voice(self) -> Optional[Voice]:
        engine = self._current_engine()
        if not engine:
            return None
        display = self._voice_cb.get()
        for voice in engine.list_voices(self._current_language()):
            if voice.display_name == display:
                return voice
        return None

    # ------------------------------------------------------------------
    # Input mode helpers
    # ------------------------------------------------------------------

    @property
    def _input_mode(self) -> str:
        """Return 'pdf' or 'text' based on the active tabview tab."""
        current_tab = self._input_nb.get()
        return self._tab_name_map.get(current_tab, "pdf")

    def _on_tab_changed(self) -> None:
        """Refresh the auto-generated output path when switching tabs."""
        # Update the raw input mode tracker.
        self._input_mode_raw = self._input_mode
        self._auto_output_path()

    def _get_input_text(self) -> str:
        """Return the text to synthesize based on the active input tab."""
        if self._input_mode == "pdf":
            if not self._pdf_path:
                raise ValueError(self._s("no_pdf"))
            book = parse_pdf(self._pdf_path)
            if not book.full_text.strip():
                raise ValueError(self._s("pdf_no_text"))
            return book.full_text
        else:
            if self._text_has_placeholder:
                return ""
            return self._text_widget.get("1.0", tk.END).strip()

    # ------------------------------------------------------------------
    # Engine change
    # ------------------------------------------------------------------

    def _on_engine_changed(self, selection: str = "") -> None:
        self._refresh_voice_list()

    def _on_language_changed(self, selection: str = "") -> None:
        self._refresh_voice_list()

    def _refresh_voice_list(self) -> None:
        """Refresh voices, status label, and capability widgets."""
        eid = self._current_engine_id()

        # Chatterbox is subprocess-only — no voice list from registry.
        if eid == "chatterbox_fi":
            self._voice_cb.configure(values=["Oletus"])
            self._voice_cb.set("Oletus")
            self._engine_status_lbl.configure(text_color=_CLR_READY)
            self._engine_status_lbl.configure(
                text="Offline, paras laatu. Kesto ~1\u20132 h NVIDIA-koneella."
            )
            self._update_capability_widgets(
                supports_cloning=True, supports_description=False
            )
            return

        engine = self._current_engine()
        if engine is None:
            self._voice_cb.configure(values=[])
            self._voice_cb.set("")
            self._engine_status_lbl.configure(text="")
            self._update_capability_widgets(False, False)
            return

        status = engine.check_status()
        if not status.available:
            self._engine_status_lbl.configure(text_color=_CLR_UNAVAILABLE)
            self._engine_status_lbl.configure(text=status.reason)
            self._voice_cb.configure(values=[])
            self._voice_cb.set("")
        elif status.needs_download:
            self._engine_status_lbl.configure(text_color=_CLR_NEEDS_SETUP)
            self._engine_status_lbl.configure(
                text=status.reason + "  Lataus käynnistyy automaattisesti."
            )
            self._populate_voice_combobox(engine)
        else:
            self._engine_status_lbl.configure(text_color=_CLR_READY)
            self._engine_status_lbl.configure(text=engine.description)
            self._populate_voice_combobox(engine)

        self._update_capability_widgets(
            supports_cloning=bool(engine.supports_voice_cloning),
            supports_description=bool(engine.supports_voice_description),
        )

    def _populate_voice_combobox(self, engine: TTSEngine) -> None:
        lang = self._current_language()
        voices = engine.list_voices(lang)
        names = [v.display_name for v in voices]
        self._voice_cb.configure(values=names)
        if names:
            default_id = engine.default_voice(lang)
            default_name = next(
                (v.display_name for v in voices if v.id == default_id),
                names[0],
            )
            self._voice_cb.set(default_name)
        else:
            self._voice_cb.set("")

    def _update_capability_widgets(
        self, supports_cloning: bool, supports_description: bool
    ) -> None:
        for w in (self._ref_label, self._ref_frame):
            if supports_cloning:
                w.grid()
            else:
                w.grid_remove()

        for w in (self._desc_label, self._voice_desc_entry):
            if supports_description:
                w.grid()
            else:
                w.grid_remove()

    # ------------------------------------------------------------------
    # File dialogs
    # ------------------------------------------------------------------

    def _bump_output_path_if_exists(self) -> None:
        """If the current output path already points at an existing file,
        bump it to the next free numbered variant so Muunna never
        overwrites the previous recording.

        Examples:
            texttospeech_3.mp3 (exists) → texttospeech_4.mp3
            book.mp3 (exists)           → book_2.mp3
            book_5.mp3 (exists)         → book_6.mp3
        """
        if not self._output_path:
            return
        target = Path(self._output_path)
        if not target.exists():
            return  # Fresh name, nothing to do.

        stem = target.stem
        suffix = target.suffix or ".mp3"
        parent = target.parent

        # Split trailing _N off the stem, defaulting to 1 if not numbered.
        import re
        match = re.match(r"^(.*?)_(\d+)$", stem)
        if match:
            base, n = match.group(1), int(match.group(2))
        else:
            base, n = stem, 1

        while True:
            n += 1
            candidate = parent / f"{base}_{n}{suffix}"
            if not candidate.exists():
                break

        new_path = str(candidate)
        self._output_path = new_path
        self._out_entry.configure(state="normal")
        self._out_entry.delete(0, tk.END)
        self._out_entry.insert(0, new_path)
        self._out_entry.configure(state="disabled")

    def _default_output_dir(self) -> Path:
        """Return the default folder where generated MP3s go.

        Installed (frozen) mode: next to the running .exe, in an
        "audiobooks" subfolder. Keeps everything in one place and lets
        the user find output without digging through their Documents.

        Dev mode: Documents\\AudiobookMaker (no sensible install root
        when running from source).
        """
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent / "audiobooks"
        return Path.home() / "Documents" / "AudiobookMaker"

    def _auto_output_path(self) -> None:
        """Generate an automatic output path based on current input mode."""
        if self._input_mode == "pdf" and self._pdf_path:
            # Output goes next to the PDF: book.pdf -> book.mp3
            suggested = str(Path(self._pdf_path).with_suffix(".mp3"))
        else:
            # Auto-increment: texttospeech_1.mp3, texttospeech_2.mp3, ...
            out_dir = self._default_output_dir()
            out_dir.mkdir(parents=True, exist_ok=True)
            n = 1
            while True:
                candidate = out_dir / f"texttospeech_{n}.mp3"
                if not candidate.exists():
                    break
                n += 1
            suggested = str(candidate)
        self._out_entry.configure(state="normal")
        self._out_entry.delete(0, tk.END)
        self._out_entry.insert(0, suggested)
        self._out_entry.configure(state="disabled")
        self._output_path = suggested

    def _browse_pdf(self) -> None:
        path = filedialog.askopenfilename(
            title=self._s("select_pdf"),
            filetypes=[("PDF", "*.pdf"), ("*", "*.*")],
        )
        if path:
            self._pdf_path = path
            self._pdf_entry.configure(state="normal")
            self._pdf_entry.delete(0, tk.END)
            self._pdf_entry.insert(0, path)
            self._pdf_entry.configure(state="disabled")
            self._status_label_val.configure(
                text="PDF valittu." if self._ui_lang == "fi" else "PDF selected."
            )
            self._auto_output_path()

    def _browse_reference_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="Valitse referenssiäänitiedosto",
            filetypes=[
                ("Äänitiedostot", "*.wav *.mp3 *.flac *.ogg *.m4a"),
                ("Kaikki tiedostot", "*.*"),
            ],
        )
        if path:
            self._ref_audio_var.set(path)

    def _clear_reference_audio(self) -> None:
        self._ref_audio_var.set("")

    def _browse_output(self) -> None:
        """Let the user pick an output folder. Filename is auto-generated."""
        current = self._out_entry.get()
        initial_dir = str(Path(current).parent) if current else str(Path.home())
        chosen = filedialog.askdirectory(
            title=self._s("save_as"),
            initialdir=initial_dir,
        )
        if not chosen:
            return
        out_dir = Path(chosen)
        out_dir.mkdir(parents=True, exist_ok=True)
        mode = OUTPUT_MODES[self._ui_lang].get(self._output_mode_cb.get(), "single")
        if mode == "single":
            # Auto-increment: texttospeech_1.mp3, texttospeech_2.mp3, ...
            n = 1
            while True:
                candidate = out_dir / f"texttospeech_{n}.mp3"
                if not candidate.exists():
                    break
                n += 1
            path = str(candidate)
        else:
            path = str(out_dir)
        self._output_path = path
        # User explicitly chose this folder — pin the name in the entry.
        # (Auto-increment on next run is still applied via _bump_output_path.)
        self._output_user_chosen = True
        self._out_entry.configure(state="normal")
        self._out_entry.delete(0, tk.END)
        self._out_entry.insert(0, path)
        self._out_entry.configure(state="disabled")

    # ------------------------------------------------------------------
    # Engine installer (placeholder)
    # ------------------------------------------------------------------

    def _open_engine_manager(self) -> None:
        """Legacy entry-point. Routes to the in-place settings view."""
        self._show_settings_view()

    # ------------------------------------------------------------------
    # Update self-heal: fallback when silent install didn't take effect
    # ------------------------------------------------------------------

    def _check_pending_update_marker(self) -> None:
        """If the last update failed, offer a visible-installer fallback.

        apply_update() writes a marker before exiting. On next launch we
        compare our version to the expected one — if we're still old,
        the silent install didn't work. Offer to run the downloaded
        installer visibly via os.startfile.
        """
        from src.auto_updater import (
            verify_pending_update, clear_pending_marker, run_installer_visibly,
            APP_VERSION,
        )
        marker = verify_pending_update(APP_VERSION)
        if marker is None:
            return  # No marker, or update succeeded — nothing to do.

        expected = marker.get("expected_version", "")
        installer_path = Path(marker.get("installer_path", ""))

        if not installer_path.exists():
            # The installer file is gone — can't recover automatically.
            clear_pending_marker()
            self._append_log(
                f"Aiempi päivitys v{expected} epäonnistui; "
                "asennustiedosto ei ole enää saatavilla."
            )
            return

        # Tell the user and offer the visible fallback.
        if self._ui_lang == "fi":
            msg = (
                f"Automaattinen päivitys versioon {expected} epäonnistui. "
                f"Haluatko käynnistää asentajan nyt?\n\n"
                f"Asennustiedosto: {installer_path.name}"
            )
            title = "Päivityksen korjaus"
        else:
            msg = (
                f"The auto-update to version {expected} did not take effect. "
                f"Run the installer now?\n\n"
                f"Installer: {installer_path.name}"
            )
            title = "Update recovery"

        if messagebox.askyesno(title, msg):
            self._append_log(
                f"Käynnistetään näkyvä asennus: {installer_path}"
            )
            try:
                run_installer_visibly(installer_path)
                # Must exit so the installer can replace our files.
                self.after(100, lambda: os._exit(0))
            except Exception as exc:
                messagebox.showerror(title, str(exc))
                clear_pending_marker()
        else:
            # User declined — clear the marker so we don't nag them again.
            clear_pending_marker()

    # ------------------------------------------------------------------
    # Old install / orphan shortcut cleanup
    # ------------------------------------------------------------------

    def _check_for_old_installs(self) -> None:
        """Silent background cleanup of old installs and orphan shortcuts.

        Runs once on startup. Removes stale items automatically and logs
        what was done to the loki panel. Never shows a popup — the user
        should not have to manage leftovers from previous versions.
        """
        def worker() -> None:
            try:
                from src.cleanup import (
                    find_old_installs, find_orphan_shortcuts,
                    remove_old_install, remove_orphan_shortcut,
                )
                old = find_old_installs()
                orphans = find_orphan_shortcuts()
                if not old and not orphans:
                    return

                removed = 0
                for inst in old:
                    ok, _ = remove_old_install(inst)
                    if ok:
                        removed += 1
                        self.after(0, lambda p=inst.path: self._append_log(
                            f"Poistettu vanha asennus: {p}"
                        ))
                for short in orphans:
                    ok, _ = remove_orphan_shortcut(short)
                    if ok:
                        removed += 1
                        self.after(0, lambda p=short.shortcut_path: self._append_log(
                            f"Poistettu rikkinäinen pikakuvake: {p.name}"
                        ))
            except Exception as exc:
                self.after(0, lambda: self._append_log_warning(
                    f"Siivous epäonnistui: {exc}"
                ))

        threading.Thread(
            target=worker, daemon=True, name="cleanup-scan",
        ).start()

    # ------------------------------------------------------------------
    # Auto-update (periodic check + download + install)
    # ------------------------------------------------------------------

    UPDATE_CHECK_INTERVAL_MS = 5 * 60 * 1000  # 5 minutes

    def _check_update_worker(self) -> None:
        """Background thread: check GitHub for a newer release."""
        try:
            info = check_for_update(APP_VERSION)
            self._update_queue.put(info)
        except Exception:
            pass  # Never crash on update check failure.

    def _poll_update_check(self) -> None:
        """Main thread: pick up the result from the update check thread."""
        try:
            info = self._update_queue.get_nowait()
            if info.available:
                self._pending_update = info
                self._show_update_banner(info)
        except queue.Empty:
            pass

        # Schedule the next periodic check.
        if getattr(sys, "frozen", False):
            self.after(
                self.UPDATE_CHECK_INTERVAL_MS,
                self._schedule_update_check,
            )

    def _schedule_update_check(self) -> None:
        """Launch a new background update check and poll for results."""
        threading.Thread(
            target=self._check_update_worker, daemon=True,
            name="update-check-periodic",
        ).start()
        self.after(500, self._poll_update_check)

    def _show_update_banner(self, info: UpdateInfo) -> None:
        """Show the update banner with version info."""
        msg = self._s("update_available").format(version=info.latest_version)
        self._update_label.configure(text=msg)
        self._update_banner.grid()

    def _on_update_click(self) -> None:
        """User clicked 'Update now' — download and install."""
        if self._pending_update is None:
            return
        self._update_btn.configure(
            state="disabled", text=self._s("update_downloading"),
        )
        threading.Thread(
            target=self._download_and_apply_update, daemon=True,
            name="update-download",
        ).start()

    def _download_and_apply_update(self) -> None:
        """Background thread: download installer, then apply on main thread."""
        try:
            update = self._pending_update
            if update is None:
                return

            def progress_cb(done: int, total: int) -> None:
                if total > 0:
                    self.after(0, lambda: self._progress_bar.set(done / total))

            path = download_update(update, progress_cb=progress_cb)
            # Apply must run on the main thread (it calls sys.exit).
            self.after(0, lambda: apply_update(path))
        except Exception as exc:
            self.after(0, lambda: self._update_btn.configure(
                state="normal", text=self._s("update_now"),
            ))
            self.after(0, lambda: messagebox.showerror(
                self._s("error"), f"{self._s('update_failed')}\n{exc}",
            ))

    # ------------------------------------------------------------------
    # Voice preview
    # ------------------------------------------------------------------

    def _on_test_voice(self) -> None:
        if self._testing_voice:
            return
        engine = self._current_engine()
        voice = self._current_voice()
        eid = self._current_engine_id()

        if eid == "chatterbox_fi":
            messagebox.showinfo(
                "Ääninäyte",
                "Chatterbox ei tue ääninäytettä tästä käyttöliittymästä."
            )
            return

        if engine is None or voice is None:
            messagebox.showerror(self._s("error"), "Valitse ensin moottori ja ääni.")
            return

        self._testing_voice = True
        self._test_btn.configure(state="disabled")
        self._status_label_val.configure(text="Syntetisoidaan ääninäytettä\u2026")

        threading.Thread(
            target=self._test_voice_worker, daemon=True, name="voice-test"
        ).start()

    def _test_voice_worker(self) -> None:
        try:
            engine = self._current_engine()
            voice = self._current_voice()
            if engine is None or voice is None:
                return
            lang = self._current_language()
            text = _SAMPLE_TEXT.get(lang, _SAMPLE_TEXT["fi"])

            tmp = tempfile.NamedTemporaryFile(
                prefix="sample_", suffix=".mp3", delete=False
            )
            tmp.close()

            ref_audio = self._ref_audio_var.get() or None
            voice_desc = self._voice_desc_var.get() or None

            engine.synthesize(
                text, tmp.name, voice.id, lang,
                lambda c, t, m: None,
                reference_audio=ref_audio,
                voice_description=voice_desc,
            )
            self._safe_play_sample(tmp.name)
        except Exception as exc:
            self.after(0, lambda: self._status_label_val.configure(
                text=f"Näyteen luonti epäonnistui: {exc}"
            ))
        finally:
            self.after(0, lambda: self._test_btn.configure(state="normal"))
            self.after(0, lambda: setattr(self, "_testing_voice", False))

    def _safe_play_sample(self, path: str) -> None:
        def _play() -> None:
            self._status_label_val.configure(text=f"Ääninäyte tallennettu: {path}")
            try:
                if sys.platform == "win32":
                    os.startfile(path)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
            except Exception:
                pass
        self.after(0, _play)

    # ------------------------------------------------------------------
    # Listen (synthesize + play without saving)
    # ------------------------------------------------------------------

    def _find_ffplay(self) -> Optional[str]:
        """Locate ffplay, checking the bundled ffmpeg dir first."""
        path = shutil.which("ffplay")
        if path:
            return path
        # On Windows, also check for ffplay.exe next to ffmpeg.
        ffmpeg_dir = get_ffmpeg_dir()
        if ffmpeg_dir:
            candidate = os.path.join(ffmpeg_dir, "ffplay.exe")
            if os.path.isfile(candidate):
                return candidate
        return None

    def _on_listen_click(self) -> None:
        if self._listening or self._synth_running:
            return

        # Priority 1: if a finished MP3 already exists at the output path,
        # just play that file. Works for every engine (Chatterbox included)
        # after a successful conversion — the user gets instant playback
        # instead of a new synthesis run.
        if self._output_path:
            out_file = Path(self._output_path)
            if out_file.is_file() and out_file.suffix.lower() == ".mp3":
                self._append_log(f"Toistetaan: {out_file}")
                try:
                    if sys.platform == "win32":
                        os.startfile(str(out_file))  # type: ignore[attr-defined]
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", str(out_file)])
                    else:
                        subprocess.Popen(["xdg-open", str(out_file)])
                except Exception as exc:
                    self._append_log_error(f"\u2718 Toisto epäonnistui: {exc}")
                return

        # Priority 2: synthesize a short preview from the input text.
        if self._input_mode == "text":
            if self._text_has_placeholder:
                text = ""
            else:
                text = self._text_widget.get("1.0", tk.END).strip()
            if not text:
                messagebox.showerror(self._s("error"), self._s("listen_no_text"))
                return
        else:
            # PDF mode: synthesize first paragraph as preview.
            if not self._pdf_path:
                messagebox.showerror(self._s("error"), self._s("no_pdf"))
                return
            try:
                book = parse_pdf(self._pdf_path)
                text = book.full_text
            except Exception as exc:
                messagebox.showerror(self._s("error"), str(exc))
                return
            if not text:
                messagebox.showerror(self._s("error"), self._s("listen_no_text"))
                return

        # Truncate long text to 1000 chars for preview.
        if len(text) > 1000:
            text = text[:1000]

        # Validate engine/voice.
        engine_id = self._current_engine_id()
        if not engine_id:
            messagebox.showerror(self._s("error"), "Valitse TTS-moottori.")
            return

        if engine_id == "chatterbox_fi":
            # Chatterbox is too slow for an on-demand preview, and there's
            # no existing MP3 to play. Tell the user to run Muunna first.
            messagebox.showinfo(
                self._s("listen"),
                "Muunna teksti ensin \u2014 Esikuuntele toistaa sen jälkeen "
                "valmiin MP3-tiedoston."
                if self._ui_lang == "fi"
                else "Convert the text first \u2014 Preview plays the "
                "resulting MP3 file afterwards."
            )
            return

        engine = self._current_engine()
        if engine is None:
            messagebox.showerror(self._s("error"), "Moottoria ei löytynyt.")
            return
        status = engine.check_status()
        if not status.available:
            messagebox.showerror(
                self._s("error"), f"{engine.display_name}: {status.reason}"
            )
            return
        voice = self._current_voice()
        if voice is None:
            messagebox.showerror(self._s("error"), "Valitse ääni.")
            return

        # Enter listening state.
        self._listening = True
        self._listen_btn.configure(state="disabled", text=self._s("listening"))
        self._convert_btn.configure(state="disabled")
        self._status_label_val.configure(text=self._s("listening"))

        threading.Thread(
            target=self._listen_worker,
            args=(text, engine, voice),
            daemon=True,
            name="listen",
        ).start()

    def _listen_worker(
        self, text: str, engine: TTSEngine, voice: Voice,
    ) -> None:
        import time
        tmp_path: Optional[str] = None
        try:
            lang = self._current_language()
            ref_audio = self._ref_audio_var.get() or None
            voice_desc = self._voice_desc_var.get() or None

            preview_len = len(text)
            self.after(0, lambda: self._append_log(
                f"Kuuntele: {engine.display_name}, {voice.display_name}"
            ))
            self.after(0, lambda: self._append_log(
                f"Teksti: {preview_len} merkkiä"
            ))

            tmp = tempfile.NamedTemporaryFile(
                prefix="listen_", suffix=".mp3", delete=False
            )
            tmp.close()
            tmp_path = tmp.name
            self._listen_temp_path = tmp_path

            t0 = time.perf_counter()
            self.after(0, lambda: self._append_log("Syntetisoidaan..."))

            engine.synthesize(
                text, tmp_path, voice.id, lang,
                lambda c, t, m: None,
                reference_audio=ref_audio,
                voice_description=voice_desc,
            )

            elapsed = time.perf_counter() - t0
            size_kb = os.path.getsize(tmp_path) / 1024
            self.after(0, lambda: self._append_log_success(
                f"\u2714 Synteesi valmis: {elapsed:.1f}s, {size_kb:.0f} KB"
            ))

            self.after(0, lambda: self._append_log("Toistetaan ääntä..."))

            # Play via the system's default audio player.
            if sys.platform == "win32":
                os.startfile(tmp_path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", tmp_path])
            else:
                subprocess.Popen(["xdg-open", tmp_path])
            # Give the player time to open the file before cleanup.
            time.sleep(2)

        except Exception as exc:
            self.after(0, lambda: self._append_log_error(f"\u2718 Virhe: {exc}"))
            self.after(0, lambda: self._status_label_val.configure(
                text=f"Listen error: {exc}"
            ))
        finally:
            # Don't delete temp file — the external player still needs it.
            # OS temp cleanup will handle it eventually.
            self._listen_temp_path = None
            self._listen_proc = None
            self.after(0, self._listen_finished)

    def _listen_finished(self) -> None:
        self._listening = False
        self._listen_btn.configure(state="normal", text=self._s("listen"))
        self._convert_btn.configure(state="normal")
        if not self._synth_running:
            self._status_label_val.configure(text=self._s("select_input_prompt"))

    # ------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------

    def _apply_loaded_config(self) -> None:
        """Restore persisted preferences to widgets."""
        cfg = self._user_cfg

        # Language.
        for label, code in LANGUAGES.items():
            if code == cfg.language:
                self._lang_cb.set(label)
                break

        # Engine.
        engine = get_engine(cfg.engine_id)
        if engine and engine.check_status().available:
            self._engine_cb.set(engine.display_name)
        elif cfg.engine_id == "chatterbox_fi":
            for lbl, eid in self._engine_display_to_id.items():
                if eid == "chatterbox_fi":
                    self._engine_cb.set(lbl)
                    break

        self._refresh_voice_list()

        # Voice.
        eng = self._current_engine()
        if eng and cfg.voice_id:
            for voice in eng.list_voices(self._current_language()):
                if voice.id == cfg.voice_id:
                    self._voice_cb.set(voice.display_name)
                    break

        # Speed.
        for label, val in SPEED_OPTIONS[self._ui_lang].items():
            if val == cfg.speed:
                self._speed_cb.set(label)
                break

        # Reference audio + voice description.
        if cfg.reference_audio:
            self._ref_audio_var.set(cfg.reference_audio)
        if cfg.voice_description:
            self._voice_desc_var.set(cfg.voice_description)

        # Input mode tab.
        if cfg.input_mode == "text":
            # Find the text tab name from the map.
            for tab_name, mode in self._tab_name_map.items():
                if mode == "text":
                    self._input_nb.set(tab_name)
                    self._input_mode_raw = "text"
                    break

        # Output mode.
        for label, val in OUTPUT_MODES[self._ui_lang].items():
            if val == cfg.output_mode:
                self._output_mode_cb.set(label)
                break

        # Log panel visibility (visible by default; hide if user previously chose to).
        if not cfg.log_panel_visible:
            self._toggle_log()

    def _save_current_config(self) -> None:
        """Snapshot current UI state into on-disk config."""
        cfg = self._user_cfg
        cfg.engine_id = self._current_engine_id() or "edge"
        voice = self._current_voice()
        cfg.voice_id = voice.id if voice else ""
        cfg.language = self._current_language()
        cfg.speed = SPEED_OPTIONS[self._ui_lang].get(self._speed_cb.get(), "+0%")
        cfg.reference_audio = self._ref_audio_var.get()
        cfg.voice_description = self._voice_desc_var.get()
        cfg.input_mode = self._input_mode
        cfg.output_mode = OUTPUT_MODES[self._ui_lang].get(
            self._output_mode_cb.get(), "single"
        )
        cfg.log_panel_visible = self._log_visible
        cfg.ui_language = self._ui_lang
        app_config.save(cfg)

    # ------------------------------------------------------------------
    # Conversion dispatch
    # ------------------------------------------------------------------

    def _on_convert_click(self) -> None:
        if self._synth_running:
            return

        # Validate input.
        if self._input_mode == "pdf" and not self._pdf_path:
            messagebox.showerror(self._s("error"), self._s("no_pdf"))
            return
        if self._input_mode == "text":
            content = "" if self._text_has_placeholder else self._text_widget.get("1.0", tk.END).strip()
            if not content:
                messagebox.showerror(self._s("error"), self._s("no_text"))
                return

        if not self._output_path:
            self._auto_output_path()
        if not self._output_path:
            messagebox.showerror(self._s("error"), self._s("no_pdf"))
            return

        # Never overwrite an existing file — if the target already exists
        # (from a previous Muunna press this session, or from an earlier
        # run that auto-picked the same number), bump the filename to the
        # next free variant.
        self._bump_output_path_if_exists()

        engine_id = self._current_engine_id()
        if not engine_id:
            messagebox.showerror(self._s("error"), "Valitse TTS-moottori.")
            return

        # For registry engines, verify availability.
        if engine_id != "chatterbox_fi":
            engine = self._current_engine()
            if engine is None:
                messagebox.showerror(self._s("error"), "Moottoria ei löytynyt.")
                return
            status = engine.check_status()
            if not status.available:
                messagebox.showerror(
                    self._s("error"), f"{engine.display_name}: {status.reason}"
                )
                return
            voice = self._current_voice()
            if voice is None:
                messagebox.showerror(self._s("error"), "Valitse ääni.")
                return

        # Disk-space sanity check for the output drive.
        try:
            from src.system_checks import check_output_disk_space
            if self._input_mode == "pdf":
                from src.pdf_parser import parse_pdf
                try:
                    text_len = len(parse_pdf(self._pdf_path).full_text)
                except Exception:
                    text_len = 0
            else:
                text_len = len(
                    "" if self._text_has_placeholder
                    else self._text_widget.get("1.0", tk.END).strip()
                )
            ok, free_mb, need_mb = check_output_disk_space(
                self._output_path, text_len, engine_id,
            )
            if not ok:
                self._append_log_error(
                    f"\u2718 Levytilaa ei riitä: vapaa {free_mb:.0f} MB, "
                    f"tarvitaan ~{need_mb:.0f} MB"
                )
                messagebox.showerror(
                    self._s("error"),
                    f"Levytilaa ei riitä tulostekansiossa.\n\n"
                    f"Vapaa: {free_mb:.0f} MB\n"
                    f"Tarvitaan: ~{need_mb:.0f} MB\n\n"
                    f"Vapauta tilaa tai valitse toinen tallennuspaikka."
                    if self._ui_lang == "fi"
                    else
                    f"Not enough disk space at the output path.\n\n"
                    f"Free: {free_mb:.0f} MB\n"
                    f"Needed: ~{need_mb:.0f} MB\n\n"
                    f"Free some space or pick a different save location."
                )
                return
            self._append_log(
                f"Levy: vapaa {free_mb:.0f} MB, arvioitu tarve {need_mb:.0f} MB"
            )
        except Exception as exc:
            self._append_log_warning(f"Disk check skipped: {exc}")

        # Persist settings before synthesis.
        self._save_current_config()

        # Enter running state.
        self._set_running_state()

        if engine_id == "chatterbox_fi":
            self._start_chatterbox_subprocess()
        else:
            self._start_inprocess_engine(engine_id)

        self.after(self.POLL_INTERVAL_MS, self._pump_events)

    # ------------------------------------------------------------------
    # In-process engine synthesis (Edge-TTS, Piper, VoxCPM2)
    # ------------------------------------------------------------------

    def _start_inprocess_engine(self, engine_id: str) -> None:
        """Start synthesis in a background thread for registry engines."""
        self._append_log(f"Engine: {engine_id}")
        self._append_log(f"Output: {self._output_path}")
        # Capture input on the main thread (thread-safe).
        input_mode = self._input_mode
        pdf_path = self._pdf_path
        input_text = None
        if input_mode == "text" and not self._text_has_placeholder:
            input_text = self._text_widget.get("1.0", tk.END).strip()
        output_path = self._output_path
        voice = self._current_voice()
        voice_id = voice.id if voice else None
        language = self._current_language()
        speed = SPEED_OPTIONS[self._ui_lang].get(self._speed_cb.get(), "+0%")
        ref_audio = self._ref_audio_var.get() or None
        voice_desc = self._voice_desc_var.get() or None

        threading.Thread(
            target=self._run_inprocess,
            args=(engine_id, input_mode, pdf_path, input_text,
                  output_path, voice_id, language, speed,
                  ref_audio, voice_desc),
            daemon=True, name=f"tts-{engine_id}",
        ).start()

    def _run_inprocess(
        self, engine_id: str, input_mode: str,
        pdf_path: Optional[str], input_text: Optional[str],
        output_path: Optional[str], voice_id: Optional[str],
        language: str, speed: str,
        ref_audio: Optional[str], voice_desc: Optional[str],
    ) -> None:
        """Background thread for in-process TTS synthesis."""
        try:
            engine = get_engine(engine_id)
            if engine is None:
                raise RuntimeError(f"Engine '{engine_id}' not found.")

            self._event_queue.put(
                ProgressEvent(kind="log", raw_line="Reading input...")
            )

            if input_mode == "pdf":
                assert pdf_path is not None
                book = parse_pdf(pdf_path)
                text = book.full_text
            else:
                text = input_text or ""

            if not text:
                raise ValueError("No text to synthesize.")

            if voice_id is None:
                voice_id = engine.default_voice(language)
                if voice_id is None:
                    raise RuntimeError("No voice available for the selected language.")

            self._event_queue.put(
                ProgressEvent(kind="log", raw_line=f"Synthesizing ({len(text)} chars)...")
            )

            # Create output directory.
            out = Path(output_path) if output_path else (
                self._default_output_dir() / "output.mp3"
            )
            out.parent.mkdir(parents=True, exist_ok=True)

            def progress_cb(current: int, total: int, msg: str = "") -> None:
                pct = (current / total) if total > 0 else 0
                self._event_queue.put(ProgressEvent(
                    kind="chunk",
                    total_done=current,
                    total_chunks=total,
                    raw_line=msg or f"Chunk {current}/{total}",
                ))

            engine.synthesize(
                text=text,
                output_path=str(out),
                voice_id=voice_id,
                language=language,
                progress_cb=progress_cb,
                reference_audio=ref_audio,
                voice_description=voice_desc,
            )

            self._event_queue.put(ProgressEvent(
                kind="done",
                output_path=str(out),
                raw_line=f"Saved: {out}",
            ))

        except Exception as exc:
            self._event_queue.put(ProgressEvent(
                kind="error",
                raw_line=str(exc),
            ))

    # ------------------------------------------------------------------
    # Chatterbox subprocess relay
    # ------------------------------------------------------------------

    def _relay_chatterbox_events(self) -> None:
        """Drain ChatterboxRunner events into the GUI queue."""
        runner = self._chatterbox_runner
        if runner is None:
            return
        while not runner.finished:
            ev = runner.poll_event(timeout=0.2)
            if ev is not None:
                self._event_queue.put(ev)
        # Final drain.
        while True:
            ev = runner.poll_event(timeout=0.05)
            if ev is None:
                break
            self._event_queue.put(ev)

    # ------------------------------------------------------------------
    # Event pump (main thread — processes events from background threads)
    # ------------------------------------------------------------------

    def _pump_events(self) -> None:
        """Poll the event queue and update UI. Reschedules itself while running."""
        while True:
            try:
                ev = self._event_queue.get_nowait()
            except queue.Empty:
                break

            # Route the line to the right severity color (yellow for
            # WARNING/FutureWarning/DeprecationWarning, red for ERROR /
            # Traceback, green for success markers). _log_line_by_severity
            # wraps _append_log_* with keyword heuristics so subprocess
            # stdout from Chatterbox gets the same treatment as in-process
            # events.
            if ev.raw_line:
                self._log_line_by_severity(ev.raw_line, kind=ev.kind)

            if ev.kind == "chunk":
                if ev.total_chunks > 0:
                    pct = ev.total_done / ev.total_chunks
                    self._progress_bar.set(pct)
                    self._status_label_val.configure(
                        text=f"{ev.total_done}/{ev.total_chunks}"
                    )

            elif ev.kind in ("chapter_done", "full_done"):
                # Chatterbox writes to {out_dir}/{book_stem}/NN_title.mp3.
                # Remember the last produced MP3 so we can copy it to the
                # user's requested output path when the job finishes.
                self._chatterbox_last_mp3 = ev.output_path

            elif ev.kind == "done":
                self._progress_bar.set(1.0)
                self._status_label_val.configure(text=self._s("done"))
                # If this was Chatterbox, move its output to where the user
                # asked for it. Chatterbox doesn't honor a --output-file path.
                self._finalize_chatterbox_output_if_needed()
                if ev.output_path:
                    self._output_path = ev.output_path
                # Show a friendly green summary at the bottom of the log.
                if self._output_path:
                    self._log_success_summary(self._output_path)
                self._set_idle_state()
                return  # Stop pumping.

            elif ev.kind == "error":
                self._fail(ev.raw_line or "Unknown error")
                return  # Stop pumping.

        # Reschedule if still running.
        if self._synth_running:
            self.after(self.POLL_INTERVAL_MS, self._pump_events)

    def _log_line_by_severity(self, line: str, kind: str = "log") -> None:
        """Append a log line and color-code it based on kind + keywords.

        Called from both the main pump loop and the _handle_event mixin
        method so Chatterbox subprocess stdout gets the same treatment.
        """
        upper = line.upper()
        if (kind == "error"
            or "ERROR:" in upper
            or "TRACEBACK" in upper
            or "\u2718" in line):
            self._append_log_error(line)
        elif ("WARNING" in upper
              or "WARN:" in upper
              or "FUTUREWARNING" in upper
              or "DEPRECATIONWARNING" in upper):
            self._append_log_warning(line)
        elif "\u2714" in line or "VALMIS" in upper or line.startswith("[done]"):
            self._append_log_success(line)
        else:
            self._append_log(line)

    def _finalize_chatterbox_output_if_needed(self) -> None:
        """Copy Chatterbox's per-chapter MP3 to the user's target path.

        Chatterbox writes `{out_dir}/{book_stem}/01_Title.mp3` (and
        `00_full.mp3` for multi-chapter books). The user requested a
        single file at `self._output_path` — copy the result there and
        log the final location.
        """
        src = getattr(self, "_chatterbox_last_mp3", "")
        if not src or not self._output_path:
            return

        src_path = Path(src)
        if not src_path.is_absolute():
            # Chatterbox reports relative paths for chapters; resolve
            # against the out_dir passed to the runner.
            runner = self._chatterbox_runner
            if runner is None:
                return
            src_path = Path(runner.out_dir) / src_path
            if not src_path.exists():
                # Chatterbox nests: {out_dir}/{book_stem}/NN_*.mp3
                # Try finding the newest .mp3 under out_dir.
                candidates = sorted(
                    Path(runner.out_dir).rglob("*.mp3"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if candidates:
                    src_path = candidates[0]

        dst_path = Path(self._output_path)
        if src_path.resolve() == dst_path.resolve():
            return  # Already at target

        if not src_path.exists():
            self._append_log(f"Chatterbox output not found: {src_path}")
            return

        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
            self._append_log(f"Saved: {dst_path}")
            self._chatterbox_last_mp3 = ""
        except OSError as exc:
            self._append_log(f"Could not move output to {dst_path}: {exc}")

    # ------------------------------------------------------------------
    # Running/idle state management
    # ------------------------------------------------------------------

    def _set_running_state(self) -> None:
        self._synth_running = True
        self._convert_btn.configure(state="disabled")
        self._listen_btn.configure(state="disabled")
        self._cancel_btn.grid()
        self._progress_bar.set(0)
        self._status_label_val.configure(text=self._s("converting"))
        self._clear_log()

    def _set_idle_state(self) -> None:
        self._synth_running = False
        self._convert_btn.configure(state="normal")
        self._listen_btn.configure(state="normal")
        self._cancel_btn.grid_remove()
        self._open_folder_btn.configure(state="normal")
        self._chatterbox_runner = None

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def _on_cancel_click(self) -> None:
        if self._chatterbox_runner is not None:
            self._chatterbox_runner.cancel()
        self._cancel_requested = True
        self._cancel_btn.configure(text=self._s("cancelling"), state="disabled")

    # ------------------------------------------------------------------
    # Output folder
    # ------------------------------------------------------------------

    def _open_output_folder(self) -> None:
        if not self._output_path:
            return
        folder = Path(self._output_path)
        if folder.is_file():
            folder = folder.parent
        if not folder.exists():
            return
        if sys.platform == "win32":
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    # ------------------------------------------------------------------
    # Log panel
    # ------------------------------------------------------------------

    def _toggle_log(self) -> None:
        if self._log_visible:
            self._log_frame.grid_remove()
            self._log_toggle_btn.configure(text=self._s("show_log"))
        else:
            self._log_frame.grid()
            self._log_toggle_btn.configure(text=self._s("hide_log"))
        self._log_visible = not self._log_visible

    def _clear_log(self) -> None:
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", tk.END)
        self._log_text.configure(state="disabled")

    def _append_log(self, line: str) -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert(tk.END, line + "\n")
        self._log_text.see(tk.END)
        self._log_text.configure(state="disabled")

    def _append_log_styled(self, line: str, severity: str = "info") -> None:
        """Append a log line with a colored/bold tag based on severity.

        severity values:
          success → bold green
          warning → bold amber/yellow (for issues, not failures)
          error   → bold red
          info    → default (no styling)
        """
        self._log_text.configure(state="normal")
        start_index = self._log_text.index(tk.END + "-1c")
        self._log_text.insert(tk.END, line + "\n")
        end_index = self._log_text.index(tk.END + "-1c")

        palette = {
            "success": ("#2e7d32", ("Consolas", 12, "bold")),   # dark green
            "warning": ("#b8860b", ("Consolas", 12, "bold")),   # dark goldenrod
            "error":   ("#c62828", ("Consolas", 12, "bold")),   # dark red
        }
        if severity in palette:
            color, font_tuple = palette[severity]
            try:
                inner = self._log_text._textbox  # type: ignore[attr-defined]
                inner.tag_configure(
                    f"log_{severity}",
                    foreground=color, font=font_tuple,
                )
                inner.tag_add(f"log_{severity}", start_index, end_index)
            except Exception:
                pass  # Fall back to plain text if tag setup fails

        self._log_text.see(tk.END)
        self._log_text.configure(state="disabled")

    def _append_log_success(self, line: str) -> None:
        self._append_log_styled(line, severity="success")

    def _append_log_warning(self, line: str) -> None:
        self._append_log_styled(line, severity="warning")

    def _append_log_error(self, line: str) -> None:
        self._append_log_styled(line, severity="error")

    def _log_success_summary(self, saved_path: str, elapsed_s: Optional[float] = None) -> None:
        """Write a friendly final summary line to the log after a job finishes."""
        size_note = ""
        try:
            size_kb = Path(saved_path).stat().st_size / 1024
            if size_kb > 1024:
                size_note = f" ({size_kb / 1024:.1f} MB)"
            else:
                size_note = f" ({size_kb:.0f} KB)"
        except OSError:
            pass

        time_note = ""
        if elapsed_s is not None and elapsed_s > 0:
            if elapsed_s >= 60:
                time_note = f" \u2014 {int(elapsed_s // 60)}m {int(elapsed_s % 60)}s"
            else:
                time_note = f" \u2014 {elapsed_s:.1f}s"

        if self._ui_lang == "fi":
            msg = f"\u2714 Valmis! Tallennettu: {saved_path}{size_note}{time_note}"
        else:
            msg = f"\u2714 Done! Saved: {saved_path}{size_note}{time_note}"
        self._append_log_success(msg)

    # ------------------------------------------------------------------
    # Error helper
    # ------------------------------------------------------------------

    def _fail(self, message: str) -> None:
        self._set_idle_state()
        self._status_label_val.configure(text=message)
        messagebox.showerror(self._s("error"), message)


# ---------------------------------------------------------------------------
# Self-test (for CI / frozen-exe verification)
# ---------------------------------------------------------------------------


def assert_true(condition: bool, msg: str = "") -> None:
    """Raise AssertionError if *condition* is falsy."""
    if not condition:
        raise AssertionError(msg or "assertion failed")


def self_test() -> int:
    """Headless sanity check: construct + destroy the window, verify core functionality."""
    errors: list[str] = []

    def check(label: str, fn) -> None:  # noqa: ANN001
        try:
            fn()
            print(f"[self-test] \u2713 {label}", flush=True)
        except Exception as exc:
            errors.append(f"{label}: {exc!r}")
            print(f"[self-test] \u2717 {label}: {exc!r}", flush=True)

    try:
        # -- Engine registry --------------------------------------------------
        engines = list_engines()
        print(
            f"[self-test] engines registered: {[e.id for e in engines]}",
            flush=True,
        )

        for engine in engines:
            check(
                f"engine '{engine.id}' status",
                lambda e=engine: e.check_status(),
            )

        # -- Module imports ----------------------------------------------------
        check("pdf_parser import", lambda: __import__("src.pdf_parser"))
        check("tts_engine import", lambda: __import__("src.tts_engine"))

        # -- App creation ------------------------------------------------------
        app = UnifiedApp()
        app.update_idletasks()
        print(f"[self-test] window title={app.title()!r}", flush=True)

        # -- Engine dropdown ---------------------------------------------------
        values = list(app._engine_cb.cget("values"))
        check(
            "engine dropdown populated",
            lambda: assert_true(len(values) >= 1, f"got {len(values)} engines"),
        )
        print(f"[self-test] engine dropdown: {values}", flush=True)

        # -- Settings widgets --------------------------------------------------
        check("language combobox", lambda: app._lang_cb.cget("values"))
        check("speed combobox", lambda: app._speed_cb.cget("values"))
        check("voice combobox", lambda: app._voice_cb.cget("values"))

        # -- Input tabs --------------------------------------------------------
        check(
            "input tabs exist",
            lambda: assert_true(
                hasattr(app, "_input_nb"), "missing _input_nb tabview"
            ),
        )
        tab_names = list(app._tab_name_map.keys())
        check(
            "input tabs switchable",
            lambda: [app._input_nb.set(t) for t in tab_names],
        )
        print(f"[self-test] input tabs: {tab_names}", flush=True)

        # -- Text widget -------------------------------------------------------
        check(
            "text widget input",
            lambda: (
                app._text_widget.delete("1.0", "end"),
                app._text_widget.insert("1.0", "Self-test input"),
            ),
        )

        # -- Config load/save --------------------------------------------------
        from src import app_config

        check("config load", lambda: app_config.load())
        cfg = app_config.load()
        check("config save", lambda: app_config.save(cfg))

        # -- Cleanup -----------------------------------------------------------
        app.destroy()

        if errors:
            print(f"\n[self-test] FAILED \u2014 {len(errors)} error(s):", flush=True)
            for e in errors:
                print(f"  \u2022 {e}", flush=True)
            return 1

        print("[self-test] ALL CHECKS PASSED", flush=True)
        return 0

    except Exception as exc:
        print(f"[self-test] FATAL: {exc!r}", flush=True, file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run() -> None:
    """Launch the unified GUI application."""
    app = UnifiedApp()
    app.mainloop()


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if "--self-test" in argv:
        return self_test()
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
