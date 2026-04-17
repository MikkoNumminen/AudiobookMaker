"""Generate the Cold Forge button icon set.

Renders 12 Lucide-style line-art icons as 48×48 PNGs in two ink colors
each (24 files total) into ``assets/icons/``. The renderer uses Pillow
only — no cairosvg dependency — so it runs in any dev environment that
already has the app's runtime deps installed.

Why we render at 192px and resize to 48px: Pillow's anti-aliasing on
``line`` and ``polygon`` is coarse at small sizes. Drawing at 4× and
downsampling with LANCZOS gives smooth strokes at the 20–24px render
sizes ``CTkImage`` actually uses in the GUI.

Re-run manually after changing any shape:

    python scripts/generate_icons.py

The resulting PNGs are tracked in git. The packaging spec globs them
into the frozen bundle; see ``tests/test_packaging_specs.py``.
"""
from __future__ import annotations

import math
from pathlib import Path
from PIL import Image, ImageDraw

# Ink colors match gui_style.TEXT_PRIMARY so icons read at the same
# contrast as surrounding button text.
INK_LIGHT = (31, 35, 40, 255)     # #1F2328 — for light-mode icons
INK_DARK = (230, 237, 243, 255)   # #E6EDF3 — for dark-mode icons

CANVAS = 192          # draw at 4× so anti-aliasing is clean
FINAL = 48            # on-disk size; CTkImage rescales at paint time
STROKE = 16           # in canvas-px; scales to ~4px at 48
MARGIN = 28           # in canvas-px; padding inside the 192-px bitmap

ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"


def _new_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _save(img: Image.Image, name: str) -> None:
    resized = img.resize((FINAL, FINAL), Image.LANCZOS)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    resized.save(ASSET_DIR / name, "PNG")


def _draw_pair(name: str, renderer) -> None:
    for suffix, ink in (("light", INK_LIGHT), ("dark", INK_DARK)):
        img, draw = _new_canvas()
        renderer(draw, ink)
        _save(img, f"{name}-{suffix}.png")


# ---------- Individual icon renderers -------------------------------------

def _play(draw: ImageDraw.ImageDraw, ink) -> None:
    # Right-pointing triangle, Lucide style (filled + slightly rounded).
    left = MARGIN + 10
    right = CANVAS - MARGIN - 4
    top = MARGIN + 4
    bottom = CANVAS - MARGIN - 4
    mid_y = (top + bottom) // 2
    draw.polygon([(left, top), (left, bottom), (right, mid_y)], fill=ink)


def _music(draw: ImageDraw.ImageDraw, ink) -> None:
    # Eighth note: stem + note-head + flag.
    # Stem runs top-right to bottom-left vertical.
    stem_x = CANVAS - MARGIN - 20
    stem_top = MARGIN
    stem_bot = CANVAS - MARGIN - 24
    draw.line([(stem_x, stem_top), (stem_x, stem_bot)], fill=ink, width=STROKE)
    # Flag: curved line off the top of the stem.
    draw.line([(stem_x, stem_top), (stem_x - 34, stem_top + 22)],
              fill=ink, width=STROKE)
    # Note-head (filled ellipse, slightly tilted — approximated axis-aligned).
    head_cx = stem_x - 6
    head_cy = stem_bot + 2
    draw.ellipse([head_cx - 26, head_cy - 18, head_cx + 14, head_cy + 18],
                 fill=ink)


def _x_icon(draw: ImageDraw.ImageDraw, ink) -> None:
    # Diagonal X. Width stays at STROKE; corner_radius not available,
    # so we just use line() which renders flat caps — fine at 48px.
    a = MARGIN + 4
    b = CANVAS - MARGIN - 4
    draw.line([(a, a), (b, b)], fill=ink, width=STROKE)
    draw.line([(a, b), (b, a)], fill=ink, width=STROKE)


def _folder(draw: ImageDraw.ImageDraw, ink) -> None:
    # Folder: trapezoidal tab + rectangular body, hollow outline.
    left = MARGIN - 4
    right = CANVAS - MARGIN + 4
    top = MARGIN + 16
    bottom = CANVAS - MARGIN
    tab_right = left + 58
    tab_top = top - 16
    # Outline via successive lines so we get a consistent stroke weight.
    pts = [
        (left, bottom),
        (left, tab_top),
        (tab_right, tab_top),
        (tab_right + 10, top),
        (right, top),
        (right, bottom),
        (left, bottom),
    ]
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=ink, width=STROKE)


def _settings(draw: ImageDraw.ImageDraw, ink) -> None:
    # Gear: 8 teeth + center circle. Simplified — a circle outline plus
    # 8 short radial bars. Center hole is a filled background-color dot
    # achieved by drawing a smaller circle in transparency (via erase).
    cx, cy = CANVAS // 2, CANVAS // 2
    outer_r = 62
    inner_r = 46
    tooth_r = 74
    # Teeth as short radial rectangles.
    for k in range(8):
        ang = k * math.pi / 4
        x1 = cx + math.cos(ang) * outer_r
        y1 = cy + math.sin(ang) * outer_r
        x2 = cx + math.cos(ang) * tooth_r
        y2 = cy + math.sin(ang) * tooth_r
        draw.line([(x1, y1), (x2, y2)], fill=ink, width=STROKE + 4)
    # Outer ring.
    draw.ellipse(
        [cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r],
        outline=ink, width=STROKE,
    )
    # Erase center to make it a ring.
    draw.ellipse(
        [cx - inner_r + 2, cy - inner_r + 2, cx + inner_r - 2, cy + inner_r - 2],
        fill=(0, 0, 0, 0),
    )
    # Center hole outline.
    hole_r = 20
    draw.ellipse(
        [cx - hole_r, cy - hole_r, cx + hole_r, cy + hole_r],
        outline=ink, width=STROKE,
    )


def _volume(draw: ImageDraw.ImageDraw, ink) -> None:
    # Speaker: square + triangle cone + two arcs for sound waves.
    sq_left = MARGIN
    sq_size = 40
    sq_top = CANVAS // 2 - sq_size // 2
    # Speaker body (filled).
    draw.rectangle(
        [sq_left, sq_top, sq_left + sq_size, sq_top + sq_size], fill=ink,
    )
    # Cone extends right.
    cone_tip_x = sq_left + sq_size + 48
    draw.polygon([
        (sq_left + sq_size, sq_top + 4),
        (sq_left + sq_size, sq_top + sq_size - 4),
        (cone_tip_x, sq_top + sq_size + 22),
        (cone_tip_x, sq_top - 22),
    ], fill=ink)
    # Two arcs for sound waves.
    wave_x = cone_tip_x + 6
    for radius, extent in ((22, 60), (42, 70)):
        draw.arc(
            [wave_x - radius, CANVAS // 2 - radius,
             wave_x + radius, CANVAS // 2 + radius],
            start=-extent, end=extent, fill=ink, width=STROKE,
        )


def _book(draw: ImageDraw.ImageDraw, ink) -> None:
    # Open-book outline: two pages meeting at a spine.
    left = MARGIN - 2
    right = CANVAS - MARGIN + 2
    top = MARGIN + 8
    bottom = CANVAS - MARGIN - 4
    mid = CANVAS // 2
    # Left page outline.
    draw.line([(left, top), (left, bottom)], fill=ink, width=STROKE)
    draw.line([(left, top), (mid, top + 6)], fill=ink, width=STROKE)
    draw.line([(left, bottom), (mid, bottom)], fill=ink, width=STROKE)
    # Right page.
    draw.line([(right, top), (right, bottom)], fill=ink, width=STROKE)
    draw.line([(right, top), (mid, top + 6)], fill=ink, width=STROKE)
    draw.line([(right, bottom), (mid, bottom)], fill=ink, width=STROKE)
    # Spine.
    draw.line([(mid, top + 6), (mid, bottom)], fill=ink, width=STROKE)


def _text(draw: ImageDraw.ImageDraw, ink) -> None:
    # Four horizontal text lines, second-to-last slightly shorter.
    left = MARGIN
    right = CANVAS - MARGIN
    short = left + (right - left) * 3 // 4
    ys = [MARGIN + 10, MARGIN + 50, CANVAS - MARGIN - 50, CANVAS - MARGIN - 10]
    for i, y in enumerate(ys):
        end = short if i == 2 else right
        draw.line([(left, y), (end, y)], fill=ink, width=STROKE)


def _list_icon(draw: ImageDraw.ImageDraw, ink) -> None:
    # Three rows: small dot + line each.
    left = MARGIN
    right = CANVAS - MARGIN
    ys = [MARGIN + 18, CANVAS // 2, CANVAS - MARGIN - 18]
    dot_r = 8
    for y in ys:
        draw.ellipse(
            [left - dot_r, y - dot_r, left + dot_r, y + dot_r], fill=ink,
        )
        draw.line([(left + 28, y), (right, y)], fill=ink, width=STROKE)


def _download(draw: ImageDraw.ImageDraw, ink) -> None:
    # Down-arrow into tray.
    cx = CANVAS // 2
    arrow_top = MARGIN + 2
    arrow_bot = CANVAS - MARGIN - 40
    # Shaft.
    draw.line([(cx, arrow_top), (cx, arrow_bot)], fill=ink, width=STROKE)
    # Arrowhead.
    head_half = 24
    draw.line([(cx - head_half, arrow_bot - head_half),
               (cx, arrow_bot)], fill=ink, width=STROKE)
    draw.line([(cx + head_half, arrow_bot - head_half),
               (cx, arrow_bot)], fill=ink, width=STROKE)
    # Tray: open bracket ⌊ ⌋.
    tray_y = CANVAS - MARGIN - 10
    tray_side = 28
    tray_left = MARGIN + 6
    tray_right = CANVAS - MARGIN - 6
    draw.line([(tray_left, tray_y - tray_side),
               (tray_left, tray_y)], fill=ink, width=STROKE)
    draw.line([(tray_right, tray_y - tray_side),
               (tray_right, tray_y)], fill=ink, width=STROKE)
    draw.line([(tray_left, tray_y), (tray_right, tray_y)],
              fill=ink, width=STROKE)


def _chevron_down(draw: ImageDraw.ImageDraw, ink) -> None:
    # V shape pointing down.
    left = MARGIN + 4
    right = CANVAS - MARGIN - 4
    top = CANVAS // 2 - 22
    bottom = CANVAS // 2 + 32
    cx = CANVAS // 2
    draw.line([(left, top), (cx, bottom)], fill=ink, width=STROKE + 2)
    draw.line([(cx, bottom), (right, top)], fill=ink, width=STROKE + 2)


def _mic(draw: ImageDraw.ImageDraw, ink) -> None:
    # Microphone: rounded capsule + stand.
    cx = CANVAS // 2
    cap_top = MARGIN - 2
    cap_bot = CANVAS // 2 + 14
    cap_half = 24
    # Capsule (filled rounded rect — approximated with a rectangle
    # capped by circles top and bottom).
    draw.ellipse(
        [cx - cap_half, cap_top, cx + cap_half, cap_top + 2 * cap_half],
        fill=ink,
    )
    draw.rectangle(
        [cx - cap_half, cap_top + cap_half, cx + cap_half, cap_bot - cap_half],
        fill=ink,
    )
    draw.ellipse(
        [cx - cap_half, cap_bot - 2 * cap_half, cx + cap_half, cap_bot],
        fill=ink,
    )
    # Arc beneath forming the "U" under the capsule.
    arc_radius = 36
    draw.arc(
        [cx - arc_radius, cap_bot - arc_radius,
         cx + arc_radius, cap_bot + arc_radius],
        start=20, end=160, fill=ink, width=STROKE,
    )
    # Stand stem.
    stand_top = cap_bot + arc_radius - 4
    stand_bot = CANVAS - MARGIN
    draw.line([(cx, stand_top), (cx, stand_bot)], fill=ink, width=STROKE)
    # Base.
    draw.line([(cx - 24, stand_bot), (cx + 24, stand_bot)],
              fill=ink, width=STROKE)


ICONS = {
    "play": _play,
    "music": _music,
    "x": _x_icon,
    "folder": _folder,
    "settings": _settings,
    "volume": _volume,
    "book": _book,
    "text": _text,
    "list": _list_icon,
    "download": _download,
    "chevron-down": _chevron_down,
    "mic": _mic,
}


def main() -> None:
    for name, renderer in ICONS.items():
        _draw_pair(name, renderer)
    print(f"Wrote {2 * len(ICONS)} PNGs to {ASSET_DIR}")


if __name__ == "__main__":
    main()
