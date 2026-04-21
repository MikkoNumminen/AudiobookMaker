"""Tests for drag-and-drop path parsing + dispatch in :mod:`src.gui_unified`.

The drop payload parser (:meth:`UnifiedApp._parse_dnd_path_list`) is a
pure string function, so it runs headless without Tk. The dispatch
logic (:meth:`UnifiedApp._on_file_drop`) is exercised with a minimal
``types.SimpleNamespace`` event stand-in and a mock for the clone-voice
entry point, which keeps the test independent of the GUI widget tree.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ``src.gui_unified`` imports customtkinter at module top; the Chatterbox
# venv intentionally omits CTk, so skip this file cleanly there. The
# full dev suite (py -3) has CTk and runs everything.
pytest.importorskip("customtkinter")


def _parser():
    """Import the static parser without triggering CTk construction."""
    from src.gui_unified import UnifiedApp

    return UnifiedApp._parse_dnd_path_list


class TestParseDndPathList:
    def test_empty_returns_empty(self) -> None:
        assert _parser()("") == []
        assert _parser()("   ") == []

    def test_single_plain_path(self) -> None:
        assert _parser()("C:/sound.wav") == ["C:/sound.wav"]

    def test_single_braced_path(self) -> None:
        # Braces wrap paths with spaces — tkdnd's list format.
        assert _parser()("{C:/my folder/sound.wav}") == [
            "C:/my folder/sound.wav",
        ]

    def test_multiple_plain_paths(self) -> None:
        assert _parser()("a.wav b.mp3 c.flac") == [
            "a.wav", "b.mp3", "c.flac",
        ]

    def test_mixed_braced_and_plain(self) -> None:
        assert _parser()("{C:/one two.wav} plain.mp3 {C:/three four.ogg}") == [
            "C:/one two.wav", "plain.mp3", "C:/three four.ogg",
        ]

    def test_unterminated_brace_takes_remainder(self) -> None:
        # Defensive: tkdnd should never emit this, but we don't want the
        # parser to drop the payload entirely if it does.
        assert _parser()("{C:/unterminated") == ["C:/unterminated"]

    def test_leading_trailing_whitespace(self) -> None:
        assert _parser()("  C:/one.wav  ") == ["C:/one.wav"]


class TestOnFileDropDispatch:
    def _make_instance(self, clone_mock: MagicMock):
        """Return a throwaway object with just enough of ``UnifiedApp`` to
        dispatch a drop event, without calling ``CTk.__init__``."""
        from src.gui_unified import UnifiedApp

        inst = UnifiedApp.__new__(UnifiedApp)
        inst._clone_voice_from_file = clone_mock  # type: ignore[attr-defined]
        return inst

    def test_audio_drop_routes_to_clone_voice(self) -> None:
        from src.gui_unified import UnifiedApp

        clone = MagicMock()
        inst = self._make_instance(clone)
        evt = SimpleNamespace(data="C:/narration.wav")
        UnifiedApp._on_file_drop(inst, evt)
        clone.assert_called_once_with(path_override="C:/narration.wav")

    def test_non_audio_drop_is_ignored(self) -> None:
        from src.gui_unified import UnifiedApp

        clone = MagicMock()
        inst = self._make_instance(clone)
        evt = SimpleNamespace(data="C:/document.pdf")
        UnifiedApp._on_file_drop(inst, evt)
        clone.assert_not_called()

    def test_multi_drop_uses_first_audio_file(self) -> None:
        from src.gui_unified import UnifiedApp

        clone = MagicMock()
        inst = self._make_instance(clone)
        evt = SimpleNamespace(data="notes.pdf first.wav second.mp3")
        UnifiedApp._on_file_drop(inst, evt)
        # PDF skipped; first.wav wins.
        clone.assert_called_once_with(path_override="first.wav")

    def test_case_insensitive_extension_match(self) -> None:
        from src.gui_unified import UnifiedApp

        clone = MagicMock()
        inst = self._make_instance(clone)
        evt = SimpleNamespace(data="C:/LOUD.WAV")
        UnifiedApp._on_file_drop(inst, evt)
        clone.assert_called_once_with(path_override="C:/LOUD.WAV")

    def test_braced_path_with_spaces_routes_correctly(self) -> None:
        from src.gui_unified import UnifiedApp

        clone = MagicMock()
        inst = self._make_instance(clone)
        evt = SimpleNamespace(data="{C:/my folder/recording.m4a}")
        UnifiedApp._on_file_drop(inst, evt)
        clone.assert_called_once_with(
            path_override="C:/my folder/recording.m4a",
        )


class TestTryBindFileDropGracefulFallback:
    def test_importerror_is_swallowed(self, monkeypatch) -> None:
        """Missing tkinterdnd2 must never raise — drop is a bonus, not a
        requirement."""
        import builtins

        from src.gui_unified import UnifiedApp

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "tkinterdnd2":
                raise ImportError("simulated missing dep")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)

        inst = UnifiedApp.__new__(UnifiedApp)
        # Should return cleanly without raising or touching any Tk APIs.
        UnifiedApp._try_bind_file_drop(inst)
