"""Collapsible Asetukset (settings) panel.

Extracted verbatim from ``UnifiedApp._build_settings_frame``. The panel is
a ghost-button header that toggles a surface-card body containing Speed,
Reference audio, Voice style, and output path + mode. Capability-specific
rows (Ref. ääni, Äänityyli) are hidden on first build and shown later by
engine-capability refresh logic on the host.
"""

from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

import customtkinter as ctk

from src import gui_style

if TYPE_CHECKING:
    from src.gui_unified import UnifiedApp


def build_settings_frame(host: "UnifiedApp", parent: ctk.CTkFrame, row: int) -> None:
    """Populate the Asetukset header + body on ``parent`` at ``row`` and
    ``row + 1``.

    Writes these attributes on ``host``:
        _settings_open, _settings_header_btn, _settings_outer, _settings_frame,
        _engine_status_lbl, _speed_label, _speed_cb, _ref_label, _ref_frame,
        _ref_audio_var, _ref_entry, _ref_browse_btn, _ref_clear_btn,
        _import_pack_btn, _desc_label, _voice_desc_var, _voice_desc_entry,
        _save_label, _out_entry, _out_browse_btn, _output_mode_label,
        _output_mode_cb
    """
    # Late import — ``_CLR_READY``, ``SPEED_OPTIONS``, ``OUTPUT_MODES`` are
    # module-level in gui_unified; importing at top would circularise.
    from src.gui_unified import _CLR_READY, OUTPUT_MODES, SPEED_OPTIONS

    # Collapsible header bar + hidden body. The header is a ghost
    # button (transparent bg, hover highlight only) that toggles the
    # surface-card body below. Chevron glyph stays unicode for now —
    # commit 6 will swap in the Lucide icon via ``gui_style.icon``.
    host._settings_open = False

    # Secondary-button look for the ghost header: it should read as
    # a clickable row header, not a primary action.
    header_frame = ctk.CTkFrame(parent, fg_color="transparent")
    header_frame.grid(
        row=row, column=0, sticky="ew",
        pady=(gui_style.PAD_XS, gui_style.PAD_XS // 2 or 1),
    )
    header_frame.columnconfigure(0, weight=1)

    host._settings_header_btn = ctk.CTkButton(
        header_frame,
        text="\u25B8 Asetukset",
        command=host._toggle_settings,
        anchor="w",
        height=32,
        font=gui_style.font_button(),
        fg_color="transparent",
        text_color=gui_style.TEXT_SECONDARY,
        hover_color=gui_style.BG_SURFACE_2,
        corner_radius=gui_style.RADIUS_SM,
    )
    host._settings_header_btn.grid(row=0, column=0, sticky="ew")

    # Surface card: 1px border + BG_SURFACE_1 fill gives the body a
    # subtle lift against BG_APP without relying on native shadows.
    settings_outer = ctk.CTkFrame(
        parent,
        fg_color=gui_style.BG_SURFACE_1,
        border_width=1,
        border_color=gui_style.BORDER_SUBTLE,
        corner_radius=gui_style.RADIUS_MD,
    )
    settings_outer.grid(
        row=row + 1, column=0, sticky="ew", pady=(0, gui_style.PAD_SM)
    )
    settings_outer.columnconfigure(0, weight=1)
    host._settings_outer = settings_outer
    settings_outer.grid_remove()  # Collapsed by default

    host._settings_frame = ctk.CTkFrame(settings_outer, fg_color="transparent")
    host._settings_frame.grid(
        row=0, column=0, sticky="ew",
        padx=gui_style.PAD_MD, pady=gui_style.PAD_MD,
    )
    host._settings_frame.columnconfigure(1, weight=1)
    host._settings_frame.columnconfigure(3, weight=1)
    settings = host._settings_frame

    srow = 0

    # Hidden compatibility widget for legacy engine-status hook points.
    host._engine_status_lbl = ctk.CTkLabel(
        settings, text="", text_color=_CLR_READY, wraplength=560,
    )
    # Not gridded — other code can still call .configure(text=...).

    # Shared style dict for secondary "utility" buttons inside the
    # settings panel (Selaa/Tyhjennä/Vaihda). Mirrors the treatment
    # used in ``build_action_row`` so the visual vocabulary is
    # consistent across the window.
    _sec = dict(
        font=gui_style.font_button(),
        fg_color=gui_style.BTN_SECONDARY_BG,
        hover_color=gui_style.BTN_SECONDARY_HOVER,
        text_color=gui_style.TEXT_PRIMARY,
        border_width=1,
        border_color=gui_style.BORDER_SUBTLE,
        corner_radius=gui_style.RADIUS_SM,
    )

    # Row 0: Speed. Kieli lives in the engine bar now, not here, so
    # the Nopeus widget gets promoted to column 0 and stays on its
    # own row instead of sharing with a (removed) language picker.
    host._speed_label = ctk.CTkLabel(
        settings, text="Nopeus:", font=gui_style.font_label(),
        text_color=gui_style.TEXT_SECONDARY,
    )
    host._speed_label.grid(
        row=srow, column=0, sticky="w", padx=(0, gui_style.PAD_SM)
    )
    host._speed_cb = ctk.CTkComboBox(
        settings,
        values=list(SPEED_OPTIONS["fi"].keys()), state="readonly", width=200,
        font=gui_style.font_body(),
    )
    host._speed_cb.set("Normaali")
    host._speed_cb.grid(row=srow, column=1, sticky="w")

    # Voice-pack import button — right-aligned on the Speed row. Stays
    # visible regardless of engine capability: importing a pack doesn't
    # require the current engine to support cloning, and the pack shows
    # up in the Voice dropdown next to Grandmom once the active engine
    # is Chatterbox. See UnifiedApp._import_voice_pack.
    host._import_pack_btn = ctk.CTkButton(
        settings, text="Tuo \u00e4\u00e4nipaketti\u2026",
        command=host._import_voice_pack,
        width=180, **_sec,
        image=gui_style.icon("folder", size=16),
        compound="left",
    )
    host._import_pack_btn.grid(
        row=srow, column=3, sticky="e", padx=(gui_style.PAD_SM, 0),
    )
    srow += 1

    # Row 3: Reference audio (voice cloning) — hidden when unsupported
    host._ref_label = ctk.CTkLabel(
        settings, text="Ref. ääni:", font=gui_style.font_label(),
        text_color=gui_style.TEXT_SECONDARY,
    )
    host._ref_label.grid(
        row=srow, column=0, sticky="w",
        padx=(0, gui_style.PAD_SM), pady=(gui_style.PAD_SM, 0),
    )
    host._ref_frame = ctk.CTkFrame(settings, fg_color="transparent")
    host._ref_frame.grid(
        row=srow, column=1, columnspan=3, sticky="ew",
        pady=(gui_style.PAD_SM, 0),
    )
    host._ref_frame.columnconfigure(0, weight=1)
    host._ref_audio_var = tk.StringVar(value="")
    host._ref_entry = ctk.CTkEntry(
        host._ref_frame, textvariable=host._ref_audio_var, state="disabled",
        font=gui_style.font_body(),
    )
    host._ref_entry.grid(
        row=0, column=0, sticky="ew", padx=(0, gui_style.PAD_SM)
    )
    host._ref_browse_btn = ctk.CTkButton(
        host._ref_frame, text="Selaa", command=host._browse_reference_audio,
        width=90, **_sec,
        image=gui_style.icon("mic", size=16),
        compound="left",
    )
    host._ref_browse_btn.grid(row=0, column=1)
    host._ref_clear_btn = ctk.CTkButton(
        host._ref_frame, text="Tyhjennä", command=host._clear_reference_audio,
        width=100, **_sec,
        image=gui_style.icon("x", size=16),
        compound="left",
    )
    host._ref_clear_btn.grid(row=0, column=2, padx=(gui_style.PAD_XS, 0))
    srow += 1

    # Row 4: Voice description — hidden when unsupported
    host._desc_label = ctk.CTkLabel(
        settings, text="Äänityyli:", font=gui_style.font_label(),
        text_color=gui_style.TEXT_SECONDARY,
    )
    host._desc_label.grid(
        row=srow, column=0, sticky="w",
        padx=(0, gui_style.PAD_SM), pady=(gui_style.PAD_SM, 0),
    )
    host._voice_desc_var = tk.StringVar(value="")
    host._voice_desc_entry = ctk.CTkEntry(
        settings, textvariable=host._voice_desc_var,
        font=gui_style.font_body(),
    )
    host._voice_desc_entry.grid(
        row=srow, column=1, columnspan=3, sticky="ew",
        pady=(gui_style.PAD_SM, 0),
    )
    srow += 1

    # Row 5: Tallenna + Tuloste on the SAME row (merged output controls).
    host._save_label = ctk.CTkLabel(
        settings, text="Tallenna:", font=gui_style.font_label(),
        text_color=gui_style.TEXT_SECONDARY,
    )
    host._save_label.grid(
        row=srow, column=0, sticky="w",
        padx=(0, gui_style.PAD_SM), pady=(gui_style.PAD_SM, 0),
    )
    out_frame = ctk.CTkFrame(settings, fg_color="transparent")
    out_frame.grid(
        row=srow, column=1, columnspan=3, sticky="ew",
        pady=(gui_style.PAD_SM, 0),
    )
    out_frame.columnconfigure(0, weight=1)

    host._out_entry = ctk.CTkEntry(
        out_frame, state="disabled", font=gui_style.font_body(),
    )
    host._out_entry.grid(
        row=0, column=0, sticky="ew", padx=(0, gui_style.PAD_SM)
    )

    host._out_browse_btn = ctk.CTkButton(
        out_frame, text="Vaihda\u2026", command=host._browse_output,
        width=110, **_sec,
        image=gui_style.icon("folder", size=16),
        compound="left",
    )
    host._out_browse_btn.grid(
        row=0, column=1, padx=(0, gui_style.PAD_SM)
    )

    host._output_mode_label = ctk.CTkLabel(
        out_frame, text="Tuloste:", font=gui_style.font_label(),
        text_color=gui_style.TEXT_SECONDARY,
    )
    host._output_mode_label.grid(
        row=0, column=2, sticky="w", padx=(0, gui_style.PAD_XS)
    )

    host._output_mode_cb = ctk.CTkComboBox(
        out_frame,
        values=list(OUTPUT_MODES["fi"].keys()), state="readonly",
        width=140, font=gui_style.font_body(),
    )
    host._output_mode_cb.set("Yksi MP3")
    host._output_mode_cb.grid(row=0, column=3, sticky="w")
    # Set initial auto-generated path.
    host._auto_output_path()

    # Initially hide capability-specific widgets.
    host._ref_label.grid_remove()
    host._ref_frame.grid_remove()
    host._desc_label.grid_remove()
    host._voice_desc_entry.grid_remove()
