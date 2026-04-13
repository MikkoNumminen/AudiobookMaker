"""User-preferences persistence for AudiobookMaker.

Stores the last-used engine, voice, language, and speed choices in a
small JSON file under ~/.audiobookmaker/config.json so the GUI can
remember them between sessions.

Named app_config instead of config to avoid shadowing piper.config.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


CONFIG_DIR = Path.home() / ".audiobookmaker"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class UserConfig:
    """Persisted GUI preferences."""

    engine_id: str = "edge"
    """Which TTS engine the user last selected."""

    language: str = "fi"
    """Short language code of the last selected language."""

    voice_id: str = ""
    """Engine-specific voice id (may be empty = use engine default)."""

    speed: str = "+0%"
    """edge-tts style speed adjustment string."""

    reference_audio: str = ""
    """Path to a reference audio file for cloning engines, if any."""

    voice_description: str = ""
    """Free-text description of the desired voice for engines that
    support it (e.g. VoxCPM2). Ignored by engines that don't."""

    input_mode: str = "pdf"
    """Last used input mode: 'pdf' or 'text'."""

    output_mode: str = "single"
    """Output mode: 'single' (one MP3) or 'chapters' (per chapter)."""

    log_panel_visible: bool = False
    """Whether the log panel was visible last session."""

    ui_language: str = ""
    """UI display language: 'fi' (Finnish) or 'en' (English).
    Empty string means auto-detect from system locale on first run."""


def load() -> UserConfig:
    """Load user config from disk, or return defaults if missing/broken."""
    if not CONFIG_FILE.exists():
        return UserConfig()
    try:
        raw: dict[str, Any] = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return UserConfig()

    # Only accept keys that match the dataclass fields — ignore any legacy
    # entries so a stale config file can't crash the app.
    allowed = set(UserConfig.__dataclass_fields__.keys())
    filtered = {k: v for k, v in raw.items() if k in allowed and isinstance(v, (str, bool))}
    return UserConfig(**filtered)


def save(config: UserConfig) -> None:
    """Persist user config to disk. Failures are silently ignored."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(asdict(config), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        # Persistence is best-effort; don't crash the GUI if the home
        # directory is unwritable for some reason.
        pass
