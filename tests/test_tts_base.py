"""Unit tests for the TTS engine base class and registry."""

from __future__ import annotations

import pytest

from src.tts_base import (
    EngineStatus,
    TTSEngine,
    Voice,
    canonical_engine_id,
    get_engine,
    list_engines,
    register_alias,
    register_engine,
    registered_ids,
)


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
        voice_description=None,
        rate=None,
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

    def test_supported_languages_defaults_to_fi(self) -> None:
        # Back-compat: engines that don't override supported_languages()
        # should report Finnish-only, keeping legacy behaviour unchanged.
        assert _DummyEngine().supported_languages() == {"fi"}


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


# ---------------------------------------------------------------------------
# Alias map — back-compat for renamed engine ids
# ---------------------------------------------------------------------------


class TestEngineAliases:
    """``register_alias`` exists so an engine id can be renamed without
    breaking user configs, env vars, scripts, and release/update paths
    that still reference the old name.

    Production case: ``chatterbox_fi`` → ``chatterbox_grandmom`` on
    2026-05-17. The registry-level test for that specific alias is in
    ``test_chatterbox_grandmom_alias_back_compat`` below — it uses the
    real registry, not ``clean_registry``, so it exercises the actual
    alias registered by ``src/tts_chatterbox_bridge.py``.
    """

    def test_get_engine_resolves_alias(self, clean_registry) -> None:
        register_engine(_DummyEngine)  # id='dummy'
        register_alias("legacy_dummy", "dummy")
        engine = get_engine("legacy_dummy")
        assert isinstance(engine, _DummyEngine)
        # Both names return functionally-equivalent instances
        assert get_engine("dummy").__class__ is engine.__class__

    def test_canonical_engine_id_resolves_alias(self, clean_registry) -> None:
        register_engine(_DummyEngine)
        register_alias("legacy_dummy", "dummy")
        assert canonical_engine_id("legacy_dummy") == "dummy"

    def test_canonical_engine_id_passes_through_unknown(self, clean_registry) -> None:
        # Unknown ids are returned unchanged — callers that want to
        # detect "this id is bogus" should use get_engine() and check
        # for None, not the canonical-resolution helper.
        assert canonical_engine_id("never-registered") == "never-registered"

    def test_canonical_engine_id_passes_through_canonical(self, clean_registry) -> None:
        register_engine(_DummyEngine)
        assert canonical_engine_id("dummy") == "dummy"

    def test_aliases_are_not_listed_as_engines(self, clean_registry) -> None:
        register_engine(_DummyEngine)
        register_alias("legacy_dummy", "dummy")
        # The alias must not appear in registered_ids() or list_engines()
        # — those report canonical engines only.
        assert "legacy_dummy" not in registered_ids()
        engines = list_engines()
        assert len(engines) == 1
        assert engines[0].id == "dummy"

    def test_alias_to_unregistered_engine_rejected(self, clean_registry) -> None:
        with pytest.raises(ValueError, match="not a registered engine"):
            register_alias("legacy_dummy", "nowhere")

    def test_alias_overlapping_canonical_id_rejected(self, clean_registry) -> None:
        register_engine(_DummyEngine)  # id='dummy'

        class OtherEngine(_DummyEngine):
            id = "other"

        register_engine(OtherEngine)
        # Trying to alias 'dummy' (a canonical id) to 'other' would
        # shadow the real 'dummy' engine — refuse it.
        with pytest.raises(ValueError, match="already a canonical engine id"):
            register_alias("dummy", "other")

    def test_alias_self_loop_rejected(self, clean_registry) -> None:
        register_engine(_DummyEngine)
        with pytest.raises(ValueError, match="cannot map to itself"):
            register_alias("self", "self")

    def test_alias_empty_strings_rejected(self, clean_registry) -> None:
        register_engine(_DummyEngine)
        with pytest.raises(ValueError, match="non-empty"):
            register_alias("", "dummy")
        with pytest.raises(ValueError, match="non-empty"):
            register_alias("legacy_dummy", "")

    def test_registering_engine_with_alias_id_rejected(self, clean_registry) -> None:
        register_engine(_DummyEngine)
        register_alias("legacy_dummy", "dummy")

        class CollidingEngine(_DummyEngine):
            id = "legacy_dummy"

        with pytest.raises(ValueError, match="already registered as an alias"):
            register_engine(CollidingEngine)


def test_chatterbox_grandmom_alias_back_compat() -> None:
    """The production rename: ``chatterbox_fi`` is an alias of
    ``chatterbox_grandmom``. This test uses the real registry (no
    clean_registry fixture) to verify the alias is actually wired up
    by ``src.tts_chatterbox_bridge`` at import time.

    The contract this locks in: every existing user config /
    environment variable / CLI invocation that names ``chatterbox_fi``
    must continue to resolve to the same engine the GUI now surfaces
    as ``chatterbox_grandmom``.
    """
    assert canonical_engine_id("chatterbox_fi") == "chatterbox_grandmom"
    legacy = get_engine("chatterbox_fi")
    canonical = get_engine("chatterbox_grandmom")
    assert legacy is not None
    assert canonical is not None
    assert legacy.__class__ is canonical.__class__
    # The alias must not pollute registered_ids() — only the canonical
    # name surfaces in any "list all engines" UX.
    assert "chatterbox_fi" not in registered_ids()
    assert "chatterbox_grandmom" in registered_ids()
