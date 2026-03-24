"""Tkinter GUI for AudiobookMaker.

Provides a simple window for selecting a PDF, configuring TTS settings,
and converting to MP3. Runs TTS in a background thread to keep the UI
responsive.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from src.pdf_parser import parse_pdf
from src.tts_engine import TTSConfig, text_to_speech, chapters_to_speech, VOICES


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WINDOW_TITLE = "AudiobookMaker"
WINDOW_MIN_W = 600
WINDOW_MIN_H = 420

LANGUAGES = {
    "Suomi": "fi",
    "English": "en",
}

SPEED_OPTIONS = {
    "Hidas (-25%)": "-25%",
    "Normaali": "+0%",
    "Nopea (+25%)": "+25%",
    "Erittäin nopea (+50%)": "+50%",
}

OUTPUT_MODES = {
    "Yksi MP3-tiedosto": "single",
    "Yksi tiedosto per luku": "chapters",
}


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------


class App(tk.Tk):
    """Root application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title(WINDOW_TITLE)
        self.minsize(WINDOW_MIN_W, WINDOW_MIN_H)
        self.resizable(True, True)
        self._center_window()

        # State
        self._pdf_path: Optional[str] = None
        self._output_path: Optional[str] = None
        self._converting = False

        self._build_ui()

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
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Construct all widgets."""
        main = ttk.Frame(self, padding=16)
        main.pack(fill=tk.BOTH, expand=True)

        # ---- PDF file selection ----
        ttk.Label(main, text="PDF-tiedosto", font=("", 10, "bold")).grid(
            row=0, column=0, sticky=tk.W, pady=(0, 2)
        )
        pdf_row = ttk.Frame(main)
        pdf_row.grid(row=1, column=0, sticky=tk.EW, pady=(0, 12))
        main.columnconfigure(0, weight=1)
        pdf_row.columnconfigure(0, weight=1)

        self._pdf_var = tk.StringVar(value="Ei tiedostoa valittu")
        ttk.Entry(pdf_row, textvariable=self._pdf_var, state="readonly").grid(
            row=0, column=0, sticky=tk.EW, padx=(0, 8)
        )
        ttk.Button(pdf_row, text="Selaa…", command=self._browse_pdf).grid(
            row=0, column=1
        )

        # ---- Settings frame ----
        settings = ttk.LabelFrame(main, text="Asetukset", padding=8)
        settings.grid(row=2, column=0, sticky=tk.EW, pady=(0, 12))
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)

        # Language
        ttk.Label(settings, text="Kieli:").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        self._lang_var = tk.StringVar(value="Suomi")
        lang_cb = ttk.Combobox(
            settings,
            textvariable=self._lang_var,
            values=list(LANGUAGES.keys()),
            state="readonly",
            width=14,
        )
        lang_cb.grid(row=0, column=1, sticky=tk.W)
        lang_cb.bind("<<ComboboxSelected>>", self._on_language_changed)

        # Speed
        ttk.Label(settings, text="Nopeus:").grid(row=0, column=2, sticky=tk.W, padx=(16, 8))
        self._speed_var = tk.StringVar(value="Normaali")
        ttk.Combobox(
            settings,
            textvariable=self._speed_var,
            values=list(SPEED_OPTIONS.keys()),
            state="readonly",
            width=20,
        ).grid(row=0, column=3, sticky=tk.W)

        # Voice
        ttk.Label(settings, text="Ääni:").grid(row=1, column=0, sticky=tk.W, padx=(0, 8), pady=(8, 0))
        self._voice_var = tk.StringVar()
        self._voice_cb = ttk.Combobox(
            settings,
            textvariable=self._voice_var,
            state="readonly",
            width=26,
        )
        self._voice_cb.grid(row=1, column=1, columnspan=3, sticky=tk.W, pady=(8, 0))
        self._refresh_voice_list()

        # Output mode
        ttk.Label(settings, text="Tulostus:").grid(row=2, column=0, sticky=tk.W, padx=(0, 8), pady=(8, 0))
        self._output_mode_var = tk.StringVar(value="Yksi MP3-tiedosto")
        ttk.Combobox(
            settings,
            textvariable=self._output_mode_var,
            values=list(OUTPUT_MODES.keys()),
            state="readonly",
            width=26,
        ).grid(row=2, column=1, columnspan=3, sticky=tk.W, pady=(8, 0))

        # ---- Output file selection ----
        ttk.Label(main, text="Tallennuspaikka", font=("", 10, "bold")).grid(
            row=3, column=0, sticky=tk.W, pady=(0, 2)
        )
        out_row = ttk.Frame(main)
        out_row.grid(row=4, column=0, sticky=tk.EW, pady=(0, 12))
        out_row.columnconfigure(0, weight=1)

        self._out_var = tk.StringVar(value="Ei valittu")
        ttk.Entry(out_row, textvariable=self._out_var, state="readonly").grid(
            row=0, column=0, sticky=tk.EW, padx=(0, 8)
        )
        ttk.Button(out_row, text="Valitse…", command=self._browse_output).grid(
            row=0, column=1
        )

        # ---- Progress area ----
        self._status_var = tk.StringVar(value="Valitse PDF-tiedosto aloittaaksesi.")
        ttk.Label(main, textvariable=self._status_var, wraplength=560).grid(
            row=5, column=0, sticky=tk.W, pady=(0, 4)
        )

        self._progress_var = tk.DoubleVar(value=0.0)
        self._progress_bar = ttk.Progressbar(
            main, variable=self._progress_var, maximum=100
        )
        self._progress_bar.grid(row=6, column=0, sticky=tk.EW, pady=(0, 12))

        # ---- Convert button ----
        self._convert_btn = ttk.Button(
            main, text="Muunna äänikirjaksi", command=self._start_conversion
        )
        self._convert_btn.grid(row=7, column=0, pady=(0, 4))

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _browse_pdf(self) -> None:
        path = filedialog.askopenfilename(
            title="Valitse PDF-tiedosto",
            filetypes=[("PDF-tiedostot", "*.pdf"), ("Kaikki tiedostot", "*.*")],
        )
        if path:
            self._pdf_path = path
            self._pdf_var.set(path)
            self._status_var.set("PDF valittu. Voit aloittaa muunnoksen.")
            # Auto-suggest output path
            if not self._output_path:
                suggested = str(Path(path).with_suffix(".mp3"))
                self._out_var.set(suggested)
                self._output_path = suggested

    def _browse_output(self) -> None:
        mode = OUTPUT_MODES[self._output_mode_var.get()]
        if mode == "single":
            path = filedialog.asksaveasfilename(
                title="Tallenna MP3-tiedostona",
                defaultextension=".mp3",
                filetypes=[("MP3-tiedostot", "*.mp3")],
            )
            if path:
                self._output_path = path
                self._out_var.set(path)
        else:
            # For chapter mode, select a directory
            path = filedialog.askdirectory(title="Valitse kohdekansio")
            if path:
                self._output_path = path
                self._out_var.set(path)

    def _on_language_changed(self, _event: object = None) -> None:
        self._refresh_voice_list()

    def _refresh_voice_list(self) -> None:
        lang_code = LANGUAGES.get(self._lang_var.get(), "fi")
        voices = VOICES.get(lang_code, VOICES["fi"])
        voice_names = list(voices.values())
        self._voice_cb["values"] = voice_names
        self._voice_cb.set(voices["default"])

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def _start_conversion(self) -> None:
        if self._converting:
            return

        if not self._pdf_path:
            messagebox.showerror("Virhe", "Valitse ensin PDF-tiedosto.")
            return

        if not self._output_path:
            messagebox.showerror("Virhe", "Valitse tallennuspaikka.")
            return

        self._converting = True
        self._convert_btn.config(state=tk.DISABLED)
        self._progress_var.set(0)
        self._status_var.set("Parsitaan PDF…")

        thread = threading.Thread(target=self._conversion_worker, daemon=True)
        thread.start()

    def _conversion_worker(self) -> None:
        """Run in background thread — must not call Tkinter directly."""
        try:
            # Parse PDF
            self._safe_update_status("Luetaan PDF-tiedostoa…")
            book = parse_pdf(self._pdf_path)

            lang_code = LANGUAGES.get(self._lang_var.get(), "fi")
            voice = self._voice_var.get() or VOICES[lang_code]["default"]
            rate = SPEED_OPTIONS.get(self._speed_var.get(), "+0%")

            config = TTSConfig(language=lang_code, voice=voice, rate=rate)
            mode = OUTPUT_MODES[self._output_mode_var.get()]

            total_chars = book.total_chars
            processed_chars = 0

            def progress_cb(current: int, total: int, msg: str) -> None:
                nonlocal processed_chars
                if total > 0:
                    pct = (current / total) * 100
                else:
                    pct = 0
                self._safe_update_progress(pct, msg)

            if mode == "single":
                self._safe_update_status("Muunnetaan tekstiä puheeksi…")
                text_to_speech(book.full_text, self._output_path, config, progress_cb)
            else:
                chapters = [(ch.title, ch.content) for ch in book.chapters]
                chapters_to_speech(chapters, self._output_path, config, progress_cb)

            self._safe_conversion_done(success=True, message="Valmis! Äänikirja tallennettu.")

        except Exception as exc:
            self._safe_conversion_done(success=False, message=str(exc))

    # ------------------------------------------------------------------
    # Thread-safe UI updates via after()
    # ------------------------------------------------------------------

    def _safe_update_status(self, msg: str) -> None:
        self.after(0, lambda: self._status_var.set(msg))

    def _safe_update_progress(self, pct: float, msg: str) -> None:
        def _update() -> None:
            self._progress_var.set(pct)
            self._status_var.set(msg)
        self.after(0, _update)

    def _safe_conversion_done(self, success: bool, message: str) -> None:
        def _done() -> None:
            self._converting = False
            self._convert_btn.config(state=tk.NORMAL)
            self._progress_var.set(100 if success else 0)
            self._status_var.set(message)
            if success:
                messagebox.showinfo("Valmis", message)
            else:
                messagebox.showerror(
                    "Virhe",
                    f"Muunnos epäonnistui:\n\n{message}\n\n"
                    "Tarkista että internet-yhteys toimii ja PDF sisältää kopioitavaa tekstiä.",
                )
        self.after(0, _done)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run() -> None:
    """Launch the GUI application."""
    app = App()
    app.mainloop()
