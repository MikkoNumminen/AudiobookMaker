"""Tests for the in-process audio player wrapper.

Mocks ``pygame.mixer`` so the suite stays green on CI workers without
an audio device.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_pygame(monkeypatch):
    """Install a fake ``pygame`` module so the player never touches real audio.

    Each test gets a fresh fake plus a fresh AudioPlayer instance so the
    module-level singleton from a previous test doesn't leak.
    """
    fake = types.ModuleType("pygame")
    fake.error = type("PygameError", (Exception,), {})
    fake.mixer = types.SimpleNamespace(
        init=MagicMock(),
        quit=MagicMock(),
        music=types.SimpleNamespace(
            load=MagicMock(),
            play=MagicMock(),
            stop=MagicMock(),
            get_busy=MagicMock(return_value=False),
        ),
    )
    monkeypatch.setitem(sys.modules, "pygame", fake)
    # Force a fresh AudioPlayer for the test (and clear singleton).
    from src import _audio_player

    monkeypatch.setattr(_audio_player, "_player", None)
    return fake


def _make_player(_audio_player_module):
    """Construct a fresh AudioPlayer (bypassing the singleton)."""
    return _audio_player_module.AudioPlayer()


def test_play_initialises_mixer_and_starts_clip(fake_pygame, tmp_path):
    from src import _audio_player

    clip = tmp_path / "x.mp3"
    clip.write_bytes(b"")

    player = _make_player(_audio_player)
    player.play(clip)

    fake_pygame.mixer.init.assert_called_once()
    fake_pygame.mixer.music.load.assert_called_once_with(str(clip))
    fake_pygame.mixer.music.play.assert_called_once()


def test_is_playing_reflects_mixer_busy_state(fake_pygame, tmp_path):
    from src import _audio_player

    clip = tmp_path / "x.mp3"
    clip.write_bytes(b"")

    player = _make_player(_audio_player)
    # Before play(), the mixer isn't initialised; is_playing must be False.
    assert player.is_playing() is False

    player.play(clip)
    fake_pygame.mixer.music.get_busy.return_value = True
    assert player.is_playing() is True

    fake_pygame.mixer.music.get_busy.return_value = False
    assert player.is_playing() is False


def test_stop_halts_playback_and_is_idempotent(fake_pygame, tmp_path):
    from src import _audio_player

    clip = tmp_path / "x.mp3"
    clip.write_bytes(b"")

    player = _make_player(_audio_player)

    # stop() before any play() is a no-op (no mixer init, no error).
    player.stop()
    fake_pygame.mixer.music.stop.assert_not_called()

    player.play(clip)
    fake_pygame.mixer.music.stop.reset_mock()

    player.stop()
    fake_pygame.mixer.music.stop.assert_called_once()

    # Second stop() must not raise; mixer.stop is fine to call again.
    player.stop()
    assert fake_pygame.mixer.music.stop.call_count == 2


def test_play_after_play_stops_previous_clip(fake_pygame, tmp_path):
    from src import _audio_player

    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"")
    second.write_bytes(b"")

    player = _make_player(_audio_player)
    player.play(first)
    fake_pygame.mixer.music.stop.reset_mock()

    player.play(second)

    # The implementation calls stop() before loading the new clip so two
    # overlapping plays never share the mixer.
    fake_pygame.mixer.music.stop.assert_called_once()
    fake_pygame.mixer.music.load.assert_called_with(str(second))


def test_atexit_hook_is_registered_on_first_play(fake_pygame, tmp_path, monkeypatch):
    """First play() registers an atexit cleanup; subsequent plays do not re-register."""
    from src import _audio_player

    register_calls: list = []

    def fake_register(func, *args, **kwargs):
        register_calls.append(func)
        return func

    monkeypatch.setattr(_audio_player.atexit, "register", fake_register)

    clip = tmp_path / "x.mp3"
    clip.write_bytes(b"")

    player = _make_player(_audio_player)
    player.play(clip)
    assert len(register_calls) == 1
    assert register_calls[0] == player._shutdown

    # A second play() must not register the hook again.
    player.play(clip)
    assert len(register_calls) == 1


def test_get_player_returns_singleton(fake_pygame):
    from src import _audio_player

    a = _audio_player.get_player()
    b = _audio_player.get_player()
    assert a is b
