"""Hero header bar: goat mascot + app name + tagline on the left, UI
language picker + engine-manager button on the right.

Extracted verbatim from ``UnifiedApp._build_header_bar``. The goal is
GUI-layout isolation only — zero behavior change. The host is the
``UnifiedApp`` instance; the builder assigns all the widget references
back onto it so language-toggle handlers and tests keep reaching the
same attribute names they always did.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from src import gui_style

if TYPE_CHECKING:
    from src.gui_unified import UnifiedApp


def build_header_bar(host: "UnifiedApp", parent: ctk.CTkFrame, row: int) -> None:
    """Populate the hero header band on ``parent`` at ``row``.

    Writes these attributes on ``host``:
        _hero_logo, _hero_logo_image (optional), _hero_title, _hero_tagline,
        _ui_lang_cb, _install_engines_btn
    """
    # Import here to avoid a circular import at module load — ``_APP_ROOT``
    # is resolved inside gui_unified based on frozen-app detection.
    from src.gui_unified import _APP_ROOT

    bar = ctk.CTkFrame(
        parent,
        fg_color=gui_style.BG_SURFACE_1,
        corner_radius=gui_style.RADIUS_LG,
        border_width=1,
        border_color=gui_style.BORDER_SUBTLE,
    )
    bar.grid(
        row=row, column=0, sticky="ew",
        pady=(0, gui_style.PAD_MD),
    )
    # Col 1 expands so the right-side controls hug the right edge.
    bar.columnconfigure(1, weight=1)

    # Col 0: goat mascot logo. CTkImage handles HiDPI scaling for us;
    # we ship one PNG and let CTk resize it. Loading is best-effort —
    # a missing Pillow or missing asset falls back to an empty label.
    host._hero_logo = ctk.CTkLabel(bar, text="")
    try:
        from PIL import Image

        logo_path = _APP_ROOT / "assets" / "icon.png"
        if logo_path.exists():
            host._hero_logo_image = ctk.CTkImage(
                light_image=Image.open(logo_path),
                dark_image=Image.open(logo_path),
                size=(48, 48),
            )
            host._hero_logo.configure(image=host._hero_logo_image)
    except Exception:
        # Pillow missing or asset decode failed — the label just
        # stays empty, the rest of the header still renders fine.
        pass
    host._hero_logo.grid(
        row=0, column=0, rowspan=2,
        padx=(gui_style.PAD_LG, gui_style.PAD_MD),
        pady=gui_style.PAD_MD,
    )

    # Col 1: title + tagline, stacked. Kept as attributes so the
    # language-toggle handler and tests can reach them.
    title_stack = ctk.CTkFrame(bar, fg_color="transparent")
    title_stack.grid(row=0, column=1, rowspan=2, sticky="w",
                     pady=gui_style.PAD_MD)

    host._hero_title = ctk.CTkLabel(
        title_stack,
        text="AudiobookMaker",
        font=gui_style.font_hero(),
        text_color=gui_style.TEXT_PRIMARY,
        anchor="w",
    )
    host._hero_title.grid(row=0, column=0, sticky="w")

    host._hero_tagline = ctk.CTkLabel(
        title_stack,
        text=host._hero_tagline_text(),
        font=gui_style.font_tagline(),
        text_color=gui_style.TEXT_SECONDARY,
        anchor="w",
    )
    host._hero_tagline.grid(row=1, column=0, sticky="w")

    # Col 2: right-side controls (UI language + engine manager).
    right = ctk.CTkFrame(bar, fg_color="transparent")
    right.grid(
        row=0, column=2, rowspan=2, sticky="e",
        padx=(gui_style.PAD_MD, gui_style.PAD_LG),
        pady=gui_style.PAD_MD,
    )

    # Language toggle — compact combobox (kept as _ui_lang_cb so the
    # existing language-change handler keeps working).
    host._ui_lang_cb = ctk.CTkComboBox(
        right,
        values=["Suomi", "English"], state="readonly", width=100,
        command=host._on_ui_language_changed,
    )
    host._ui_lang_cb.set("Suomi" if host._ui_lang == "fi" else "English")
    host._ui_lang_cb.grid(row=0, column=0, padx=(0, gui_style.PAD_SM))

    # Engine manager — opens the in-place settings view (no Toplevel).
    host._install_engines_btn = ctk.CTkButton(
        right, text="Moottorit\u2026",
        command=host._show_settings_view, width=150,
        image=gui_style.icon("settings", size=16),
        compound="left",
    )
    host._install_engines_btn.grid(row=0, column=1)
