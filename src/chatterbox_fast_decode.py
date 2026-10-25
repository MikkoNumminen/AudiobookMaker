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
:func:`install` wraps ``engine.t3.inference``. Any failure in the fast path
(an unexpected model layout, a CUDA graph capture error, a library version
with a different loop) disables it for the rest of the run and the chunk is
generated again with the library's own loop. Set
``ABM_CHATTERBOX_FAST_DECODE=0`` to turn it off entirely.

This module is imported only by the Chatterbox runner, inside the engine
venv; torch and chatterbox are imported lazily so the module itself stays
importable (and testable) without them.
"""

from __future__ import annotations

import os
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


def fast_decode_enabled(environ: Optional[dict] = None) -> bool:
    """False only when the user explicitly turned the fast path off."""
    value = (environ if environ is not None else os.environ).get(ENV_FLAG, "")
    return value.strip().lower() not in ("0", "false", "no", "off")


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
        self.graphs.clear()
        self.pool = None
        self.k_cache = self.v_cache = self.aligned_rows = None
        torch.cuda.empty_cache()
        shape = (len(self.layers), 2, self.n_heads, capacity, self.head_dim)
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
        decoder.ensure_capacity(prefill_len + max_new_tokens)
        decoder.load_prefill(output.past_key_values, prefill_len)
        logits_source = output.logits
        del output

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


def install(engine, log: Callable[[str], None] = print) -> bool:
    """Route ``engine.t3.inference`` through the fast decoder when possible.

    Returns True when the fast path was installed. The original method stays
    in place as the fallback: the first failure logs one line, disables the
    fast path for the rest of the run, and regenerates the chunk with it.
    """
    if not fast_decode_enabled():
        log(f"[tts] fast decode off ({ENV_FLAG}=0)")
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

    original = t3.inference
    decoder = FastT3Decoder(t3, LLAMA_ALIGNED_HEADS)
    state = {"enabled": True}

    def inference(**kwargs):
        if state["enabled"]:
            try:
                return fast_inference(t3, decoder, **kwargs)
            except Exception as exc:  # any failure: fall back for the whole run
                state["enabled"] = False
                decoder.graphs.clear()
                decoder.k_cache = decoder.v_cache = None
                log(
                    f"[warn] fast decode failed ({type(exc).__name__}: {exc}); "
                    f"using the standard decoder for the rest of this run"
                )
        return original(**kwargs)

    t3.inference = inference
    log("[tts] fast decode on (static cache + CUDA graphs)")
    return True
