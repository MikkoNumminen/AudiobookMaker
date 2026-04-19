"""Generate the GitHub repository social-preview image.

GitHub serves a 1280×640 PNG for link-unfurl previews on services like
WhatsApp, Telegram, Slack, Discord, X, and Facebook. Without one, the
unfurled card falls back to a generic placeholder.

This script renders ``assets/social_preview.png`` from:

* ``assets/icon.png`` — the goat mascot (256×256 RGBA), upscaled and
  placed on the left half of the canvas.
* The app name and one-line tagline rendered on the right.

The teal background and cream wordmark sample directly from the icon
so the preview reads as the same visual identity as the in-app logo.

Re-run after changing the wordmark text or the source icon:

    python scripts/generate_social_preview.py

Then upload the resulting PNG via:

    https://github.com/MikkoNumminen/AudiobookMaker/settings
    → Social preview → Edit → Upload an image

(The GitHub API has no endpoint for setting the social preview image,
so this last step is unavoidable manual work.)
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ICON = REPO_ROOT / "assets" / "icon.png"
OUTPUT = REPO_ROOT / "assets" / "social_preview.png"

# Canvas — GitHub's recommended social-preview size.
WIDTH, HEIGHT = 1280, 640

# Palette — sampled from the goat icon disc and face.
BG_COLOR = (30, 80, 80, 255)       # #1E5050 — same teal as the disc
INK_COLOR = (235, 230, 215, 255)   # cream, ~goat-face tone, high contrast
SUB_COLOR = (200, 195, 180, 255)   # slightly muted cream for the tagline

TITLE_TEXT = "AudiobookMaker"
TAGLINE_TEXT = "PDF · EPUB · TXT  →  audiobook"

# Font candidates, first existing one wins. Bundled DejaVu (shipped with
# Pillow) is the cross-platform fallback so this still runs on CI/Linux.
TITLE_FONT_CANDIDATES = (
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
)
TAGLINE_FONT_CANDIDATES = (
    "C:/Windows/Fonts/seguisb.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "DejaVuSans.ttf",
)


def _load_font(candidates: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    # Last-ditch: Pillow's default bitmap font (small, ugly, but never fails).
    return ImageFont.load_default()


def _measure(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def render() -> Path:
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), BG_COLOR)

    # ---- Goat: upscale the 256×256 source to 440 px on the left.
    # 1.72× scale-up softens slightly but keeps lines crisper than the
    # 2× we tried first. The integer source size also helps LANCZOS.
    goat = Image.open(SOURCE_ICON).convert("RGBA")
    goat_size = 440
    goat = goat.resize((goat_size, goat_size), Image.LANCZOS)
    goat_x = 70
    goat_y = (HEIGHT - goat_size) // 2
    canvas.alpha_composite(goat, dest=(goat_x, goat_y))

    # ---- Text block on the right.
    draw = ImageDraw.Draw(canvas)
    # Title at 80pt fits "AudiobookMaker" in ~660 px; canvas remainder
    # after goat + 60px gap is 710 px. Comfortable, with right margin.
    title_font = _load_font(TITLE_FONT_CANDIDATES, size=80)
    tagline_font = _load_font(TAGLINE_FONT_CANDIDATES, size=38)

    text_x = goat_x + goat_size + 60
    title_w, title_h = _measure(draw, TITLE_TEXT, title_font)
    tagline_w, tagline_h = _measure(draw, TAGLINE_TEXT, tagline_font)

    block_h = title_h + 28 + tagline_h
    block_top = (HEIGHT - block_h) // 2

    draw.text((text_x, block_top), TITLE_TEXT, font=title_font, fill=INK_COLOR)
    draw.text(
        (text_x, block_top + title_h + 28),
        TAGLINE_TEXT,
        font=tagline_font,
        fill=SUB_COLOR,
    )

    # ---- Save flat RGB; PNG with alpha works on GitHub but RGB is leaner.
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(OUTPUT, "PNG", optimize=True)
    return OUTPUT


if __name__ == "__main__":
    out = render()
    print(f"Wrote {out} ({WIDTH}×{HEIGHT})")
