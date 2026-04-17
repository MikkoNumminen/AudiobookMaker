"""Engine manager UI — Toplevel dialog and embedded view.

Extracted from ``gui_unified.py`` to keep the main window module smaller.
Two public classes:

* :class:`EngineManagerDialog` — modal ``CTkToplevel`` (legacy entry point).
* :class:`EngineManagerView` — embedded ``CTkFrame`` variant used by the
  in-place settings page.

Both share the same install / uninstall / progress-polling logic; the view
imports the dialog's methods by reference so there's only one source of
truth.
"""

from __future__ import annotations

import os
import queue
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Optional

import customtkinter as ctk

from src import gui_style


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
        "back": "Takaisin",
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
        "back": "Back",
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

        # Secondary-style back button: slate surface (not accent blue) with a
        # bold chevron glyph so the direction reads at a glance.
        self._back_btn = ctk.CTkButton(
            header,
            text=f"\u2B9C  {self._s('back')}",
            width=110,
            height=36,
            corner_radius=gui_style.RADIUS_SM,
            fg_color=gui_style.BTN_SECONDARY_BG,
            hover_color=gui_style.BTN_SECONDARY_HOVER,
            text_color=gui_style.TEXT_PRIMARY,
            font=gui_style.font_button(),
            command=self._on_back_click,
            anchor="w",
        )
        self._back_btn.grid(row=0, column=0, sticky="w")

        self._title_lbl = ctk.CTkLabel(
            header, text=self._s("title"),
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        self._title_lbl.grid(row=0, column=1, sticky="w", padx=(12, 0))

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)  # Engines section stretches.

        # System info section
        sys_frame = ctk.CTkFrame(self)
        sys_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        sys_frame.columnconfigure(0, weight=1)

        self._system_header_lbl = ctk.CTkLabel(
            sys_frame, text=self._s("system"),
            font=ctk.CTkFont(weight="bold", size=14),
        )
        self._system_header_lbl.grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))

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

        self._engines_header_lbl = ctk.CTkLabel(
            eng_frame, text=self._s("engines"),
            font=ctk.CTkFont(weight="bold", size=14),
        )
        self._engines_header_lbl.grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))

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

    def set_language(self, ui_lang: str) -> None:
        """Re-localize all visible widgets to ``ui_lang``.

        Called by the main window when the user toggles Suomi/English — the
        view is built once at startup and kept in the stacked grid, so
        without this the header and section titles remain stuck in whatever
        language was active when the app opened.
        """
        self._ui_lang = ui_lang
        self._strings = _ENGINE_MGR_STRINGS.get(ui_lang, _ENGINE_MGR_STRINGS["fi"])
        self._back_btn.configure(text=f"\u2B9C  {self._s('back')}")
        self._title_lbl.configure(text=self._s("title"))
        self._system_header_lbl.configure(text=self._s("system"))
        self._engines_header_lbl.configure(text=self._s("engines"))
        # Re-render dynamic rows so status text ("Installed", "Uninstall")
        # picks up the new language.
        self._refresh_system_info()
        self._refresh_engine_rows()

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
