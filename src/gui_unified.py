"""Unified AudiobookMaker GUI — single window replacing gui.py and launcher.py.

Combines the simple launcher's one-click workflow with the advanced settings
panel from the original gui.py. All engines are dispatched through the same
queue-based event loop: Chatterbox runs as a subprocess via launcher_bridge,
while Edge-TTS / Piper / VoxCPM2 run in-process on a background thread.

Entry point::

    python -m src.gui_unified
    # or: from src.gui_unified import run; run()

Supports Finnish and English UI languages.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Optional

from src import app_config
from src.auto_updater import check_for_update, download_update, apply_update, APP_VERSION, UpdateInfo
from src.ffmpeg_path import setup_ffmpeg_path
from src.launcher_bridge import ChatterboxRunner, ProgressEvent, resolve_chatterbox_python
from src.pdf_parser import parse_pdf
from src.tts_base import EngineStatus, TTSEngine, Voice, get_engine, list_engines
from src.tts_engine import TTSConfig, chapters_to_speech

# Import engine modules for their register_engine() side effects.
from src import tts_edge  # noqa: F401
from src import tts_piper  # noqa: F401

try:
    from src import tts_voxcpm  # noqa: F401
except Exception:
    pass  # VoxCPM2 is optional developer-install.


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
# UI constants
# ---------------------------------------------------------------------------

WINDOW_TITLE = "AudiobookMaker"
WINDOW_MIN_W = 720
WINDOW_MIN_H = 600

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
_CLR_READY = "#2e7d32"
_CLR_NEEDS_SETUP = "#e65100"
_CLR_UNAVAILABLE = "#c62828"

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
        "test_voice": "Kuuntele näyte",
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
        "test_voice": "Preview voice",
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
    },
}


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------


class UnifiedApp(tk.Tk):
    """Single-window AudiobookMaker GUI."""

    POLL_INTERVAL_MS = 100

    def __init__(self) -> None:
        super().__init__()
        self.title(WINDOW_TITLE)
        self.minsize(WINDOW_MIN_W, WINDOW_MIN_H)
        self._center_window()

        setup_ffmpeg_path()

        # ---- internal state ----
        self._pdf_path: Optional[str] = None
        self._output_path: Optional[str] = None
        self._synth_running = False
        self._cancel_requested = False
        self._cancel_flag = threading.Event()
        self._testing_voice = False
        self._chatterbox_runner: Optional[ChatterboxRunner] = None
        self._event_queue: "queue.Queue[ProgressEvent]" = queue.Queue()
        self._log_visible = False
        self._pending_update: Optional[UpdateInfo] = None

        # Load persisted preferences.
        self._user_cfg = app_config.load()
        self._ui_lang: str = self._user_cfg.ui_language or "fi"

        # Maps populated during engine list init.
        self._engine_display_to_id: dict[str, str] = {}

        # Build all widgets.
        self._build_ui()

        # Restore settings from config.
        self._apply_loaded_config()

        # Apply UI language (updates all widget texts).
        self._apply_ui_language()

        # Check for updates in background (non-blocking).
        self._update_queue: "queue.Queue[UpdateInfo]" = queue.Queue()
        threading.Thread(
            target=self._check_update_worker, daemon=True, name="update-check",
        ).start()
        self.after(500, self._poll_update_check)

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

    # ------------------------------------------------------------------
    # UI language helpers
    # ------------------------------------------------------------------

    def _s(self, key: str) -> str:
        """Return the UI string for *key* in the current UI language."""
        return _STRINGS.get(self._ui_lang, _STRINGS["fi"]).get(key, key)

    def _on_ui_language_changed(self, _event: object = None) -> None:
        """Handle the UI language combobox change."""
        sel = self._ui_lang_var.get()
        self._ui_lang = "en" if sel == "English" else "fi"
        self._apply_ui_language()
        # Persist immediately.
        self._save_current_config()

    def _apply_ui_language(self) -> None:
        """Update ALL widget texts to match ``self._ui_lang``."""
        s = lambda key: self._s(key)  # noqa: E731 — local shorthand

        # Window title.
        self.title(s("window_title"))

        # Input notebook tabs.
        self._input_nb.tab(0, text=s("tab_pdf"))
        self._input_nb.tab(1, text=s("tab_text"))

        # PDF browse button.
        self._pdf_browse_btn.config(text=s("browse"))

        # Text placeholder (only update if placeholder is currently shown).
        self._text_placeholder = s("text_placeholder")
        if self._text_has_placeholder:
            self._text_widget.delete("1.0", tk.END)
            self._text_widget.insert("1.0", self._text_placeholder)

        # PDF var — if no file selected, update the placeholder text.
        if not self._pdf_path:
            self._pdf_var.set(s("no_file_selected"))

        # Settings frame.
        self._settings_frame.config(text=s("settings_frame"))

        # UI language label.
        self._ui_lang_label.config(text=s("ui_language"))

        # Engine label + install button.
        self._engine_label.config(text=s("engine_label"))
        self._install_engines_btn.config(text=s("install_engines"))

        # TTS language + speed labels.
        self._tts_lang_label.config(text=s("language_label"))
        self._speed_label.config(text=s("speed_label"))

        # Speed combobox: translate values while preserving the selected rate.
        old_speed_opts = SPEED_OPTIONS["fi" if self._ui_lang == "en" else "en"]
        new_speed_opts = SPEED_OPTIONS[self._ui_lang]
        current_rate = old_speed_opts.get(self._speed_var.get())
        if current_rate is None:
            # Might already be in current language — look up directly.
            current_rate = new_speed_opts.get(self._speed_var.get(), "+0%")
        self._speed_cb["values"] = list(new_speed_opts.keys())
        # Find the label for the current rate in the new language.
        new_label = next(
            (lbl for lbl, val in new_speed_opts.items() if val == current_rate),
            list(new_speed_opts.keys())[1],  # fallback to Normal
        )
        self._speed_var.set(new_label)

        # Voice label + test button.
        self._voice_label.config(text=s("voice_label"))
        self._test_btn.config(text=s("test_voice"))

        # Reference audio widgets.
        self._ref_label.config(text=s("ref_audio_label"))
        self._ref_browse_btn.config(text=s("browse").rstrip("\u2026"))
        self._ref_clear_btn.config(text=s("clear"))

        # Voice description label.
        self._desc_label.config(text=s("voice_desc_label"))

        # Output mode: translate values while preserving the selected mode.
        old_out_opts = OUTPUT_MODES["fi" if self._ui_lang == "en" else "en"]
        new_out_opts = OUTPUT_MODES[self._ui_lang]
        current_mode = old_out_opts.get(self._output_mode_var.get())
        if current_mode is None:
            current_mode = new_out_opts.get(self._output_mode_var.get(), "single")
        self._output_mode_cb["values"] = list(new_out_opts.keys())
        new_mode_label = next(
            (lbl for lbl, val in new_out_opts.items() if val == current_mode),
            list(new_out_opts.keys())[0],
        )
        self._output_mode_var.set(new_mode_label)
        self._output_mode_label.config(text=s("output_mode_label"))

        # Save label + browse button.
        self._save_label.config(text=s("save_label"))
        self._out_browse_btn.config(text=s("browse"))

        # Output var — if no output selected, update placeholder.
        if not self._output_path:
            self._out_var.set(s("no_output_selected"))

        # Status line (only if not mid-conversion).
        if not self._synth_running:
            self._status_var.set(s("select_input_prompt"))

        # Convert / cancel / open folder buttons.
        self._convert_btn.config(text=s("convert"))
        if not self._cancel_requested:
            self._cancel_btn.config(text=s("cancel"))
        self._open_folder_btn.config(text=s("open_folder"))

        # Log toggle button.
        if self._log_visible:
            self._log_toggle_btn.config(text=s("hide_log"))
        else:
            self._log_toggle_btn.config(text=s("show_log"))

        # Update banner.
        if self._pending_update and self._pending_update.available:
            self._update_label.config(
                text=s("update_available").format(
                    version=self._pending_update.latest_version
                )
            )
            self._update_btn.config(text=s("update_now"))

        # UI lang combobox — keep it in sync.
        self._ui_lang_var.set("Suomi" if self._ui_lang == "fi" else "English")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        main.columnconfigure(0, weight=1)
        # Let log panel row stretch.
        main.rowconfigure(6, weight=1)

        self._build_update_banner(main, row=0)
        self._build_input_tabs(main, row=1)
        self._build_settings_frame(main, row=2)
        self._build_output_frame(main, row=3)
        self._build_progress_frame(main, row=4)
        self._build_log_panel(main, row=5, stretch_row=6)

    # ---- 0. Update banner ---------------------------------------------

    def _build_update_banner(self, parent: ttk.Frame, row: int) -> None:
        self._update_banner = ttk.Frame(parent)
        self._update_banner.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        self._update_banner.columnconfigure(0, weight=1)

        self._update_label = ttk.Label(self._update_banner, text="")
        self._update_label.grid(row=0, column=0, sticky="w", padx=(0, 8))

        self._update_btn = ttk.Button(
            self._update_banner, text=self._s("update_now"),
            command=self._on_update_click,
        )
        self._update_btn.grid(row=0, column=1)

        # Hidden by default.
        self._update_banner.grid_remove()

    # ---- 1. Input tabs ------------------------------------------------

    def _build_input_tabs(self, parent: ttk.Frame, row: int) -> None:
        self._input_nb = ttk.Notebook(parent)
        self._input_nb.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        self._input_nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # PDF tab
        pdf_frame = ttk.Frame(self._input_nb, padding=6)
        pdf_frame.columnconfigure(0, weight=1)
        self._pdf_var = tk.StringVar(value="Ei tiedostoa valittu")
        ttk.Entry(pdf_frame, textvariable=self._pdf_var, state="readonly").grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        self._pdf_browse_btn = ttk.Button(pdf_frame, text="Selaa\u2026", command=self._browse_pdf)
        self._pdf_browse_btn.grid(row=0, column=1)
        self._input_nb.add(pdf_frame, text="PDF-tiedosto")

        # Text tab
        text_frame = ttk.Frame(self._input_nb, padding=6)
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        self._text_widget = tk.Text(
            text_frame, height=6, wrap=tk.WORD, font=("", 10)
        )
        text_scroll = ttk.Scrollbar(
            text_frame, orient=tk.VERTICAL, command=self._text_widget.yview
        )
        self._text_widget.configure(yscrollcommand=text_scroll.set)
        self._text_widget.grid(row=0, column=0, sticky="nsew")
        text_scroll.grid(row=0, column=1, sticky="ns")

        # Placeholder handling.
        self._text_placeholder = "Kirjoita tai liitä teksti tähän..."
        self._text_has_placeholder = True
        self._text_widget.insert("1.0", self._text_placeholder)
        self._text_widget.configure(foreground="#999")
        self._text_widget.bind("<FocusIn>", self._on_text_focus_in)
        self._text_widget.bind("<FocusOut>", self._on_text_focus_out)

        self._input_nb.add(text_frame, text="Teksti")

    def _on_text_focus_in(self, _event: object = None) -> None:
        if self._text_has_placeholder:
            self._text_widget.delete("1.0", tk.END)
            self._text_widget.configure(foreground="")
            self._text_has_placeholder = False

    def _on_text_focus_out(self, _event: object = None) -> None:
        content = self._text_widget.get("1.0", tk.END).strip()
        if not content:
            self._text_widget.insert("1.0", self._text_placeholder)
            self._text_widget.configure(foreground="#999")
            self._text_has_placeholder = True

    # ---- 2. Settings frame --------------------------------------------

    def _build_settings_frame(self, parent: ttk.Frame, row: int) -> None:
        self._settings_frame = ttk.LabelFrame(parent, text="Asetukset", padding=8)
        self._settings_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        self._settings_frame.columnconfigure(1, weight=1)
        self._settings_frame.columnconfigure(3, weight=1)
        settings = self._settings_frame

        srow = 0

        # Row 0: UI language selector
        self._ui_lang_label = ttk.Label(settings, text="Käyttöliittymä:")
        self._ui_lang_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6)
        )
        self._ui_lang_var = tk.StringVar(
            value="Suomi" if self._ui_lang == "fi" else "English"
        )
        self._ui_lang_cb = ttk.Combobox(
            settings, textvariable=self._ui_lang_var,
            values=["Suomi", "English"], state="readonly", width=14,
        )
        self._ui_lang_cb.grid(row=srow, column=1, sticky="w")
        self._ui_lang_cb.bind("<<ComboboxSelected>>", self._on_ui_language_changed)
        srow += 1

        # Row 1: Engine + install button
        self._engine_label = ttk.Label(settings, text="Moottori:")
        self._engine_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6)
        )
        engine_frame = ttk.Frame(settings)
        engine_frame.grid(row=srow, column=1, columnspan=3, sticky="ew")
        engine_frame.columnconfigure(0, weight=1)
        self._engine_var = tk.StringVar()
        self._engine_cb = ttk.Combobox(
            engine_frame, textvariable=self._engine_var, state="readonly"
        )
        self._engine_cb.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._engine_cb.bind("<<ComboboxSelected>>", self._on_engine_changed)
        self._install_engines_btn = ttk.Button(
            engine_frame, text="Asenna moottoreita\u2026",
            command=self._open_engine_manager,
        )
        self._install_engines_btn.grid(row=0, column=1)
        self._populate_engine_list()
        srow += 1

        # Row 2: Engine status
        self._engine_status_var = tk.StringVar(value="")
        self._engine_status_lbl = ttk.Label(
            settings, textvariable=self._engine_status_var,
            foreground=_CLR_READY, wraplength=560,
        )
        self._engine_status_lbl.grid(
            row=srow, column=0, columnspan=4, sticky="w", pady=(2, 4)
        )
        srow += 1

        # Row 3: Language + Speed
        self._tts_lang_label = ttk.Label(settings, text="Kieli:")
        self._tts_lang_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6)
        )
        self._lang_var = tk.StringVar(value="Suomi")
        lang_cb = ttk.Combobox(
            settings, textvariable=self._lang_var,
            values=list(LANGUAGES.keys()), state="readonly", width=14,
        )
        lang_cb.grid(row=srow, column=1, sticky="w")
        lang_cb.bind("<<ComboboxSelected>>", self._on_language_changed)

        self._speed_label = ttk.Label(settings, text="Nopeus:")
        self._speed_label.grid(
            row=srow, column=2, sticky="w", padx=(16, 6)
        )
        self._speed_var = tk.StringVar(value="Normaali")
        self._speed_cb = ttk.Combobox(
            settings, textvariable=self._speed_var,
            values=list(SPEED_OPTIONS["fi"].keys()), state="readonly", width=20,
        )
        self._speed_cb.grid(row=srow, column=3, sticky="w")
        srow += 1

        # Row 4: Voice + preview
        self._voice_label = ttk.Label(settings, text="Ääni:")
        self._voice_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6), pady=(6, 0)
        )
        voice_frame = ttk.Frame(settings)
        voice_frame.grid(
            row=srow, column=1, columnspan=3, sticky="ew", pady=(6, 0)
        )
        voice_frame.columnconfigure(0, weight=1)
        self._voice_var = tk.StringVar()
        self._voice_cb = ttk.Combobox(
            voice_frame, textvariable=self._voice_var, state="readonly"
        )
        self._voice_cb.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._test_btn = ttk.Button(
            voice_frame, text="Kuuntele näyte", command=self._on_test_voice
        )
        self._test_btn.grid(row=0, column=1)
        srow += 1

        # Row 5: Reference audio (cloning) — hidden when unsupported
        self._ref_label = ttk.Label(settings, text="Ref. ääni:")
        self._ref_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6), pady=(6, 0)
        )
        self._ref_frame = ttk.Frame(settings)
        self._ref_frame.grid(
            row=srow, column=1, columnspan=3, sticky="ew", pady=(6, 0)
        )
        self._ref_frame.columnconfigure(0, weight=1)
        self._ref_audio_var = tk.StringVar(value="")
        ttk.Entry(
            self._ref_frame, textvariable=self._ref_audio_var, state="readonly"
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._ref_browse_btn = ttk.Button(
            self._ref_frame, text="Selaa", command=self._browse_reference_audio
        )
        self._ref_browse_btn.grid(row=0, column=1)
        self._ref_clear_btn = ttk.Button(
            self._ref_frame, text="Tyhjennä", command=self._clear_reference_audio
        )
        self._ref_clear_btn.grid(row=0, column=2, padx=(4, 0))
        srow += 1

        # Row 6: Voice description — hidden when unsupported
        self._desc_label = ttk.Label(settings, text="Äänityyli:")
        self._desc_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6), pady=(6, 0)
        )
        self._voice_desc_var = tk.StringVar(value="")
        self._voice_desc_entry = ttk.Entry(
            settings, textvariable=self._voice_desc_var
        )
        self._voice_desc_entry.grid(
            row=srow, column=1, columnspan=3, sticky="ew", pady=(6, 0)
        )
        srow += 1

        # Row 7: Output mode
        self._output_mode_label = ttk.Label(settings, text="Tuloste:")
        self._output_mode_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6), pady=(6, 0)
        )
        self._output_mode_var = tk.StringVar(value="Yksi MP3")
        self._output_mode_cb = ttk.Combobox(
            settings, textvariable=self._output_mode_var,
            values=list(OUTPUT_MODES["fi"].keys()), state="readonly",
        )
        self._output_mode_cb.grid(row=srow, column=1, columnspan=3, sticky="ew", pady=(6, 0))

        # Initially hide capability-specific widgets.
        self._ref_label.grid_remove()
        self._ref_frame.grid_remove()
        self._desc_label.grid_remove()
        self._voice_desc_entry.grid_remove()

    # ---- 3. Output frame -----------------------------------------------

    def _build_output_frame(self, parent: ttk.Frame, row: int) -> None:
        out_frame = ttk.Frame(parent)
        out_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        out_frame.columnconfigure(1, weight=1)

        self._save_label = ttk.Label(out_frame, text="Tallenna:")
        self._save_label.grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        self._out_var = tk.StringVar(value="")
        ttk.Entry(out_frame, textvariable=self._out_var, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=(0, 6)
        )
        self._out_browse_btn = ttk.Button(
            out_frame, text="Vaihda\u2026", command=self._browse_output,
        )
        self._out_browse_btn.grid(row=0, column=2)
        # Set initial auto-generated path.
        self._auto_output_path()

    # ---- 4. Progress frame --------------------------------------------

    def _build_progress_frame(self, parent: ttk.Frame, row: int) -> None:
        pf = ttk.Frame(parent)
        pf.grid(row=row, column=0, sticky="ew", pady=(0, 4))
        pf.columnconfigure(0, weight=1)

        self._status_var = tk.StringVar(value="Valitse syöte ja paina Muunna.")
        ttk.Label(pf, textvariable=self._status_var, wraplength=680).grid(
            row=0, column=0, sticky="ew"
        )

        self._eta_var = tk.StringVar(value="")
        ttk.Label(pf, textvariable=self._eta_var, foreground="#666").grid(
            row=1, column=0, sticky="ew", pady=(2, 4)
        )

        self._progress_var = tk.DoubleVar(value=0.0)
        ttk.Progressbar(
            pf, variable=self._progress_var, maximum=1000, mode="determinate"
        ).grid(row=2, column=0, sticky="ew", pady=(0, 8))

        btn_row = ttk.Frame(pf)
        btn_row.grid(row=3, column=0)

        self._convert_btn = ttk.Button(
            btn_row, text="Muunna", command=self._on_convert_click
        )
        self._convert_btn.grid(row=0, column=0, padx=(0, 6))

        self._cancel_btn = ttk.Button(
            btn_row, text="Peruuta", command=self._request_cancel
        )
        self._cancel_btn.grid(row=0, column=1, padx=(0, 6))
        self._cancel_btn.grid_remove()

        self._open_folder_btn = ttk.Button(
            btn_row, text="Avaa kansio", command=self._open_output_folder,
            state=tk.DISABLED,
        )
        self._open_folder_btn.grid(row=0, column=2)

    # ---- 5. Log panel (collapsible) -----------------------------------

    def _build_log_panel(
        self, parent: ttk.Frame, row: int, stretch_row: int
    ) -> None:
        toggle_frame = ttk.Frame(parent)
        toggle_frame.grid(row=row, column=0, sticky="ew", pady=(4, 0))

        self._log_toggle_btn = ttk.Button(
            toggle_frame, text="Näytä loki", command=self._toggle_log
        )
        self._log_toggle_btn.grid(row=0, column=0, sticky="w")

        self._log_frame = ttk.Frame(parent)
        # Placed in stretch_row so it can grow.
        self._log_frame.grid(
            row=stretch_row, column=0, sticky="nsew", pady=(4, 0)
        )
        self._log_frame.columnconfigure(0, weight=1)
        self._log_frame.rowconfigure(0, weight=1)

        self._log_text = scrolledtext.ScrolledText(
            self._log_frame, height=10, wrap=tk.WORD,
            font=("Consolas", 9),
        )
        self._log_text.grid(row=0, column=0, sticky="nsew")
        self._log_text.configure(state=tk.DISABLED)

        # Hidden by default.
        self._log_frame.grid_remove()

    # ------------------------------------------------------------------
    # Engine list population
    # ------------------------------------------------------------------

    def _populate_engine_list(self) -> None:
        """Fill the engine combobox from the registry + Chatterbox check."""
        self._engine_display_to_id.clear()

        for engine in list_engines():
            self._engine_display_to_id[engine.display_name] = engine.id

        # Chatterbox-Finnish via subprocess bridge.
        chatterbox_py = resolve_chatterbox_python()
        runner_script = _REPO_ROOT / "scripts" / "generate_chatterbox_audiobook.py"
        if chatterbox_py is not None and runner_script.exists():
            label = "Chatterbox Finnish (paras laatu, NVIDIA)"
            self._engine_display_to_id[label] = "chatterbox_fi"

        labels = list(self._engine_display_to_id.keys())
        self._engine_cb["values"] = labels
        if labels and not self._engine_var.get():
            self._engine_var.set(labels[0])

    def _current_engine_id(self) -> str:
        display = self._engine_var.get()
        return self._engine_display_to_id.get(display, "")

    def _current_engine(self) -> Optional[TTSEngine]:
        eid = self._current_engine_id()
        if not eid or eid == "chatterbox_fi":
            return None
        return get_engine(eid)

    def _current_language(self) -> str:
        return LANGUAGES.get(self._lang_var.get(), "fi")

    def _current_voice(self) -> Optional[Voice]:
        engine = self._current_engine()
        if not engine:
            return None
        display = self._voice_var.get()
        for voice in engine.list_voices(self._current_language()):
            if voice.display_name == display:
                return voice
        return None

    # ------------------------------------------------------------------
    # Input mode helpers
    # ------------------------------------------------------------------

    @property
    def _input_mode(self) -> str:
        """Return 'pdf' or 'text' based on the active notebook tab."""
        idx = self._input_nb.index(self._input_nb.select())
        return "pdf" if idx == 0 else "text"

    def _on_tab_changed(self, _event: object = None) -> None:
        """Refresh the auto-generated output path when switching tabs."""
        self._auto_output_path()

    def _get_input_text(self) -> str:
        """Return the text to synthesize based on the active input tab."""
        if self._input_mode == "pdf":
            if not self._pdf_path:
                raise ValueError(self._s("no_pdf"))
            book = parse_pdf(self._pdf_path)
            return book.full_text
        else:
            if self._text_has_placeholder:
                return ""
            return self._text_widget.get("1.0", tk.END).strip()

    # ------------------------------------------------------------------
    # Engine change
    # ------------------------------------------------------------------

    def _on_engine_changed(self, _event: object = None) -> None:
        self._refresh_voice_list()

    def _on_language_changed(self, _event: object = None) -> None:
        self._refresh_voice_list()

    def _refresh_voice_list(self) -> None:
        """Refresh voices, status label, and capability widgets."""
        eid = self._current_engine_id()

        # Chatterbox is subprocess-only — no voice list from registry.
        if eid == "chatterbox_fi":
            self._voice_cb["values"] = ["Oletus"]
            self._voice_var.set("Oletus")
            self._engine_status_lbl.configure(foreground=_CLR_READY)
            self._engine_status_var.set(
                "Offline, paras laatu. Kesto ~1\u20132 h NVIDIA-koneella."
            )
            self._update_capability_widgets(
                supports_cloning=True, supports_description=False
            )
            return

        engine = self._current_engine()
        if engine is None:
            self._voice_cb["values"] = []
            self._voice_var.set("")
            self._engine_status_var.set("")
            self._update_capability_widgets(False, False)
            return

        status = engine.check_status()
        if not status.available:
            self._engine_status_lbl.configure(foreground=_CLR_UNAVAILABLE)
            self._engine_status_var.set(status.reason)
            self._voice_cb["values"] = []
            self._voice_var.set("")
        elif status.needs_download:
            self._engine_status_lbl.configure(foreground=_CLR_NEEDS_SETUP)
            self._engine_status_var.set(
                status.reason + "  Lataus käynnistyy automaattisesti."
            )
            self._populate_voice_combobox(engine)
        else:
            self._engine_status_lbl.configure(foreground=_CLR_READY)
            self._engine_status_var.set(engine.description)
            self._populate_voice_combobox(engine)

        self._update_capability_widgets(
            supports_cloning=bool(engine.supports_voice_cloning),
            supports_description=bool(engine.supports_voice_description),
        )

    def _populate_voice_combobox(self, engine: TTSEngine) -> None:
        lang = self._current_language()
        voices = engine.list_voices(lang)
        names = [v.display_name for v in voices]
        self._voice_cb["values"] = names
        if names:
            default_id = engine.default_voice(lang)
            default_name = next(
                (v.display_name for v in voices if v.id == default_id),
                names[0],
            )
            self._voice_var.set(default_name)
        else:
            self._voice_var.set("")

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

    def _auto_output_path(self) -> None:
        """Generate an automatic output path based on current input mode."""
        if self._input_mode == "pdf" and self._pdf_path:
            # Output goes next to the PDF: book.pdf -> book.mp3
            suggested = str(Path(self._pdf_path).with_suffix(".mp3"))
        else:
            # Auto-increment: texttospeech_1.mp3, texttospeech_2.mp3, ...
            out_dir = Path.home() / "Documents" / "AudiobookMaker"
            out_dir.mkdir(parents=True, exist_ok=True)
            n = 1
            while True:
                candidate = out_dir / f"texttospeech_{n}.mp3"
                if not candidate.exists():
                    break
                n += 1
            suggested = str(candidate)
        self._out_var.set(suggested)
        self._output_path = suggested

    def _browse_pdf(self) -> None:
        path = filedialog.askopenfilename(
            title=self._s("select_pdf"),
            filetypes=[("PDF", "*.pdf"), ("*", "*.*")],
        )
        if path:
            self._pdf_path = path
            self._pdf_var.set(path)
            self._status_var.set("PDF valittu." if self._ui_lang == "fi" else "PDF selected.")
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
        """Let the user override the auto-generated output path."""
        current = self._out_var.get()
        initial_dir = str(Path(current).parent) if current else str(Path.home())
        mode = OUTPUT_MODES[self._ui_lang].get(self._output_mode_var.get(), "single")
        if mode == "single":
            path = filedialog.asksaveasfilename(
                title=self._s("save_as"),
                initialdir=initial_dir,
                defaultextension=".mp3",
                filetypes=[("MP3", "*.mp3")],
            )
            if path:
                self._output_path = path
                self._out_var.set(path)
        else:
            path = filedialog.askdirectory(
                title=self._s("save_as"),
                initialdir=initial_dir,
            )
            if path:
                self._output_path = path
                self._out_var.set(path)

    # ------------------------------------------------------------------
    # Engine installer (placeholder)
    # ------------------------------------------------------------------

    def _open_engine_manager(self) -> None:
        """Placeholder for the engine installer dialog."""
        dlg = tk.Toplevel(self)
        dlg.title(self._s("engine_manager_title"))
        dlg.geometry("400x200")
        dlg.transient(self)
        dlg.grab_set()
        ttk.Label(
            dlg, text="Engine manager \u2014 coming soon",
            font=("", 12), anchor="center",
        ).pack(expand=True, fill=tk.BOTH, padx=20, pady=20)
        ttk.Button(dlg, text="Sulje", command=dlg.destroy).pack(pady=(0, 16))

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
        self._test_btn.config(state=tk.DISABLED)
        self._status_var.set("Syntetisoidaan ääninäytettä\u2026")

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
            self.after(0, lambda: self._status_var.set(
                f"Näyteen luonti epäonnistui: {exc}"
            ))
        finally:
            self.after(0, lambda: self._test_btn.config(state=tk.NORMAL))
            self.after(0, lambda: setattr(self, "_testing_voice", False))

    def _safe_play_sample(self, path: str) -> None:
        def _play() -> None:
            self._status_var.set(f"Ääninäyte tallennettu: {path}")
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
    # Config persistence
    # ------------------------------------------------------------------

    def _apply_loaded_config(self) -> None:
        """Restore persisted preferences to widgets."""
        cfg = self._user_cfg

        # Language.
        for label, code in LANGUAGES.items():
            if code == cfg.language:
                self._lang_var.set(label)
                break

        # Engine.
        engine = get_engine(cfg.engine_id)
        if engine and engine.check_status().available:
            self._engine_var.set(engine.display_name)
        elif cfg.engine_id == "chatterbox_fi":
            for lbl, eid in self._engine_display_to_id.items():
                if eid == "chatterbox_fi":
                    self._engine_var.set(lbl)
                    break

        self._refresh_voice_list()

        # Voice.
        eng = self._current_engine()
        if eng and cfg.voice_id:
            for voice in eng.list_voices(self._current_language()):
                if voice.id == cfg.voice_id:
                    self._voice_var.set(voice.display_name)
                    break

        # Speed.
        for label, val in SPEED_OPTIONS[self._ui_lang].items():
            if val == cfg.speed:
                self._speed_var.set(label)
                break

        # Reference audio + voice description.
        if cfg.reference_audio:
            self._ref_audio_var.set(cfg.reference_audio)
        if cfg.voice_description:
            self._voice_desc_var.set(cfg.voice_description)

        # Input mode tab.
        if cfg.input_mode == "text":
            self._input_nb.select(1)

        # Output mode.
        for label, val in OUTPUT_MODES[self._ui_lang].items():
            if val == cfg.output_mode:
                self._output_mode_var.set(label)
                break

        # Log panel visibility.
        if cfg.log_panel_visible:
            self._toggle_log()

    def _save_current_config(self) -> None:
        """Snapshot current UI state into on-disk config."""
        cfg = self._user_cfg
        cfg.engine_id = self._current_engine_id() or "edge"
        voice = self._current_voice()
        cfg.voice_id = voice.id if voice else ""
        cfg.language = self._current_language()
        cfg.speed = SPEED_OPTIONS[self._ui_lang].get(self._speed_var.get(), "+0%")
        cfg.reference_audio = self._ref_audio_var.get()
        cfg.voice_description = self._voice_desc_var.get()
        cfg.input_mode = self._input_mode
        cfg.output_mode = OUTPUT_MODES[self._ui_lang].get(
            self._output_mode_var.get(), "single"
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

        engine_id = self._current_engine_id()
        if not engine_id:
            messagebox.showerror(self._s("error"), "Valitse TTS-moottori.")
            return

        # For registry engines, verify availability.
        if engine_id != "chatterbox_fi":
            engine = self._current_engine()
            voice = self._current_voice()
            if engine is None:
                messagebox.showerror(self._s("error"), "Moottoria ei löytynyt.")
                return
            if voice is None:
                messagebox.showerror(self._s("error"), "Valitse ääni.")
                return
            status = engine.check_status()
            if not status.available:
                messagebox.showerror(
                    self._s("error"), f"{engine.display_name}: {status.reason}"
                )
                return

        # Persist settings before synthesis.
        self._save_current_config()

        # Enter running state.
        self._set_running_state()

        if engine_id == "chatterbox_fi":
            self._start_chatterbox_subprocess()
        else:
            self._start_inprocess_engine(engine_id)

        self.after(self.POLL_INTERVAL_MS, self._pump_events)

    def _set_running_state(self) -> None:
        self._synth_running = True
        self._cancel_requested = False
        self._cancel_flag.clear()
        self._convert_btn.config(state=tk.DISABLED)
        self._cancel_btn.grid()
        self._open_folder_btn.config(state=tk.DISABLED)
        self._progress_var.set(0)
        self._status_var.set(self._s("converting"))
        self._eta_var.set("")
        self._clear_log()

    def _set_idle_state(self) -> None:
        self._synth_running = False
        self._convert_btn.config(state=tk.NORMAL)
        self._cancel_btn.grid_remove()

    # ---- Chatterbox subprocess ----------------------------------------

    def _start_chatterbox_subprocess(self) -> None:
        if self._input_mode != "pdf" or not self._pdf_path:
            # Chatterbox runner only supports PDF input.
            self._fail("Chatterbox tukee vain PDF-syötettä.")
            return

        python_exe = resolve_chatterbox_python()
        runner_script = _REPO_ROOT / "scripts" / "generate_chatterbox_audiobook.py"
        if python_exe is None or not runner_script.exists():
            self._fail(
                "Chatterbox-venviä ei löytynyt. Asenna se ensin "
                "suorittamalla scripts/setup_chatterbox_windows.bat."
            )
            return

        # Use output path's parent directory, or a sensible default.
        if self._out_var.get() and self._out_var.get() not in ("Ei valittu", "Not selected"):
            out_dir = Path(self._out_var.get()).parent
        else:
            out_dir = Path.home() / "Documents" / "AudiobookMaker"
        out_dir = out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        extra_args: list[str] = []
        ref_audio = self._ref_audio_var.get()
        if ref_audio:
            extra_args.extend(["--ref-audio", ref_audio])

        self._chatterbox_runner = ChatterboxRunner(
            python_exe=str(python_exe),
            script_path=str(runner_script),
            pdf_path=str(self._pdf_path),
            out_dir=str(out_dir),
            extra_args=extra_args,
        )

        self._append_log(f"PDF: {self._pdf_path}")
        self._append_log(f"Output: {out_dir}")
        self._append_log("Engine: chatterbox_fi")

        try:
            self._chatterbox_runner.start()
        except Exception as exc:
            self._fail(f"Subprocess ei käynnistynyt: {exc}")
            return

        threading.Thread(
            target=self._relay_chatterbox_events, daemon=True,
            name="chatterbox-relay",
        ).start()

    def _relay_chatterbox_events(self) -> None:
        runner = self._chatterbox_runner
        assert runner is not None
        while not runner.finished:
            ev = runner.poll_event(timeout=0.2)
            if ev is not None:
                self._event_queue.put(ev)

    # ---- In-process engine thread -------------------------------------

    def _start_inprocess_engine(self, engine_id: str) -> None:
        self._append_log(f"Engine: {engine_id}")
        self._append_log(f"Output: {self._output_path}")
        threading.Thread(
            target=self._run_inprocess, args=(engine_id,),
            daemon=True, name=f"tts-{engine_id}",
        ).start()

    def _run_inprocess(self, engine_id: str) -> None:
        """Background thread. Communicates with UI only via event queue."""
        try:
            engine = get_engine(engine_id)
            if engine is None:
                raise RuntimeError(f"Moottoria '{engine_id}' ei löytynyt.")

            self._event_queue.put(
                ProgressEvent(kind="log", raw_line="Luetaan syötettä\u2026")
            )

            if self._input_mode == "pdf":
                assert self._pdf_path is not None
                book = parse_pdf(self._pdf_path)
                text = book.full_text
            else:
                text = self._text_widget.get("1.0", tk.END).strip()

            if not text:
                raise ValueError("Ei tekstiä syntetisoitavaksi.")

            voice = self._current_voice()
            if voice is None:
                # Fallback to engine default.
                voice_id = engine.default_voice(self._current_language())
                if voice_id is None:
                    raise RuntimeError(
                        "Moottorilla ei ole ääntä valitulle kielelle."
                    )
            else:
                voice_id = voice.id

            lang = self._current_language()
            ref_audio = self._ref_audio_var.get() or None
            voice_desc = self._voice_desc_var.get() or None
            mode = OUTPUT_MODES[self._ui_lang].get(self._output_mode_var.get(), "single")
            assert self._output_path is not None

            def progress_cb(current: int, total: int, msg: str) -> None:
                if self._cancel_flag.is_set():
                    raise InterruptedError("Käyttäjä peruutti synteesin.")
                self._event_queue.put(
                    ProgressEvent(
                        kind="chunk",
                        total_done=current,
                        total_chunks=max(total, 1),
                        raw_line=msg,
                    )
                )

            if mode == "chapters" and self._input_mode == "pdf":
                assert self._pdf_path is not None
                book = parse_pdf(self._pdf_path)
                if engine_id == "edge":
                    chapters = [(ch.title, ch.content) for ch in book.chapters]
                    rate = SPEED_OPTIONS[self._ui_lang].get(self._speed_var.get(), "+0%")
                    config = TTSConfig(
                        language=lang, voice=voice_id, rate=rate
                    )
                    chapters_to_speech(
                        chapters, self._output_path, config, progress_cb
                    )
                else:
                    raise RuntimeError(
                        "Lukukohtainen tulostus on tällä hetkellä tuettu "
                        "vain Edge-TTS-moottorilla."
                    )
            else:
                engine.synthesize(
                    text, self._output_path, voice_id, lang,
                    progress_cb,
                    reference_audio=ref_audio,
                    voice_description=voice_desc,
                )

            self._event_queue.put(
                ProgressEvent(
                    kind="full_done", output_path=str(self._output_path)
                )
            )
            self._event_queue.put(ProgressEvent(kind="exit", returncode=0))

        except InterruptedError:
            self._event_queue.put(
                ProgressEvent(kind="signal", raw_line="Peruutettu.")
            )
            self._event_queue.put(ProgressEvent(kind="exit", returncode=0))
        except Exception as exc:
            self._event_queue.put(
                ProgressEvent(kind="error", raw_line=str(exc))
            )
            self._event_queue.put(ProgressEvent(kind="exit", returncode=1))

    # ------------------------------------------------------------------
    # Event pump (Tk main thread)
    # ------------------------------------------------------------------

    def _pump_events(self) -> None:
        had_exit = False
        while True:
            try:
                ev = self._event_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_event(ev)
            if ev.kind == "exit":
                had_exit = True

        if not had_exit and self._synth_running:
            self.after(self.POLL_INTERVAL_MS, self._pump_events)

    def _handle_event(self, ev: ProgressEvent) -> None:
        if ev.raw_line:
            self._append_log(ev.raw_line)

        if ev.kind == "setup_total":
            self._eta_var.set(
                f"Yhteensä {ev.total_chunks} palaa synteesissä."
            )
        elif ev.kind == "setup_cached":
            self._progress_var.set(
                (ev.total_done / max(ev.total_chunks, 1)) * 1000
            )
            self._eta_var.set(
                f"Jatketaan välimuistista: "
                f"{ev.total_done}/{ev.total_chunks} palaa valmiina."
            )
        elif ev.kind == "chunk":
            if ev.total_chunks > 0:
                self._progress_var.set(
                    (ev.total_done / ev.total_chunks) * 1000
                )
            if ev.chapter_total > 0:
                self._status_var.set(
                    f"Luku {ev.chapter_idx}/{ev.chapter_total}, "
                    f"pala {ev.chunk_idx}/{ev.chunk_total}"
                )
                if ev.elapsed_s or ev.eta_s:
                    self._eta_var.set(
                        f"Kulunut {int(ev.elapsed_s // 60)} min \u2014 "
                        f"jäljellä noin {int(ev.eta_s // 60)} min"
                    )
            else:
                self._status_var.set(ev.raw_line or "Synteesi käynnissä\u2026")
        elif ev.kind in ("full_done", "chapter_done"):
            if ev.output_path:
                self._output_path = ev.output_path
        elif ev.kind == "done":
            self._progress_var.set(1000)
        elif ev.kind == "signal":
            self._cancel_requested = True
        elif ev.kind == "exit":
            self._on_synth_exit(ev.returncode)

    def _on_synth_exit(self, returncode: int) -> None:
        self._set_idle_state()

        if returncode == 0 and not self._cancel_requested:
            self._progress_var.set(1000)
            out_name = (
                Path(self._output_path).name if self._output_path else ""
            )
            self._status_var.set(f"{self._s('done')} {out_name}")
            self._open_folder_btn.config(state=tk.NORMAL)
            messagebox.showinfo(self._s("done"), f"{out_name}")
        elif self._cancel_requested:
            self._status_var.set(self._s("cancelling"))
            self._cancel_requested = False
        else:
            tail = ""
            if self._chatterbox_runner is not None:
                tail = "\n".join(self._chatterbox_runner.tail_lines(15))
            self._status_var.set(f"{self._s('error')} \u2014 log")
            messagebox.showerror(
                self._s("error"),
                tail or self._s("error"),
            )

        self._chatterbox_runner = None

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def _request_cancel(self) -> None:
        self._cancel_requested = True
        self._cancel_flag.set()
        self._cancel_btn.config(text=self._s("cancelling"), state=tk.DISABLED)
        if self._chatterbox_runner is not None:
            self._chatterbox_runner.cancel()

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
            self._log_toggle_btn.config(text=self._s("show_log"))
        else:
            self._log_frame.grid()
            self._log_toggle_btn.config(text=self._s("hide_log"))
        self._log_visible = not self._log_visible

    def _clear_log(self) -> None:
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.delete("1.0", tk.END)
        self._log_text.configure(state=tk.DISABLED)

    def _append_log(self, line: str) -> None:
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.insert(tk.END, line + "\n")
        self._log_text.see(tk.END)
        self._log_text.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Auto-update
    # ------------------------------------------------------------------

    def _check_update_worker(self) -> None:
        """Background thread: check GitHub for a newer version."""
        try:
            info = check_for_update(APP_VERSION)
            self._update_queue.put(info)
        except Exception:
            pass  # Silently ignore — no banner shown.

    def _poll_update_check(self) -> None:
        """Tk main-thread poller: pick up the update-check result."""
        try:
            info = self._update_queue.get_nowait()
        except queue.Empty:
            self.after(500, self._poll_update_check)
            return

        if info.available:
            self._pending_update = info
            self._update_label.config(
                text=self._s("update_available").format(
                    version=info.latest_version
                )
            )
            self._update_btn.config(text=self._s("update_now"))
            self._update_banner.grid()

    def _on_update_click(self) -> None:
        """User clicked the update button — download and install."""
        if self._pending_update is None:
            return

        self._update_btn.config(
            state=tk.DISABLED,
            text=self._s("update_downloading"),
        )

        threading.Thread(
            target=self._download_update_worker, daemon=True,
            name="update-download",
        ).start()
        self.after(self.POLL_INTERVAL_MS, self._pump_update_download)

    def _download_update_worker(self) -> None:
        """Background thread: download the installer."""
        assert self._pending_update is not None
        try:
            def progress_cb(done: int, total: int) -> None:
                if total > 0:
                    self._event_queue.put(
                        ProgressEvent(
                            kind="chunk",
                            total_done=done,
                            total_chunks=total,
                            raw_line=self._s("update_downloading"),
                        )
                    )

            installer_path = download_update(self._pending_update, progress_cb)
            self._event_queue.put(
                ProgressEvent(
                    kind="update_done",
                    raw_line=str(installer_path),
                )
            )
        except Exception as exc:
            self._event_queue.put(
                ProgressEvent(kind="update_failed", raw_line=str(exc))
            )

    def _pump_update_download(self) -> None:
        """Tk main-thread pump for update download progress."""
        while True:
            try:
                ev = self._event_queue.get_nowait()
            except queue.Empty:
                break

            if ev.kind == "chunk":
                if ev.total_chunks > 0:
                    self._progress_var.set(
                        (ev.total_done / ev.total_chunks) * 1000
                    )
            elif ev.kind == "update_done":
                self._progress_var.set(1000)
                self._update_btn.config(text=self._s("update_installing"))
                installer_path = Path(ev.raw_line)
                self.after(200, lambda: apply_update(installer_path))
                return
            elif ev.kind == "update_failed":
                self._update_btn.config(
                    state=tk.NORMAL,
                    text=self._s("update_failed"),
                )
                self._progress_var.set(0)
                return

        self.after(self.POLL_INTERVAL_MS, self._pump_update_download)

    # ------------------------------------------------------------------
    # Error helper
    # ------------------------------------------------------------------

    def _fail(self, message: str) -> None:
        self._set_idle_state()
        self._status_var.set(message)
        messagebox.showerror(self._s("error"), message)


# ---------------------------------------------------------------------------
# Self-test (for CI / frozen-exe verification)
# ---------------------------------------------------------------------------


def self_test() -> int:
    """Headless sanity check: construct + destroy the window."""
    try:
        engines = list_engines()
        print(
            f"[self-test] engines registered: {[e.id for e in engines]}",
            flush=True,
        )
        app = UnifiedApp()
        app.update_idletasks()
        print(
            f"[self-test] window title={app.title()!r} "
            f"geometry={app.geometry()!r}",
            flush=True,
        )
        values = list(app._engine_cb["values"])
        print(f"[self-test] engine dropdown: {values}", flush=True)
        app.destroy()
        print("[self-test] OK", flush=True)
        return 0
    except Exception as exc:
        print(f"[self-test] FAILED: {exc!r}", flush=True, file=sys.stderr)
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
