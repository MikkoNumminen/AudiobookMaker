"""Fast GPU decode loop for the Chatterbox T3 model.

Why this exists
---------------
Chatterbox turns text into speech tokens one token at a time (about 25
tokens per second of audio). The library's own loop in
``chatterbox.models.t3.t3.T3.inference`` runs every token through the full
Hugging Face ``LlamaModel`` call stack with ``output_attentions=True`` and
``output_hidden_states=True``. That stack launches several hundred tiny GPU
kernels from Python per token, so the processor, not the graphics card, sets
the pace: on an RTX 3080 Ti the card sat 35-40 % busy at ~24 tokens/s. A
faster card cannot help when it is waiting for Python, which is why a new
RTX 50-series card felt no faster than the GTX 1080 it replaced.

What this does instead
----------------------
The first pass over the conditioning + text (the "prefill") still goes
through the library unchanged, so the alignment analyzer sees exactly what it
always saw. Every later token runs through :class:`FastT3Decoder`, which does
the same per-layer math as the eager Hugging Face path (RMSNorm, rotary
embedding, attention, MLP, final norm, speech head, all in the model's own
dtype) but:

* keeps keys/values in one preallocated cache instead of a growing
  ``DynamicCache``, so every tensor has a fixed shape, and
* records the whole 30-layer step once as a CUDA graph per attention width
  (128-token buckets) and replays it, so a token costs one launch instead
  of hundreds.

The sampling loop around it (CFG, alignment analyzer, repetition penalty,
temperature, min-p, top-p, multinomial) is a line-for-line copy of the
library's, so generation semantics do not change.

Safety
------
:func:`install` wraps ``engine.t3.inference`` and first runs a self-test: it
records one graph with the real model and checks the replay against the same
step run normally, before any audio is made.

Failures come in two kinds, and they are handled differently:

* Most failures (an unexpected model layout, a Python error, running out of
  VRAM) leave CUDA usable. The fast path is switched off for the rest of the
  run, the random state is put back, and the chunk is generated again with
  the library's own loop.
* A CUDA error *during* graph capture leaves torch's CUDA random generator
  convinced a capture is still running; every later ``torch.multinomial`` in
  that process then fails ("Offset increment outside graph capture"), and
  nothing short of a new process clears it (tested on torch 2.6 and 2.8). The
  runner cannot restart itself safely (the GUI stops it by process id, so a
  child would outlive a Stop), so the fast path writes a marker file into the
  engine venv that turns it off on this machine, and the run stops with a
  message to press Convert again. Finished chunks are kept, and the next run
  uses the library loop. A new FAST_DECODE_VERSION re-enables it.

Set ``ABM_CHATTERBOX_FAST_DECODE=0`` to turn it off entirely.

This module is imported only by the Chatterbox runner, inside the engine
venv; torch and chatterbox are imported lazily so the module itself stays
importable (and testable) without them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional

# Attention width granularity for the CUDA graphs. Attention reads the whole
# cache window each step, so a tight window saves memory bandwidth; a coarse
# one means fewer graphs to record. 128 keeps the window within ~10 % of the
# live length for typical chunks and caps a 1000-token chunk at ~12 graphs.
WIDTH_BUCKET = 128

# Cache capacity granularity. Capacity only ever grows, so rounding up keeps
# reallocations (which discard every recorded graph) rare.
CAPACITY_BUCKET = 256

ENV_FLAG = "ABM_CHATTERBOX_FAST_DECODE"

# Bump when a change to this module could fix a machine that wrote the
# disable marker; a marker from an older version is then ignored.
FAST_DECODE_VERSION = "1"

MARKER_NAME = "abm_fast_decode_disabled.txt"

# VRAM that must stay free after the cache is allocated. S3Gen (speech
# tokens -> audio) runs after T3 with the cache still resident; its peak was
# ~0.5 GB above the loaded model for a 20 s chunk on an RTX 3080 Ti, so 1 GB
# leaves margin for longer chunks and other programs.
VRAM_RESERVE_BYTES = 1 << 30

# Capacity the self-test allocates: a typical chunk's prefill (~150-450
# positions) plus the library's 1000 new tokens, so the first real chunk
# usually reuses the self-test's cache and graph instead of reallocating.
SELF_TEST_CAPACITY = 1280


class FastDecodeUnavailable(RuntimeError):
    """The fast path declined to run (e.g. not enough VRAM); CUDA is fine."""


class FastDecodeBrokeCuda(RuntimeError):
    """A failed graph capture left CUDA unusable for the rest of this process."""


def fast_decode_enabled(environ: Optional[dict] = None) -> bool:
    """False only when the user explicitly turned the fast path off."""
    value = (environ if environ is not None else os.environ).get(ENV_FLAG, "")
    return value.strip().lower() not in ("0", "false", "no", "off")


def marker_path() -> Path:
    """Disable marker inside the engine venv the runner is executing in."""
    return Path(sys.prefix) / MARKER_NAME


def marker_disables(path: Path) -> bool:
    """True when ``path`` holds a disable marker written by this version."""
    try:
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError, UnicodeDecodeError):
        return False
    return first_line.strip() == f"version={FAST_DECODE_VERSION}"


def write_marker(path: Path, reason: str) -> bool:
    try:
        path.write_text(f"version={FAST_DECODE_VERSION}\n{reason}\n", encoding="utf-8")
    except OSError:
        return False
    return True


def cuda_rng_usable(torch) -> bool:
    """Can this process still sample on the GPU? False after a broken capture."""
    try:
        probs = torch.ones((1, 2), device=torch.device("cuda", torch.cuda.current_device()))
        torch.multinomial(probs, 1)
        torch.cuda.synchronize()
    except Exception:  # RuntimeError on 2.6, AcceleratorError on 2.8
        return False
    return True


def width_for(keys: int, capacity: int) -> int:
    """Attention window (multiple of WIDTH_BUCKET) that covers ``keys`` keys."""
    width = -(-keys // WIDTH_BUCKET) * WIDTH_BUCKET
    return min(width, capacity)


def capacity_for(needed: int) -> int:
    """Cache length for ``needed`` positions, rounded up to CAPACITY_BUCKET."""
    return -(-needed // CAPACITY_BUCKET) * CAPACITY_BUCKET


def unsupported_reason(t3) -> Optional[str]:
    """Why the fast path cannot run this T3 model, or None if it can."""
    if getattr(t3, "is_gpt", False):
        return "GPT-2 backbone"
    tfmr = getattr(t3, "tfmr", None)
    layers = getattr(tfmr, "layers", None)
    if layers is None or not hasattr(tfmr, "norm") or not hasattr(tfmr, "rotary_emb"):
        return "transformer layout not recognised"
    attn = layers[0].self_attn
    for name in ("q_proj", "k_proj", "v_proj", "o_proj", "head_dim", "scaling"):
        if not hasattr(attn, name):
            return f"attention layer has no {name}"
    if getattr(attn, "num_key_value_groups", 1) != 1:
        return "grouped-query attention"
    if getattr(t3.hp, "input_pos_emb", None) != "learned":
        return "speech position embedding is not learned"
    device = getattr(t3, "device", None)
    if getattr(device, "type", None) != "cuda":
        return "not on a CUDA device"
    return None


def _cache_layers(past):
    """Yield (keys, values) per layer from a Hugging Face cache object."""
    layers = getattr(past, "layers", None)
    if layers is not None:
        for layer in layers:
            yield layer.keys, layer.values
        return
    if hasattr(past, "key_cache"):
        yield from zip(past.key_cache, past.value_cache)
        return
    for keys, values in past:
        yield keys, values


class FastT3Decoder:
    """Static-cache, CUDA-graph decode step for one T3 model.

    One instance lives for the whole run. The cache and the recorded graphs
    are reused across chunks; only the first ``prefill_len`` positions are
    refilled per chunk, and everything past the current position is masked.
    """

    def __init__(self, t3, aligned_heads):
        import torch
        from transformers.models.llama.modeling_llama import apply_rotary_pos_emb

        self.torch = torch
        self.apply_rope = apply_rotary_pos_emb
        self.t3 = t3
        self.tfmr = t3.tfmr
        self.layers = list(self.tfmr.layers)
        attn = self.layers[0].self_attn
        self.n_heads = attn.q_proj.out_features // attn.head_dim
        self.head_dim = attn.head_dim
        self.scaling = attn.scaling
        self.device = t3.device
        self.dtype = t3.speech_head.weight.dtype
        self.hidden = t3.speech_head.in_features
        self.vocab = t3.speech_head.out_features
        # (layer_idx, head_idx) pairs whose attention rows the alignment
        # analyzer reads; same list the analyzer hooks in the prefill.
        self.aligned = [(int(l), int(h)) for l, h in aligned_heads]
        self.aligned_by_layer = {}
        for slot, (layer_idx, head_idx) in enumerate(self.aligned):
            self.aligned_by_layer.setdefault(layer_idx, []).append((slot, head_idx))

        self.capacity = 0
        self.graphs: dict[int, object] = {}
        self.pool = None
        # Static inputs. Replays read these, so they are updated in place.
        self.tok = torch.zeros((1, 1), dtype=torch.long, device=self.device)
        self.speech_pos = torch.zeros((1, 1), dtype=torch.long, device=self.device)
        self.pos = torch.zeros((1,), dtype=torch.long, device=self.device)
        # Static outputs, allocated outside the graph pool so they survive
        # any graph's replay.
        self.logits = torch.zeros((2, 1, self.vocab), dtype=self.dtype, device=self.device)
        self.k_cache = None
        self.v_cache = None
        self.aligned_rows = None
        self.cos = None
        self.sin = None

    # -- cache management -------------------------------------------------

    def ensure_capacity(self, needed: int) -> None:
        torch = self.torch
        if needed <= self.capacity:
            return
        capacity = capacity_for(needed)
        # Drop the old graphs and cache before allocating the bigger one so
        # the two never coexist in VRAM.
        self.release()
        shape = (len(self.layers), 2, self.n_heads, capacity, self.head_dim)
        element = torch.empty((), dtype=self.dtype).element_size()
        cache_bytes = 2 * element
        for dim in shape:
            cache_bytes *= dim
        free, _total = torch.cuda.mem_get_info()
        # Blocks torch has cached but not handed out are free for this too.
        free += torch.cuda.memory_reserved() - torch.cuda.memory_allocated()
        if free - cache_bytes < VRAM_RESERVE_BYTES:
            raise FastDecodeUnavailable(
                f"needs {cache_bytes / 2**30:.2f} GB of VRAM for its cache plus "
                f"{VRAM_RESERVE_BYTES / 2**30:.0f} GB to spare, "
                f"{free / 2**30:.2f} GB free"
            )
        self.k_cache = torch.zeros(shape, dtype=self.dtype, device=self.device)
        self.v_cache = torch.zeros(shape, dtype=self.dtype, device=self.device)
        self.aligned_rows = torch.zeros(
            (max(len(self.aligned), 1), capacity), dtype=self.dtype, device=self.device
        )
        positions = torch.arange(capacity, device=self.device)[None, :]
        probe = torch.zeros((1, 1, self.hidden), dtype=self.dtype, device=self.device)
        cos, sin = self.tfmr.rotary_emb(probe, positions)
        self.cos, self.sin = cos[0].contiguous(), sin[0].contiguous()  # (capacity, head_dim)
        self.capacity = capacity

    def release(self) -> None:
        """Free the cache and every recorded graph."""
        self.graphs.clear()
        self.pool = None
        self.k_cache = self.v_cache = self.aligned_rows = None
        self.cos = self.sin = None
        self.capacity = 0
        self.torch.cuda.empty_cache()

    def self_test(self) -> Optional[str]:
        """Record one graph with the real weights and check its replay.

        Returns None when the replay matches the same step run without a
        graph, else what differed. Runs before any audio, so a GPU or driver
        that cannot record this step is found before the first chunk. The
        dummy step writes cache slot 0, which every chunk's prefill
        overwrites.
        """
        torch = self.torch
        self.ensure_capacity(SELF_TEST_CAPACITY)
        self.tok.fill_(int(self.t3.hp.start_speech_token))
        self.pos.fill_(0)
        self.speech_pos.fill_(1)
        self.k_cache.zero_()
        self.v_cache.zero_()
        self._step_math(WIDTH_BUCKET)
        expected = self.logits.clone()
        self._graph_for(WIDTH_BUCKET).replay()
        torch.cuda.synchronize()
        diff = (self.logits - expected).abs().max().item()
        scale = expected.abs().max().item() or 1.0
        if not diff <= 1e-4 * scale:
            return f"graph replay differs from the plain step (max diff {diff:.3g})"
        return None

    def load_prefill(self, past, prefill_len: int) -> None:
        """Copy the library's prefill cache into the static cache."""
        for layer_idx, (keys, values) in enumerate(_cache_layers(past)):
            self.k_cache[layer_idx, :, :, :prefill_len].copy_(keys)
            self.v_cache[layer_idx, :, :, :prefill_len].copy_(values)

    # -- the decode step ----------------------------------------------------

    def _step_math(self, width: int) -> None:
        """One token through every layer, attending over ``width`` keys."""
        torch = self.torch
        t3 = self.t3
        x = t3.speech_emb(self.tok) + t3.speech_pos_emb.emb(self.speech_pos)  # (1, 1, D)
        x = torch.cat([x, x])  # (2, 1, D): conditional + unconditional (CFG)
        cos = self.cos.index_select(0, self.pos)[None]  # (1, 1, head_dim)
        sin = self.sin.index_select(0, self.pos)[None]
        keep = torch.arange(width, device=self.device) <= self.pos  # (width,)
        masked = torch.finfo(self.dtype).min
        for layer_idx, layer in enumerate(self.layers):
            attn = layer.self_attn
            h = layer.input_layernorm(x)
            q = attn.q_proj(h).view(2, 1, self.n_heads, self.head_dim).transpose(1, 2)
            k = attn.k_proj(h).view(2, 1, self.n_heads, self.head_dim).transpose(1, 2)
            v = attn.v_proj(h).view(2, 1, self.n_heads, self.head_dim).transpose(1, 2)
            q, k = self.apply_rope(q, k, cos, sin)
            k_layer = self.k_cache[layer_idx]
            v_layer = self.v_cache[layer_idx]
            k_layer.index_copy_(2, self.pos, k)
            v_layer.index_copy_(2, self.pos, v)
            # Same arithmetic as transformers' eager_attention_forward; the
            # library forces eager attention for these steps too.
            scores = torch.matmul(q, k_layer[:, :, :width].transpose(2, 3)) * self.scaling
            scores = torch.where(keep, scores, masked)
            weights = torch.softmax(scores, dim=-1, dtype=torch.float32).to(q.dtype)
            for slot, head_idx in self.aligned_by_layer.get(layer_idx, ()):
                self.aligned_rows[slot, :width].copy_(weights[0, head_idx, 0])
            out = torch.matmul(weights, v_layer[:, :, :width])
            out = out.transpose(1, 2).reshape(2, 1, -1)
            x = x + attn.o_proj(out)
            x = x + layer.mlp(layer.post_attention_layernorm(x))
        x = self.tfmr.norm(x)
        self.logits.copy_(t3.speech_head(x))

    def _graph_for(self, width: int):
        torch = self.torch
        graph = self.graphs.get(width)
        if graph is not None:
            return graph
        # Warm up on a side stream (cuBLAS picks its kernels here), then
        # record. The warm-up really runs the step with the current inputs;
        # the step is idempotent (it rewrites the same cache slot), so the
        # replay that follows produces the same result.
        stream = torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(2):
                self._step_math(width)
        torch.cuda.current_stream().wait_stream(stream)
        if self.pool is None:
            self.pool = torch.cuda.graph_pool_handle()
        graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(graph, pool=self.pool):
            self._step_math(width)
        self.graphs[width] = graph
        return graph

    def step(self, token, position: int, speech_position: int) -> None:
        """Feed ``token`` at cache ``position``; fills ``self.logits``."""
        self.tok.copy_(token.view(1, 1))
        self.pos.fill_(position)
        self.speech_pos.fill_(speech_position)
        self._graph_for(width_for(position + 1, self.capacity)).replay()


def fast_inference(
    t3,
    decoder: FastT3Decoder,
    *,
    t3_cond,
    text_tokens,
    initial_speech_tokens=None,
    prepend_prompt_speech_tokens=None,
    num_return_sequences=1,
    max_new_tokens=None,
    stop_on_eos=True,
    do_sample=True,
    temperature=0.8,
    top_p=0.95,
    min_p=0.05,
    length_penalty=1.0,
    repetition_penalty=1.2,
    cfg_weight=0.5,
):
    """Drop-in for ``T3.inference`` (chatterbox-tts 0.1.7) using the fast step.

    Everything except the per-token forward pass is copied from the library
    so sampling, CFG and the alignment analyzer behave exactly as before.
    """
    import torch
    from chatterbox.models.t3.inference.alignment_stream_analyzer import (
        AlignmentStreamAnalyzer,
    )
    from chatterbox.models.t3.inference.t3_hf_backend import T3HuggingfaceBackend
    from chatterbox.models.t3.t3 import _ensure_BOT_EOT
    from transformers.generation.logits_process import (
        MinPLogitsWarper,
        RepetitionPenaltyLogitsProcessor,
        TopPLogitsWarper,
    )

    assert prepend_prompt_speech_tokens is None, "not implemented"
    _ensure_BOT_EOT(text_tokens, t3.hp)
    text_tokens = torch.atleast_2d(text_tokens).to(dtype=torch.long, device=t3.device)
    if text_tokens.size(0) != 2:
        raise RuntimeError("fast decode expects the CFG pair (batch of 2)")
    if initial_speech_tokens is None:
        initial_speech_tokens = t3.hp.start_speech_token * torch.ones_like(text_tokens[:, :1])

    embeds, len_cond = t3.prepare_input_embeds(
        t3_cond=t3_cond,
        text_tokens=text_tokens,
        speech_tokens=initial_speech_tokens,
        cfg_weight=cfg_weight,
    )

    alignment_stream_analyzer = None
    try:
        if t3.hp.is_multilingual:
            alignment_stream_analyzer = AlignmentStreamAnalyzer(
                t3.tfmr,
                None,
                text_tokens_slice=(len_cond, len_cond + text_tokens.size(-1)),
                alignment_layer_idx=9,
                eos_idx=t3.hp.stop_speech_token,
            )
        t3.patched_model = T3HuggingfaceBackend(
            config=t3.cfg,
            llama=t3.tfmr,
            speech_enc=t3.speech_emb,
            speech_head=t3.speech_head,
            alignment_stream_analyzer=alignment_stream_analyzer,
        )
        t3.compiled = True

        device = embeds.device
        bos_token = torch.tensor([[t3.hp.start_speech_token]], dtype=torch.long, device=device)
        bos_embed = t3.speech_emb(bos_token)
        bos_embed = bos_embed + t3.speech_pos_emb.get_fixed_embedding(0)
        bos_embed = torch.cat([bos_embed, bos_embed])
        inputs_embeds = torch.cat([embeds, bos_embed], dim=1)
        prefill_len = inputs_embeds.size(1)

        generated_ids = bos_token.clone()
        predicted = []
        min_p_warper = MinPLogitsWarper(min_p=min_p)
        top_p_warper = TopPLogitsWarper(top_p=top_p)
        repetition_penalty_processor = RepetitionPenaltyLogitsProcessor(
            penalty=float(repetition_penalty)
        )

        # Prefill through the library, unchanged: the analyzer's hooks see
        # the full attention maps exactly as before.
        output = t3.patched_model(
            inputs_embeds=inputs_embeds,
            past_key_values=None,
            use_cache=True,
            output_attentions=True,
            output_hidden_states=True,
            return_dict=True,
        )
        # Keep only what the loop needs: the prefill output also holds every
        # layer's attention map and hidden states, which must not sit in VRAM
        # while the cache is allocated.
        past = output.past_key_values
        logits_source = output.logits[:, -1:, :]
        del output
        decoder.ensure_capacity(prefill_len + max_new_tokens)
        decoder.load_prefill(past, prefill_len)
        del past

        aligned_slots = len(decoder.aligned)
        for i in range(max_new_tokens):
            logits_step = logits_source[:, -1, :]
            cond = logits_step[0:1, :]
            uncond = logits_step[1:2, :]
            cfg = torch.as_tensor(cfg_weight, device=cond.device, dtype=cond.dtype)
            logits = cond + cfg * (cond - uncond)

            if alignment_stream_analyzer is not None:
                if logits.dim() == 1:
                    logits = logits.unsqueeze(0)
                last_token = generated_ids[0, -1].item() if len(generated_ids[0]) > 0 else None
                logits = alignment_stream_analyzer.step(logits, next_token=last_token)

            ids_for_proc = generated_ids[:1, ...]
            logits = repetition_penalty_processor(ids_for_proc, logits)
            if temperature != 1.0:
                logits = logits / temperature
            logits = min_p_warper(ids_for_proc, logits)
            logits = top_p_warper(ids_for_proc, logits)

            probs = torch.softmax(logits, dim=-1)
            next_token = torch.multinomial(probs, num_samples=1)

            predicted.append(next_token)
            generated_ids = torch.cat([generated_ids, next_token], dim=1)

            if next_token.view(-1) == t3.hp.stop_speech_token:
                break

            position = prefill_len + i
            decoder.step(next_token, position, i + 1)
            logits_source = decoder.logits
            if alignment_stream_analyzer is not None and aligned_slots:
                # The analyzer's hooks only fire inside the library's forward,
                # so hand it the same rows they would have captured:
                # attention of the newest token over every key so far.
                rows = decoder.aligned_rows[:aligned_slots, : position + 1].cpu()
                for slot in range(aligned_slots):
                    alignment_stream_analyzer.last_aligned_attns[slot] = rows[slot : slot + 1]

        return torch.cat(predicted, dim=1)
    finally:
        if alignment_stream_analyzer is not None and hasattr(alignment_stream_analyzer, "close"):
            alignment_stream_analyzer.close()


def _after_failure(torch, decoder: "FastT3Decoder", exc: Exception, log) -> None:
    """Free the fast path's VRAM and stop the run if CUDA is now unusable.

    Returns normally when CUDA still works, so the caller can fall back to
    the library loop in this process. Raises FastDecodeBrokeCuda otherwise,
    after recording the marker that keeps the next run off the fast path.
    """
    try:
        decoder.release()
    except Exception:  # a broken capture can make even empty_cache fail
        pass
    if cuda_rng_usable(torch):
        return
    path = marker_path()
    if write_marker(path, f"{type(exc).__name__}: {exc}"):
        log(
            "[error] The GPU fast mode failed on this graphics card and CUDA "
            "cannot be used again in this run. It is now switched off on this "
            "machine. Press Convert again: finished parts are kept, and the "
            "next run uses the standard mode."
        )
    else:
        log(
            f"[error] The GPU fast mode failed on this graphics card and CUDA "
            f"cannot be used again in this run. Could not save the setting "
            f"that switches it off ({path}); set {ENV_FLAG}=0 and press "
            f"Convert again. Finished parts are kept."
        )
    raise FastDecodeBrokeCuda(f"{type(exc).__name__}: {exc}") from exc


def install(engine, log: Callable[[str], None] = print) -> bool:
    """Route ``engine.t3.inference`` through the fast decoder when possible.

    Returns True when the fast path was installed. It is installed only after
    its self-test passes. The original method stays in place as the
    fallback; see the module docstring for how failures are handled. Raises
    FastDecodeBrokeCuda when a failure left CUDA unusable for this process.

    The wrapper exposes ``fast_decode_state`` (``{"enabled": bool}``) and
    ``fast_decoder`` so checks can confirm the fast path really ran.
    """
    if not fast_decode_enabled():
        log(f"[tts] fast decode off ({ENV_FLAG}=0)")
        return False
    if marker_disables(marker_path()):
        log(
            f"[tts] fast decode off: it failed on this machine before "
            f"({marker_path()}); using the standard decoder"
        )
        return False
    t3 = engine.t3
    reason = unsupported_reason(t3)
    if reason:
        log(f"[tts] fast decode unavailable: {reason}; using the standard decoder")
        return False
    try:
        from chatterbox.models.t3.inference.alignment_stream_analyzer import (
            LLAMA_ALIGNED_HEADS,
        )
    except ImportError as exc:
        log(f"[tts] fast decode unavailable: {exc}; using the standard decoder")
        return False
    import torch

    decoder = FastT3Decoder(t3, LLAMA_ALIGNED_HEADS)
    try:
        with torch.inference_mode():
            problem = decoder.self_test()
    except Exception as exc:
        _after_failure(torch, decoder, exc, log)
        problem = f"{type(exc).__name__}: {exc}"
    if problem:
        decoder.release()
        log(f"[tts] fast decode unavailable: self-test failed ({problem}); "
            f"using the standard decoder")
        return False

    original = t3.inference
    state = {"enabled": True}

    def inference(**kwargs):
        if state["enabled"]:
            cpu_rng = torch.get_rng_state()
            cuda_rng = torch.cuda.get_rng_state()
            try:
                return fast_inference(t3, decoder, **kwargs)
            except Exception as exc:
                state["enabled"] = False
                _after_failure(torch, decoder, exc, log)
                # The failed attempt may have drawn samples; rewind so the
                # library loop draws what it would have drawn on its own.
                torch.set_rng_state(cpu_rng)
                torch.cuda.set_rng_state(cuda_rng)
                log(
                    f"[warn] fast decode failed ({type(exc).__name__}: {exc}); "
                    f"using the standard decoder for the rest of this run"
                )
        return original(**kwargs)

    inference.fast_decode_state = state
    inference.fast_decoder = decoder
    t3.inference = inference
    log("[tts] fast decode on (static cache + CUDA graphs, self-test passed)")
    return True
