"""Shared pytest fixtures for the AudiobookMaker test suite."""

from __future__ import annotations

import pytest

from src.tts_base import _REGISTRY


@pytest.fixture
def clean_registry():
    """Isolate each test from the real engine registry."""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)
