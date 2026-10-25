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
    """The one chatterbox import install() needs, plus a fake torch."""
    analyzer = SimpleNamespace(LLAMA_ALIGNED_HEADS=[(12, 15), (13, 11), (9, 2)])
    names = [
        "chatterbox", "chatterbox.models", "chatterbox.models.t3",
        "chatterbox.models.t3.inference",
    ]
    modules = {name: MagicMock() for name in names}
    modules["chatterbox.models.t3.inference.alignment_stream_analyzer"] = analyzer
    torch = MagicMock()
    torch.get_rng_state.return_value = "cpu rng before"
    torch.cuda.get_rng_state.return_value = "cuda rng before"
    modules["torch"] = torch
    with mock.patch.dict(sys.modules, modules):
        yield torch


@pytest.fixture()
def marker(tmp_path, monkeypatch):
    path = tmp_path / fd.MARKER_NAME
    monkeypatch.setattr(fd, "marker_path", lambda: path)
    monkeypatch.delenv(fd.ENV_FLAG, raising=False)
    return path


def _engine():
    t3 = _t3()
    t3.inference = MagicMock(return_value="library tokens")
    return SimpleNamespace(t3=t3)


def _decoder(self_test=None):
    decoder = MagicMock()
    if isinstance(self_test, Exception):
        decoder.self_test.side_effect = self_test
    else:
        decoder.self_test.return_value = self_test
    return decoder


def test_off_switch_leaves_the_library_loop(monkeypatch) -> None:
    monkeypatch.setenv(fd.ENV_FLAG, "0")
    engine = _engine()
    original = engine.t3.inference
    lines = []
    assert fd.install(engine, log=lines.append) is False
    assert engine.t3.inference is original
    assert "off" in lines[0]


def test_unsupported_model_leaves_the_library_loop(marker) -> None:
    engine = _engine()
    engine.t3.device = SimpleNamespace(type="cpu")
    original = engine.t3.inference
    lines = []
    assert fd.install(engine, log=lines.append) is False
    assert engine.t3.inference is original
    assert "standard decoder" in lines[0]


def test_fast_path_serves_inference(marker, chatterbox_heads) -> None:
    engine = _engine()
    original = engine.t3.inference
    lines = []
    decoder = _decoder()
    with mock.patch.object(fd, "FastT3Decoder", return_value=decoder) as decoder_cls, \
         mock.patch.object(fd, "fast_inference", return_value="fast tokens") as fast:
        assert fd.install(engine, log=lines.append) is True
        assert engine.t3.inference(text_tokens="tt", max_new_tokens=1000) == "fast tokens"
    decoder_cls.assert_called_once_with(engine.t3, [(12, 15), (13, 11), (9, 2)])
    decoder.self_test.assert_called_once()
    assert fast.call_args.kwargs == {"text_tokens": "tt", "max_new_tokens": 1000}
    original.assert_not_called()
    assert engine.t3.inference.fast_decode_state == {"enabled": True}
    assert engine.t3.inference.fast_decoder is decoder
    assert lines == ["[tts] fast decode on (static cache + CUDA graphs, self-test passed)"]


def test_failure_falls_back_for_the_rest_of_the_run(marker, chatterbox_heads) -> None:
    torch = chatterbox_heads
    engine = _engine()
    original = engine.t3.inference
    lines = []
    with mock.patch.object(fd, "FastT3Decoder", return_value=_decoder()), \
         mock.patch.object(fd, "cuda_rng_usable", return_value=True), \
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
    # The random state the failed attempt consumed is rewound first.
    torch.set_rng_state.assert_called_once_with("cpu rng before")
    torch.cuda.set_rng_state.assert_called_once_with("cuda rng before")
    assert engine.t3.inference.fast_decode_state == {"enabled": False}
    assert lines[-1].startswith("[warn] fast decode failed (RuntimeError: graph capture failed)")
    assert not marker.exists()


def test_failure_that_breaks_cuda_stops_the_run_and_remembers(marker, chatterbox_heads) -> None:
    engine = _engine()
    original = engine.t3.inference
    lines = []
    with mock.patch.object(fd, "FastT3Decoder", return_value=_decoder()), \
         mock.patch.object(fd, "cuda_rng_usable", return_value=False), \
         mock.patch.object(fd, "fast_inference",
                           side_effect=RuntimeError("operation failed during capture")):
        fd.install(engine, log=lines.append)
        with pytest.raises(fd.FastDecodeBrokeCuda):
            engine.t3.inference(text_tokens="a")
    # Falling back in this process would only fail again in multinomial.
    original.assert_not_called()
    assert fd.marker_disables(marker)
    assert "Press Convert again" in lines[-1] and "finished parts are kept" in lines[-1]


def test_marker_turns_it_off_on_the_next_run(marker) -> None:
    assert fd.write_marker(marker, "RuntimeError: capture")
    engine = _engine()
    original = engine.t3.inference
    lines = []
    assert fd.install(engine, log=lines.append) is False
    assert engine.t3.inference is original
    assert "failed on this machine before" in lines[0]


def test_marker_from_an_older_version_is_ignored(marker) -> None:
    marker.write_text("version=0\nold failure\n", encoding="utf-8")
    assert not fd.marker_disables(marker)
    assert not fd.marker_disables(marker.with_name("missing.txt"))


def test_self_test_mismatch_keeps_the_library_loop(marker, chatterbox_heads) -> None:
    engine = _engine()
    original = engine.t3.inference
    lines = []
    decoder = _decoder(self_test="graph replay differs from the plain step (max diff 0.5)")
    with mock.patch.object(fd, "FastT3Decoder", return_value=decoder):
        assert fd.install(engine, log=lines.append) is False
    assert engine.t3.inference is original
    decoder.release.assert_called()
    assert "self-test failed (graph replay differs" in lines[-1]


def test_self_test_error_with_healthy_cuda_keeps_the_library_loop(marker, chatterbox_heads) -> None:
    engine = _engine()
    original = engine.t3.inference
    lines = []
    decoder = _decoder(self_test=fd.FastDecodeUnavailable("needs 0.75 GB"))
    with mock.patch.object(fd, "FastT3Decoder", return_value=decoder), \
         mock.patch.object(fd, "cuda_rng_usable", return_value=True):
        assert fd.install(engine, log=lines.append) is False
    assert engine.t3.inference is original
    assert "needs 0.75 GB" in lines[-1]
    assert not marker.exists()


def test_self_test_that_breaks_cuda_stops_before_any_audio(marker, chatterbox_heads) -> None:
    engine = _engine()
    decoder = _decoder(self_test=RuntimeError("operation failed during capture"))
    with mock.patch.object(fd, "FastT3Decoder", return_value=decoder), \
         mock.patch.object(fd, "cuda_rng_usable", return_value=False):
        with pytest.raises(fd.FastDecodeBrokeCuda):
            fd.install(engine, log=lambda _line: None)
    assert fd.marker_disables(marker)


def test_unwritable_marker_says_how_to_turn_it_off(tmp_path, monkeypatch, chatterbox_heads) -> None:
    monkeypatch.setattr(fd, "marker_path", lambda: tmp_path / "no-such-dir" / fd.MARKER_NAME)
    lines = []
    with mock.patch.object(fd, "cuda_rng_usable", return_value=False):
        with pytest.raises(fd.FastDecodeBrokeCuda):
            fd._after_failure(chatterbox_heads, MagicMock(), RuntimeError("x"), lines.append)
    assert f"{fd.ENV_FLAG}=0" in lines[-1]
