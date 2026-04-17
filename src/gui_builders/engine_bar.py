"""Always-visible engine-and-voice picker card.

Extracted verbatim from ``UnifiedApp._build_engine_bar``. Holds the
Language / Engine / Voice dropdowns plus the Chatterbox chunk-chars
spinbox. Layout only — the Kieli → Moottori → Ääni funnel and the
deferred callback wiring live unchanged in ``UnifiedApp``.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

import customtkinter as ctk

from src import gui_style

if TYPE_CHECKING:
    from src.gui_unified import UnifiedApp


def build_engine_bar(host: "UnifiedApp", parent: ctk.CTkFrame, row: int) -> None:
    """Populate the engine/voice card on ``parent`` at ``row``.

    Writes these attributes on ``host``:
        _engine_bar, _engine_section_lbl, _tts_lang_label, _lang_cb,
        _engine_label, _engine_cb, _voice_label, _voice_cb, _voice_count_lbl,
        _test_btn, _chunk_chars_label, _chunk_chars_var, _chunk_chars_spin
    """
    # Late import — ``LANGUAGES`` is a module-level constant in gui_unified.
    from src.gui_unified import LANGUAGES

    bar = ctk.CTkFrame(
        parent,
        fg_color=gui_style.BG_SURFACE_1,
        corner_radius=gui_style.RADIUS_MD,
        border_width=1,
        border_color=gui_style.BORDER_SUBTLE,
    )
    bar.grid(row=row, column=0, sticky="ew", pady=(0, gui_style.PAD_MD))
    bar.columnconfigure(1, weight=1)
    bar.columnconfigure(3, weight=1)
    host._engine_bar = bar

    # Row 0: subtle section title so the card isn't anonymous.
    # The translation key lives in _STRINGS; the label refreshes via
    # _apply_ui_language just like the other localized labels.
    host._engine_section_lbl = ctk.CTkLabel(
        bar,
        text=host._s("section_voice"),
        font=gui_style.font_label(),
        text_color=gui_style.TEXT_SECONDARY,
        anchor="w",
    )
    host._engine_section_lbl.grid(
        row=0, column=0, columnspan=5, sticky="w",
        padx=gui_style.PAD_MD, pady=(gui_style.PAD_MD, 0),
    )

    # Row 1: Kieli + Moottori.
    host._tts_lang_label = ctk.CTkLabel(
        bar, text="Kieli:",
        font=gui_style.font_label(),
        text_color=gui_style.TEXT_SECONDARY,
    )
    host._tts_lang_label.grid(
        row=1, column=0, sticky="w",
        padx=(gui_style.PAD_MD, gui_style.PAD_SM),
        pady=(gui_style.PAD_SM, gui_style.PAD_XS),
    )
    # Note: command= is intentionally NOT wired here. CTkComboBox.set()
    # triggers the command callback, and both _apply_loaded_config()
    # and the initial .set("Suomi") below would fire the cascade
    # (re-filter engines, refresh voices, save config) before the rest
    # of the widget tree exists. We attach the callbacks at the end of
    # __init__ via _wire_engine_bar_callbacks() so they only ever run
    # in response to real user clicks.
    host._lang_cb = ctk.CTkComboBox(
        bar,
        values=list(LANGUAGES.keys()), state="readonly",
    )
    host._lang_cb.set("Suomi")
    host._lang_cb.grid(
        row=1, column=1, sticky="ew",
        padx=(0, gui_style.PAD_MD),
        pady=(gui_style.PAD_SM, gui_style.PAD_XS),
    )

    host._engine_label = ctk.CTkLabel(
        bar, text="Moottori:",
        font=gui_style.font_label(),
        text_color=gui_style.TEXT_SECONDARY,
    )
    host._engine_label.grid(
        row=1, column=2, sticky="w",
        padx=(0, gui_style.PAD_SM),
        pady=(gui_style.PAD_SM, gui_style.PAD_XS),
    )
    host._engine_cb = ctk.CTkComboBox(bar, state="readonly")
    host._engine_cb.grid(
        row=1, column=3, sticky="ew",
        padx=(0, gui_style.PAD_XS),
        pady=(gui_style.PAD_SM, gui_style.PAD_XS),
    )
    # Engine combobox stays empty until _apply_loaded_config() resolves
    # the user's saved Language and calls _populate_engine_list() with
    # the right language context. Populating here would build the
    # dropdown for "Suomi" first and rebuild it ms later for the
    # actual saved Language — invisible to the user but flagged by the
    # sequencing audit as the wrong shape.

    # Row 2: Ääni + voice-count side label + Testaa.
    host._voice_label = ctk.CTkLabel(
        bar, text="Ääni:",
        font=gui_style.font_label(),
        text_color=gui_style.TEXT_SECONDARY,
    )
    host._voice_label.grid(
        row=2, column=0, sticky="w",
        padx=(gui_style.PAD_MD, gui_style.PAD_SM),
        pady=(gui_style.PAD_XS, gui_style.PAD_MD),
    )
    host._voice_cb = ctk.CTkComboBox(bar, state="readonly")
    host._voice_cb.grid(
        row=2, column=1, sticky="ew",
        padx=(0, gui_style.PAD_MD),
        pady=(gui_style.PAD_XS, gui_style.PAD_MD),
    )

    # Subtle side-label — honest about how many voices are available in
    # the selected language without colouring dropdown items (which
    # CTkComboBox doesn't support).
    host._voice_count_lbl = ctk.CTkLabel(
        bar, text="",
        font=gui_style.font_small(),
        text_color=gui_style.TEXT_MUTED,
    )
    host._voice_count_lbl.grid(
        row=2, column=2, columnspan=2, sticky="w",
        padx=(0, gui_style.PAD_SM),
        pady=(gui_style.PAD_XS, gui_style.PAD_MD),
    )

    host._test_btn = ctk.CTkButton(
        bar, text="Testaa ääni", command=host._on_test_voice,
        width=140,
        font=gui_style.font_button(),
        fg_color=gui_style.BTN_SECONDARY_BG,
        hover_color=gui_style.BTN_SECONDARY_HOVER,
        text_color=gui_style.TEXT_PRIMARY,
        border_width=1,
        border_color=gui_style.BORDER_SUBTLE,
        image=gui_style.icon("volume", size=16),
        compound="left",
    )
    host._test_btn.grid(
        row=2, column=4,
        padx=(0, gui_style.PAD_MD),
        pady=(gui_style.PAD_XS, gui_style.PAD_MD),
    )

    # Row 3: Chatterbox-specific tuning — chunk size (characters per
    # synthesis chunk). Default 300 matches the upstream consensus.
    # When left at 300 the mixin omits the flag, so the CLI default
    # wins and we don't leak GUI state into otherwise-default runs.
    # Chatterbox chunk size (chars).
    host._chunk_chars_label = ctk.CTkLabel(
        bar, text=host._s("chunk_chars_label"),
        font=gui_style.font_label(),
        text_color=gui_style.TEXT_SECONDARY,
    )
    host._chunk_chars_label.grid(
        row=3, column=0, columnspan=2, sticky="w",
        padx=(gui_style.PAD_MD, gui_style.PAD_SM),
        pady=(0, gui_style.PAD_MD),
    )
    host._chunk_chars_var = tk.IntVar(value=300)
    host._chunk_chars_spin = ttk.Spinbox(
        bar,
        from_=100, to=1000, increment=50,
        textvariable=host._chunk_chars_var,
        width=6,
    )
    host._chunk_chars_spin.grid(
        row=3, column=2, sticky="w",
        padx=(0, gui_style.PAD_SM),
        pady=(0, gui_style.PAD_MD),
    )
