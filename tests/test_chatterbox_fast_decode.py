"""Tests for src/chatterbox_fast_decode.py — the CUDA-graph token loop.

The numerical equivalence with the library's own loop needs a GPU and the
engine venv; scripts/check_fast_decode_equivalence.py checks that. These
tests cover what decides whether the fast path runs at all and what happens
when it fails, with torch and chatterbox mocked out.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock

import pytest

import src.chatterbox_fast_decode as fd


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "keys, capacity, width",
    [
        (1, 1280, 128),
        (128, 1280, 128),
        (129, 1280, 256),
        (1149, 1280, 1152),
        (1280, 1280, 1280),
        (1270, 1300, 1280),
        (1290, 1300, 1300),  # never wider than the cache
    ],
)
def test_width_for(keys, capacity, width) -> None:
    assert fd.width_for(keys, capacity) == width


@pytest.mark.parametrize("needed, capacity", [(1, 256), (256, 256), (257, 512), (1148, 1280)])
def test_capacity_for(needed, capacity) -> None:
    assert fd.capacity_for(needed) == capacity


# ---------------------------------------------------------------------------
# On/off switch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "false", "OFF", " no "])
def test_env_turns_it_off(value) -> None:
    assert not fd.fast_decode_enabled({fd.ENV_FLAG: value})


@pytest.mark.parametrize("value", [None, "", "1", "yes"])
def test_on_by_default(value) -> None:
    env = {} if value is None else {fd.ENV_FLAG: value}
    assert fd.fast_decode_enabled(env)


# ---------------------------------------------------------------------------
# Model compatibility
# ---------------------------------------------------------------------------


def _attn(**overrides):
    attrs = dict(q_proj=object(), k_proj=object(), v_proj=object(), o_proj=object(),
                 head_dim=64, scaling=0.125, num_key_value_groups=1)
    attrs.update(overrides)
    return SimpleNamespace(**attrs)


def _t3(*, device="cuda", attn=None, is_gpt=False, pos_emb="learned", layers=True):
    tfmr = SimpleNamespace(norm=object(), rotary_emb=object())
    if layers:
        tfmr.layers = [SimpleNamespace(self_attn=attn or _attn())]
    return SimpleNamespace(
        is_gpt=is_gpt,
        tfmr=tfmr,
        hp=SimpleNamespace(input_pos_emb=pos_emb),
        device=SimpleNamespace(type=device),
    )


def test_the_shipped_model_shape_is_supported() -> None:
    assert fd.unsupported_reason(_t3()) is None


@pytest.mark.parametrize(
    "t3, needle",
    [
        (_t3(device="cpu"), "CUDA"),
        (_t3(is_gpt=True), "GPT-2"),
        (_t3(layers=False), "layout"),
        (_t3(attn=_attn(num_key_value_groups=4)), "grouped-query"),
        (_t3(pos_emb="none"), "position embedding"),
    ],
)
def test_unsupported_models_are_named(t3, needle) -> None:
    assert needle in fd.unsupported_reason(t3)


def test_attention_without_projections_is_unsupported() -> None:
    attn = _attn()
    del attn.q_proj
    assert "q_proj" in fd.unsupported_reason(_t3(attn=attn))


# ---------------------------------------------------------------------------
# Cache hand-over from the library's prefill
# ---------------------------------------------------------------------------


def test_cache_layers_reads_every_cache_layout() -> None:
    pairs = [("k0", "v0"), ("k1", "v1")]
    new_style = SimpleNamespace(layers=[SimpleNamespace(keys=k, values=v) for k, v in pairs])
    old_style = SimpleNamespace(key_cache=["k0", "k1"], value_cache=["v0", "v1"])
    assert list(fd._cache_layers(new_style)) == pairs
    assert list(fd._cache_layers(old_style)) == pairs
    assert list(fd._cache_layers(pairs)) == pairs


# ---------------------------------------------------------------------------
# install(): routing and fallback
# ---------------------------------------------------------------------------


@pytest.fixture()
def chatterbox_heads():
    """The one chatterbox import install() needs, without chatterbox."""
    analyzer = SimpleNamespace(LLAMA_ALIGNED_HEADS=[(12, 15), (13, 11), (9, 2)])
    names = [
        "chatterbox", "chatterbox.models", "chatterbox.models.t3",
        "chatterbox.models.t3.inference",
    ]
    modules = {name: MagicMock() for name in names}
    modules["chatterbox.models.t3.inference.alignment_stream_analyzer"] = analyzer
    with mock.patch.dict(sys.modules, modules):
        yield


def _engine():
    t3 = _t3()
    t3.inference = MagicMock(return_value="library tokens")
    return SimpleNamespace(t3=t3)


def test_off_switch_leaves_the_library_loop(monkeypatch) -> None:
    monkeypatch.setenv(fd.ENV_FLAG, "0")
    engine = _engine()
    original = engine.t3.inference
    lines = []
    assert fd.install(engine, log=lines.append) is False
    assert engine.t3.inference is original
    assert "off" in lines[0]


def test_unsupported_model_leaves_the_library_loop(monkeypatch) -> None:
    monkeypatch.delenv(fd.ENV_FLAG, raising=False)
    engine = _engine()
    engine.t3.device = SimpleNamespace(type="cpu")
    original = engine.t3.inference
    lines = []
    assert fd.install(engine, log=lines.append) is False
    assert engine.t3.inference is original
    assert "standard decoder" in lines[0]


def test_fast_path_serves_inference(monkeypatch, chatterbox_heads) -> None:
    monkeypatch.delenv(fd.ENV_FLAG, raising=False)
    engine = _engine()
    original = engine.t3.inference
    lines = []
    with mock.patch.object(fd, "FastT3Decoder") as decoder_cls, \
         mock.patch.object(fd, "fast_inference", return_value="fast tokens") as fast:
        assert fd.install(engine, log=lines.append) is True
        assert engine.t3.inference(text_tokens="tt", max_new_tokens=1000) == "fast tokens"
    decoder_cls.assert_called_once_with(engine.t3, [(12, 15), (13, 11), (9, 2)])
    assert fast.call_args.kwargs == {"text_tokens": "tt", "max_new_tokens": 1000}
    original.assert_not_called()
    assert lines == ["[tts] fast decode on (static cache + CUDA graphs)"]


def test_failure_falls_back_for_the_rest_of_the_run(monkeypatch, chatterbox_heads) -> None:
    monkeypatch.delenv(fd.ENV_FLAG, raising=False)
    engine = _engine()
    original = engine.t3.inference
    lines = []
    with mock.patch.object(fd, "FastT3Decoder"), \
         mock.patch.object(fd, "fast_inference",
                           side_effect=RuntimeError("graph capture failed")) as fast:
        fd.install(engine, log=lines.append)
        # The failing chunk is regenerated by the library loop...
        assert engine.t3.inference(text_tokens="a") == "library tokens"
        # ...and later chunks go straight there.
        assert engine.t3.inference(text_tokens="b") == "library tokens"
    assert fast.call_count == 1
    assert [c.kwargs for c in original.call_args_list] == [
        {"text_tokens": "a"}, {"text_tokens": "b"},
    ]
    assert lines[-1].startswith("[warn] fast decode failed (RuntimeError: graph capture failed)")
