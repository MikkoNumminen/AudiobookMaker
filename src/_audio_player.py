"""In-process audio playback for the GUI.

Thin wrapper around ``pygame.mixer`` so the Preview button can play
clips without shelling out to the OS default player. The mixer is
initialised lazily on the first ``play()`` call so importing this
module is cheap and safe on machines without an audio device.
"""
from __future__ import annotations

import atexit
import logging
import threading
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


class AudioPlayer:
    """Single-clip audio player backed by pygame.mixer.music."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._initialised = False
        self._atexit_registered = False

    def _ensure_init(self) -> None:
        if self._initialised:
            return
        import pygame  # local import: don't pay the cost at module import time

        pygame.mixer.init()
        self._initialised = True
        if not self._atexit_registered:
            atexit.register(self._shutdown)
            self._atexit_registered = True

    def play(self, path: Union[str, Path]) -> None:
        """Stop any current clip, then load and play *path*."""
        import pygame

        with self._lock:
            self._ensure_init()
            # Stop anything that's playing so the new clip starts cleanly.
            try:
                pygame.mixer.music.stop()
            except pygame.error as exc:
                logger.debug("mixer.stop failed before play: %s", exc)
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.play()

    def stop(self) -> None:
        """Halt playback. Idempotent — safe to call when nothing is playing."""
        if not self._initialised:
            return
        import pygame

        with self._lock:
            try:
                pygame.mixer.music.stop()
            except pygame.error as exc:
                logger.debug("mixer.stop failed: %s", exc)

    def is_playing(self) -> bool:
        """True if a clip is currently being played."""
        if not self._initialised:
            return False
        import pygame

        try:
            return bool(pygame.mixer.music.get_busy())
        except pygame.error:
            return False

    def _shutdown(self) -> None:
        """atexit hook: stop playback and tear down the mixer."""
        if not self._initialised:
            return
        try:
            import pygame

            pygame.mixer.music.stop()
            pygame.mixer.quit()
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            logger.debug("mixer shutdown failed: %s", exc)
        finally:
            self._initialised = False


_player: Optional[AudioPlayer] = None
_singleton_lock = threading.Lock()


def get_player() -> AudioPlayer:
    """Return the shared module-level AudioPlayer instance."""
    global _player
    if _player is None:
        with _singleton_lock:
            if _player is None:
                _player = AudioPlayer()
    return _player
