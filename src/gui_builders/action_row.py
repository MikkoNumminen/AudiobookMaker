"""Primary-action row: big Convert, three secondary buttons, Cancel,
progress bar, and the status/ETA label pair.

Extracted verbatim from ``UnifiedApp._build_action_row``. Convert keeps
CTk's default accent fill (Cold Forge electric blue); the other buttons
get the muted secondary style so Convert stays visually dominant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from src import gui_style

if TYPE_CHECKING:
    from src.gui_unified import UnifiedApp


def build_action_row(host: "UnifiedApp", parent: ctk.CTkFrame, row: int) -> None:
    """Populate the primary-action row on ``parent`` at ``row``.

    Writes these attributes on ``host``:
        _convert_btn, _sample_btn, _listen_btn, _cancel_btn, _open_folder_btn,
        _progress_bar, _status_label_val, _eta_label
    """
    ar = ctk.CTkFrame(parent, fg_color="transparent")
    ar.grid(row=row, column=0, sticky="ew", pady=(0, gui_style.PAD_MD))
    ar.columnconfigure(0, weight=1)

    # Top: big primary + small secondaries.
    btn_row = ctk.CTkFrame(ar, fg_color="transparent")
    btn_row.grid(row=0, column=0, sticky="ew")
    btn_row.columnconfigure(0, weight=1)

    # The star of the show — wide, bold, clearly the primary action.
    # Convert, Make sample and Preview all ship disabled and are
    # switched on by _update_action_buttons_state once input + voice
    # are both configured (Convert/Sample) or output exists (Preview).
    host._convert_btn = ctk.CTkButton(
        btn_row, text="Muunna", command=host._on_convert_click,
        height=44, state="disabled",
        font=gui_style.font_primary_button(),
        image=gui_style.icon("play", size=20),
        compound="left",
    )
    host._convert_btn.grid(
        row=0, column=0, sticky="ew", padx=(0, gui_style.PAD_SM),
    )

    # Secondary button style — muted surface fill with a 1px border
    # so the primary (Convert) stays visually dominant.
    _sec = dict(
        font=gui_style.font_button(),
        fg_color=gui_style.BTN_SECONDARY_BG,
        hover_color=gui_style.BTN_SECONDARY_HOVER,
        text_color=gui_style.TEXT_PRIMARY,
        border_width=1,
        border_color=gui_style.BORDER_SUBTLE,
    )

    host._sample_btn = ctk.CTkButton(
        btn_row, text="Tee n\u00e4yte", command=host._on_sample_click,
        height=44, width=140, state="disabled", **_sec,
        image=gui_style.icon("music", size=18),
        compound="left",
    )
    host._sample_btn.grid(row=0, column=1, padx=(0, gui_style.PAD_MD))

    # Vertical separator marks the Configure/Produce → Review/Output
    # boundary. Convert + Make sample on the left of it are the
    # production path; Preview + Open folder on the right are for
    # reviewing what the production path actually emitted.
    sep = ctk.CTkFrame(
        btn_row, width=1, height=30, fg_color=gui_style.BORDER_SUBTLE,
    )
    sep.grid(row=0, column=2, sticky="ns", padx=(0, gui_style.PAD_MD))

    host._listen_btn = ctk.CTkButton(
        btn_row, text="Esikuuntele", command=host._on_listen_click,
        height=44, width=140, state="disabled", **_sec,
        image=gui_style.icon("volume", size=18),
        compound="left",
    )
    host._listen_btn.grid(row=0, column=3, padx=(0, gui_style.PAD_SM))

    host._cancel_btn = ctk.CTkButton(
        btn_row, text="Peruuta", command=host._request_cancel,
        height=44, width=110,
        font=gui_style.font_button(),
        fg_color=gui_style.DANGER,
        hover_color=("#8b0000", "#B03A36"),
        image=gui_style.icon("x", size=18),
        compound="left",
    )
    host._cancel_btn.grid(row=0, column=4, padx=(0, gui_style.PAD_SM))
    host._cancel_btn.grid_remove()  # Only visible while running.

    host._open_folder_btn = ctk.CTkButton(
        btn_row, text="Avaa kansio", command=host._open_output_folder,
        height=44, width=140, state="disabled", **_sec,
        image=gui_style.icon("folder", size=18),
        compound="left",
    )
    host._open_folder_btn.grid(row=0, column=5)

    # Bottom: progress bar + inline status (small, right-aligned).
    progress_row = ctk.CTkFrame(ar, fg_color="transparent")
    progress_row.grid(
        row=1, column=0, sticky="ew", pady=(gui_style.PAD_SM, 0),
    )
    progress_row.columnconfigure(0, weight=1)

    # Thicker bar reads as a real progress indicator instead of a hair
    # line — 10 px matches modern launcher norms.
    host._progress_bar = ctk.CTkProgressBar(progress_row, height=10)
    host._progress_bar.grid(
        row=0, column=0, sticky="ew", padx=(0, gui_style.PAD_SM),
    )
    host._progress_bar.set(0)
    # Progress bar is conversion-only — it has no meaning when nothing is
    # running. Hide at build time; _set_running_state grids it back in
    # when synthesis starts, _set_idle_state removes it again.
    host._progress_bar.grid_remove()

    host._status_label_val = ctk.CTkLabel(
        progress_row, text="",
        font=gui_style.font_small(),
        text_color=gui_style.TEXT_SECONDARY,
        width=180, anchor="e",
    )
    host._status_label_val.grid(row=0, column=1, sticky="e")

    # ETA label (kept for existing code paths; placed unobtrusively).
    host._eta_label = ctk.CTkLabel(
        ar, text="",
        font=gui_style.font_small(),
        text_color=gui_style.TEXT_MUTED,
    )
    host._eta_label.grid(row=2, column=0, sticky="e", pady=(2, 0))
