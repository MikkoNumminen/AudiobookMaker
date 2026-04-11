"""Unit tests for the TTS engine base class and registry."""

from __future__ import annotations

import pytest

from src.tts_base import (
    EngineStatus,
    TTSEngine,
    Voice,
    get_engine,
    list_engines,
    register_engine,
    registered_ids,
    _REGISTRY,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_registry():
    """Isolate each test from the real engine registry."""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# Voice / EngineStatus dataclasses
# ---------------------------------------------------------------------------


class TestVoice:
    def test_voice_has_required_fields(self) -> None:
        v = Voice(id="x", display_name="X", language="fi")
        assert v.id == "x"
        assert v.gender == ""

    def test_voice_is_hashable(self) -> None:
        # Voices are frozen so they can go into sets/dicts keyed by id.
        v1 = Voice(id="x", display_name="X", language="fi")
        v2 = Voice(id="x", display_name="X", language="fi")
        assert {v1, v2} == {v1}


class TestEngineStatus:
    def test_defaults(self) -> None:
        s = EngineStatus(available=True)
        assert s.reason == ""
        assert s.needs_download is False

    def test_not_available_with_reason(self) -> None:
        s = EngineStatus(available=False, reason="Install required: pip install foo")
        assert not s.available
        assert "foo" in s.reason


# ---------------------------------------------------------------------------
# Dummy engine used for contract tests
# ---------------------------------------------------------------------------


class _DummyEngine(TTSEngine):
    id = "dummy"
    display_name = "Dummy"
    description = "For tests"
    requires_gpu = False

    def check_status(self) -> EngineStatus:
        return EngineStatus(available=True)

    def list_voices(self, language: str) -> list[Voice]:
        return [Voice(id="dummy-1", display_name="Dummy 1", language=language)]

    def default_voice(self, language: str) -> str | None:
        return "dummy-1"

    def synthesize(
        self,
        text: str,
        output_path: str,
        voice_id: str,
        language: str,
        progress_cb=None,
        reference_audio=None,
    ) -> None:
        pass


# ---------------------------------------------------------------------------
# Abstract class contract
# ---------------------------------------------------------------------------


class TestAbstractContract:
    def test_cannot_instantiate_base_class(self) -> None:
        with pytest.raises(TypeError):
            TTSEngine()  # type: ignore[abstract]

    def test_subclass_must_implement_synthesize(self) -> None:
        class Incomplete(TTSEngine):
            id = "incomplete"
            display_name = "Incomplete"
            description = "x"

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_concrete_subclass_is_instantiable(self) -> None:
        engine = _DummyEngine()
        assert engine.check_status().available
        assert engine.list_voices("fi")[0].id == "dummy-1"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_register_and_lookup(self, clean_registry) -> None:
        register_engine(_DummyEngine)
        assert "dummy" in registered_ids()
        engine = get_engine("dummy")
        assert isinstance(engine, _DummyEngine)

    def test_register_returns_same_class(self, clean_registry) -> None:
        returned = register_engine(_DummyEngine)
        assert returned is _DummyEngine

    def test_duplicate_registration_rejected(self, clean_registry) -> None:
        register_engine(_DummyEngine)
        with pytest.raises(ValueError, match="already registered"):
            register_engine(_DummyEngine)

    def test_missing_id_rejected(self, clean_registry) -> None:
        class NoId(_DummyEngine):
            id = ""

        with pytest.raises(ValueError, match="non-empty 'id'"):
            register_engine(NoId)

    def test_get_engine_unknown_returns_none(self, clean_registry) -> None:
        assert get_engine("nope") is None

    def test_list_engines_returns_fresh_instances(self, clean_registry) -> None:
        register_engine(_DummyEngine)
        engines = list_engines()
        assert len(engines) == 1
        assert isinstance(engines[0], _DummyEngine)
        # A second call returns a different instance
        assert list_engines()[0] is not engines[0]

    def test_list_engines_preserves_registration_order(self, clean_registry) -> None:
        class B(_DummyEngine):
            id = "b"

        class C(_DummyEngine):
            id = "c"

        register_engine(_DummyEngine)  # id='dummy'
        register_engine(B)
        register_engine(C)
        ids = [e.id for e in list_engines()]
        assert ids == ["dummy", "b", "c"]
