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

WINDOW_TITLE = "AudiobookMaker"
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
        "update_error_detail": "Päivitys epäonnistui: {error}",
        "listen": "Kuuntele",
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
        "update_error_detail": "Update failed: {error}",
        "listen": "Listen",
        "listening": "Playing...",
        "listen_no_text": "Enter text in the Text tab first.",
        "pdf_no_text": "PDF contains no extractable text (it may be scanned). Try OCR first.",
    },
}


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

        setup_ffmpeg_path()

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
        self._event_queue: "queue.Queue[ProgressEvent]" = queue.Queue()
        self._log_visible = False
        self._pending_update: Optional[UpdateInfo] = None

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

        # Window title.
        self.title(s("window_title"))

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

        # Settings frame header label.
        self._settings_header.configure(text=s("settings_frame"))

        # UI language label.
        self._ui_lang_label.configure(text=s("ui_language"))

        # Engine label + install button.
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
        main = ctk.CTkFrame(self)
        main.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        main.columnconfigure(0, weight=1)
        # Let log panel row stretch.
        main.rowconfigure(6, weight=1)

        # Track current input mode for tab renaming during language changes.
        self._input_mode_raw = "pdf"
        self._tab_name_map: dict[str, str] = {}

        self._build_update_banner(main, row=0)
        self._build_input_tabs(main, row=1)
        self._build_settings_frame(main, row=2)
        self._build_output_frame(main, row=3)
        self._build_progress_frame(main, row=4)
        self._build_log_panel(main, row=5, stretch_row=6)

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
        # CTkFrame with a header label to replace LabelFrame.
        settings_outer = ctk.CTkFrame(parent)
        settings_outer.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        settings_outer.columnconfigure(0, weight=1)

        self._settings_header = ctk.CTkLabel(
            settings_outer, text="Asetukset",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self._settings_header.grid(row=0, column=0, sticky="w", padx=8, pady=(4, 2))

        self._settings_frame = ctk.CTkFrame(settings_outer, fg_color="transparent")
        self._settings_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        self._settings_frame.columnconfigure(1, weight=1)
        self._settings_frame.columnconfigure(3, weight=1)
        settings = self._settings_frame

        srow = 0

        # Row 0: UI language selector
        self._ui_lang_label = ctk.CTkLabel(settings, text="Käyttöliittymä:")
        self._ui_lang_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6)
        )
        self._ui_lang_cb = ctk.CTkComboBox(
            settings,
            values=["Suomi", "English"], state="readonly", width=140,
            command=self._on_ui_language_changed,
        )
        self._ui_lang_cb.set("Suomi" if self._ui_lang == "fi" else "English")
        self._ui_lang_cb.grid(row=srow, column=1, sticky="w")
        srow += 1

        # Row 1: Engine + install button
        self._engine_label = ctk.CTkLabel(settings, text="Moottori:")
        self._engine_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6)
        )
        engine_frame = ctk.CTkFrame(settings, fg_color="transparent")
        engine_frame.grid(row=srow, column=1, columnspan=3, sticky="ew")
        engine_frame.columnconfigure(0, weight=1)
        self._engine_cb = ctk.CTkComboBox(
            engine_frame, state="readonly",
            command=self._on_engine_changed,
        )
        self._engine_cb.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._install_engines_btn = ctk.CTkButton(
            engine_frame, text="Asenna moottoreita\u2026",
            command=self._open_engine_manager, width=160,
        )
        self._install_engines_btn.grid(row=0, column=1)
        self._populate_engine_list()
        srow += 1

        # Row 2: Engine status
        self._engine_status_lbl = ctk.CTkLabel(
            settings, text="",
            text_color=_CLR_READY, wraplength=560,
        )
        self._engine_status_lbl.grid(
            row=srow, column=0, columnspan=4, sticky="w", pady=(2, 4)
        )
        srow += 1

        # Row 3: Language + Speed
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

        # Row 4: Voice + preview
        self._voice_label = ctk.CTkLabel(settings, text="Ääni:")
        self._voice_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6), pady=(6, 0)
        )
        voice_frame = ctk.CTkFrame(settings, fg_color="transparent")
        voice_frame.grid(
            row=srow, column=1, columnspan=3, sticky="ew", pady=(6, 0)
        )
        voice_frame.columnconfigure(0, weight=1)
        self._voice_cb = ctk.CTkComboBox(
            voice_frame, state="readonly",
        )
        self._voice_cb.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._test_btn = ctk.CTkButton(
            voice_frame, text="Kuuntele näyte", command=self._on_test_voice,
            width=120,
        )
        self._test_btn.grid(row=0, column=1)
        srow += 1

        # Row 5: Reference audio (cloning) — hidden when unsupported
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

        # Row 6: Voice description — hidden when unsupported
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

        # Row 7: Output mode
        self._output_mode_label = ctk.CTkLabel(settings, text="Tuloste:")
        self._output_mode_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6), pady=(6, 0)
        )
        self._output_mode_cb = ctk.CTkComboBox(
            settings,
            values=list(OUTPUT_MODES["fi"].keys()), state="readonly",
        )
        self._output_mode_cb.set("Yksi MP3")
        self._output_mode_cb.grid(row=srow, column=1, columnspan=3, sticky="ew", pady=(6, 0))

        # Initially hide capability-specific widgets.
        self._ref_label.grid_remove()
        self._ref_frame.grid_remove()
        self._desc_label.grid_remove()
        self._voice_desc_entry.grid_remove()

    # ---- 3. Output frame -----------------------------------------------

    def _build_output_frame(self, parent: ctk.CTkFrame, row: int) -> None:
        out_frame = ctk.CTkFrame(parent, fg_color="transparent")
        out_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        out_frame.columnconfigure(1, weight=1)

        self._save_label = ctk.CTkLabel(out_frame, text="Tallenna:")
        self._save_label.grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        self._out_entry = ctk.CTkEntry(out_frame, state="disabled")
        self._out_entry.grid(
            row=0, column=1, sticky="ew", padx=(0, 6)
        )
        self._out_browse_btn = ctk.CTkButton(
            out_frame, text="Vaihda\u2026", command=self._browse_output,
            width=80,
        )
        self._out_browse_btn.grid(row=0, column=2)
        # Set initial auto-generated path.
        self._auto_output_path()

    # ---- 4. Progress frame --------------------------------------------

    def _build_progress_frame(self, parent: ctk.CTkFrame, row: int) -> None:
        pf = ctk.CTkFrame(parent, fg_color="transparent")
        pf.grid(row=row, column=0, sticky="ew", pady=(0, 4))
        pf.columnconfigure(0, weight=1)

        self._status_label_val = ctk.CTkLabel(
            pf, text="Valitse syöte ja paina Muunna.", wraplength=680
        )
        self._status_label_val.grid(row=0, column=0, sticky="ew")

        self._eta_label = ctk.CTkLabel(pf, text="", text_color="gray")
        self._eta_label.grid(row=1, column=0, sticky="ew", pady=(2, 4))

        self._progress_bar = ctk.CTkProgressBar(pf)
        self._progress_bar.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._progress_bar.set(0)

        btn_row = ctk.CTkFrame(pf, fg_color="transparent")
        btn_row.grid(row=3, column=0)

        self._listen_btn = ctk.CTkButton(
            btn_row, text="Kuuntele", command=self._on_listen_click
        )
        self._listen_btn.grid(row=0, column=0, padx=(0, 6))

        self._convert_btn = ctk.CTkButton(
            btn_row, text="Muunna", command=self._on_convert_click
        )
        self._convert_btn.grid(row=0, column=1, padx=(0, 6))

        self._cancel_btn = ctk.CTkButton(
            btn_row, text="Peruuta", command=self._request_cancel
        )
        self._cancel_btn.grid(row=0, column=2, padx=(0, 6))
        self._cancel_btn.grid_remove()

        self._open_folder_btn = ctk.CTkButton(
            btn_row, text="Avaa kansio", command=self._open_output_folder,
            state="disabled",
        )
        self._open_folder_btn.grid(row=0, column=3)

    # ---- 5. Log panel (collapsible) -----------------------------------

    def _build_log_panel(
        self, parent: ctk.CTkFrame, row: int, stretch_row: int
    ) -> None:
        toggle_frame = ctk.CTkFrame(parent, fg_color="transparent")
        toggle_frame.grid(row=row, column=0, sticky="ew", pady=(4, 0))

        self._log_toggle_btn = ctk.CTkButton(
            toggle_frame, text="Näytä loki", command=self._toggle_log,
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
        """Let the user override the auto-generated output path."""
        current = self._out_entry.get()
        initial_dir = str(Path(current).parent) if current else str(Path.home())
        mode = OUTPUT_MODES[self._ui_lang].get(self._output_mode_cb.get(), "single")
        if mode == "single":
            path = filedialog.asksaveasfilename(
                title=self._s("save_as"),
                initialdir=initial_dir,
                defaultextension=".mp3",
                filetypes=[("MP3", "*.mp3")],
            )
            if path:
                self._output_path = path
                self._out_entry.configure(state="normal")
                self._out_entry.delete(0, tk.END)
                self._out_entry.insert(0, path)
                self._out_entry.configure(state="disabled")
        else:
            path = filedialog.askdirectory(
                title=self._s("save_as"),
                initialdir=initial_dir,
            )
            if path:
                self._output_path = path
                self._out_entry.configure(state="normal")
                self._out_entry.delete(0, tk.END)
                self._out_entry.insert(0, path)
                self._out_entry.configure(state="disabled")

    # ------------------------------------------------------------------
    # Engine installer (placeholder)
    # ------------------------------------------------------------------

    def _open_engine_manager(self) -> None:
        """Placeholder for the engine installer dialog."""
        dlg = ctk.CTkToplevel(self)
        dlg.title(self._s("engine_manager_title"))
        dlg.geometry("400x200")
        dlg.transient(self)
        dlg.grab_set()
        ctk.CTkLabel(
            dlg, text="Engine manager \u2014 coming soon",
            font=ctk.CTkFont(size=14),
        ).pack(expand=True, fill=tk.BOTH, padx=20, pady=20)
        ctk.CTkButton(dlg, text="Sulje", command=dlg.destroy).pack(pady=(0, 16))

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

        # Get text to synthesize.
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
            messagebox.showinfo(
                self._s("listen"),
                "Chatterbox ei tue kuuntelua tästä käyttöliittymästä."
                if self._ui_lang == "fi"
                else "Chatterbox does not support listen preview from this UI."
            )
            return

        engine = self._current_engine()
        voice = self._current_voice()
        if engine is None:
            messagebox.showerror(self._s("error"), "Moottoria ei löytynyt.")
            return
        if voice is None:
            messagebox.showerror(self._s("error"), "Valitse ääni.")
            return

        ffplay_path = self._find_ffplay()
        if ffplay_path is None:
            messagebox.showerror(
                self._s("error"),
                "ffplay not found. Please install ffmpeg."
            )
            return

        # Enter listening state.
        self._listening = True
        self._listen_btn.configure(state="disabled", text=self._s("listening"))
        self._convert_btn.configure(state="disabled")
        self._status_label_val.configure(text=self._s("listening"))

        threading.Thread(
            target=self._listen_worker,
            args=(text, engine, voice, ffplay_path),
            daemon=True,
            name="listen",
        ).start()

    def _listen_worker(
        self, text: str, engine: TTSEngine, voice: Voice, ffplay_path: str
    ) -> None:
        tmp_path: Optional[str] = None
        try:
            lang = self._current_language()
            ref_audio = self._ref_audio_var.get() or None
            voice_desc = self._voice_desc_var.get() or None

            tmp = tempfile.NamedTemporaryFile(
                prefix="listen_", suffix=".mp3", delete=False
            )
            tmp.close()
            tmp_path = tmp.name
            self._listen_temp_path = tmp_path

            engine.synthesize(
                text, tmp_path, voice.id, lang,
                lambda c, t, m: None,
                reference_audio=ref_audio,
                voice_description=voice_desc,
            )

            # Play via ffplay.
            proc = subprocess.Popen(
                [ffplay_path, "-nodisp", "-autoexit", tmp_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._listen_proc = proc
            proc.wait()

        except Exception as exc:
            self.after(0, lambda: self._status_label_val.configure(
                text=f"Listen error: {exc}"
            ))
        finally:
            # Clean up temp file.
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
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
            out = Path(output_path) if output_path else Path.home() / "Documents" / "AudiobookMaker" / "output.mp3"
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

            self._append_log(ev.raw_line or ev.kind)

            if ev.kind == "chunk":
                if ev.total_chunks > 0:
                    pct = ev.total_done / ev.total_chunks
                    self._progress_bar.set(pct)
                    self._status_label_val.configure(
                        text=f"{ev.total_done}/{ev.total_chunks}"
                    )

            elif ev.kind == "done":
                self._progress_bar.set(1.0)
                self._status_label_val.configure(text=self._s("done"))
                self._output_path = ev.output_path
                self._set_idle_state()
                return  # Stop pumping.

            elif ev.kind == "error":
                self._fail(ev.raw_line or "Unknown error")
                return  # Stop pumping.

            elif ev.kind == "log":
                pass  # Already appended to log above.

        # Reschedule if still running.
        if self._synth_running:
            self.after(self.POLL_INTERVAL_MS, self._pump_events)

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
        values = list(app._engine_cb.cget("values"))
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
