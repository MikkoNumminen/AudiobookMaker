"""check_fast_decode_equivalence.py — prove the fast decoder is the same model.

``src/chatterbox_fast_decode.py`` replaces the Chatterbox library's per-token
loop with a static-cache CUDA-graph step. This script checks, on a real GPU
with the real Finnish model, that the replacement computes what the library
computes:

1. Teacher-forced: the same token sequence goes through the library's
   Hugging Face step and through the fast step, and the logits and the
   alignment-analyzer attention rows are compared every step. They may
   differ only by float rounding.
2. End to end: the same chunks are generated twice with the same seed,
   once per path. The speech tokens and the audio must come out identical.

Run it with the engine venv after any change to the fast decoder or any
upgrade of chatterbox-tts, transformers or torch:

    .venv-chatterbox\\Scripts\\python.exe scripts\\check_fast_decode_equivalence.py

Exit code 0 = equivalent, 1 = not. Uses the GPU; do not run it next to
another synthesis job.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

# Synthetic Finnish text, written for this check.
TEXTS = [
    "Aamulla järven rannalla oli hiljaista. Vanha mies istui laiturilla ja katseli, "
    "kuinka sumu nousi hitaasti veden pinnalta.",
    "Kaupungin kirjastossa oli tuona iltapäivänä tavallista enemmän väkeä. Opiskelijat "
    "lukivat tenttiin, eläkeläiset selasivat sanomalehtiä ja pieni tyttö etsi innoissaan "
    "kirjaa, jossa olisi paljon kuvia hevosista.",
]
TEACHER_STEPS = 400
MAX_REL_LOGIT_DIFF = 1e-4
MAX_ATTN_DIFF = 1e-4


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "generate_chatterbox_audiobook",
        _REPO / "scripts" / "generate_chatterbox_audiobook.py",
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def teacher_forced(runner, engine, ref_wav) -> bool:
    import torch
    import torch.nn.functional as F
    from chatterbox.models.t3.inference.alignment_stream_analyzer import (
        LLAMA_ALIGNED_HEADS,
        AlignmentStreamAnalyzer,
    )
    from chatterbox.models.t3.inference.t3_hf_backend import T3HuggingfaceBackend
    from chatterbox.mtl_tts import punc_norm

    from src.chatterbox_fast_decode import FastT3Decoder

    engine.prepare_conditionals(ref_wav, exaggeration=0.5)
    t3 = engine.t3
    hp = t3.hp
    tokens = engine.tokenizer.text_to_tokens(punc_norm(TEXTS[1]), language_id="fi").to(t3.device)
    tokens = torch.cat([tokens, tokens])
    tokens = F.pad(F.pad(tokens, (1, 0), value=hp.start_text_token), (0, 1), value=hp.stop_text_token)
    start = hp.start_speech_token * torch.ones_like(tokens[:, :1])
    embeds, len_cond = t3.prepare_input_embeds(
        t3_cond=engine.conds.t3, text_tokens=tokens, speech_tokens=start, cfg_weight=0.3)
    analyzer = AlignmentStreamAnalyzer(
        t3.tfmr, None, text_tokens_slice=(len_cond, len_cond + tokens.size(-1)),
        alignment_layer_idx=9, eos_idx=hp.stop_speech_token)
    try:
        model = T3HuggingfaceBackend(
            config=t3.cfg, llama=t3.tfmr, speech_enc=t3.speech_emb,
            speech_head=t3.speech_head, alignment_stream_analyzer=analyzer)
        bos = torch.tensor([[hp.start_speech_token]], device=t3.device)
        bos_embed = t3.speech_emb(bos) + t3.speech_pos_emb.get_fixed_embedding(0)
        inputs = torch.cat([embeds, torch.cat([bos_embed, bos_embed])], dim=1)
        prefill_len = inputs.size(1)
        out = model(inputs_embeds=inputs, past_key_values=None, use_cache=True,
                    output_attentions=True, output_hidden_states=True, return_dict=True)
        decoder = FastT3Decoder(t3, LLAMA_ALIGNED_HEADS)
        decoder.ensure_capacity(prefill_len + TEACHER_STEPS + 1)
        decoder.load_prefill(out.past_key_values, prefill_len)
        past = out.past_key_values

        worst_logit = worst_attn = 0.0
        token = out.logits[0:1, -1].argmax(-1, keepdim=True)
        for i in range(TEACHER_STEPS):
            embed = t3.speech_emb(token) + t3.speech_pos_emb.get_fixed_embedding(i + 1)
            ref = model(inputs_embeds=torch.cat([embed, embed]), past_key_values=past,
                        output_attentions=True, output_hidden_states=True, return_dict=True)
            past = ref.past_key_values
            decoder.step(token, prefill_len + i, i + 1)
            ref_logits = ref.logits[:, -1]
            fast_logits = decoder.logits[:, -1]
            worst_logit = max(worst_logit, (
                (ref_logits - fast_logits).abs().max() / ref_logits.abs().max()).item())
            for slot in range(len(LLAMA_ALIGNED_HEADS)):
                hook_row = analyzer.last_aligned_attns[slot][0]
                fast_row = decoder.aligned_rows[slot, : prefill_len + i + 1].cpu()
                worst_attn = max(worst_attn, (hook_row - fast_row).abs().max().item())
            token = ref_logits[0:1].argmax(-1, keepdim=True)
    finally:
        # PyPI chatterbox-tts 0.1.7's analyzer has no close(); the runner's
        # per-chunk cleanup removes its hooks and config changes either way.
        runner._clear_chatterbox_state(engine)

    ok = worst_logit <= MAX_REL_LOGIT_DIFF and worst_attn <= MAX_ATTN_DIFF
    print(f"[teacher-forced] {TEACHER_STEPS} steps, graphs {sorted(decoder.graphs)}: "
          f"logits rel diff {worst_logit:.2e} (max {MAX_REL_LOGIT_DIFF:.0e}), "
          f"attention diff {worst_attn:.2e} (max {MAX_ATTN_DIFF:.0e}) -> "
          f"{'OK' if ok else 'FAIL'}")
    return ok


def end_to_end(runner, engine, ref_wav) -> bool:
    import torch

    from src.chatterbox_fast_decode import install

    def generate_all():
        results = []
        for i, text in enumerate(TEXTS):
            torch.manual_seed(1234 + i)
            wav, _audio_s, wall_s = runner._generate_chunk(engine, text, "fi", ref_wav)
            results.append((wav, wall_s))
        return results

    library = generate_all()
    install(engine)
    fast = generate_all()
    ok = True
    for i, ((lib_wav, lib_s), (fast_wav, fast_s)) in enumerate(zip(library, fast)):
        same = lib_wav.shape == fast_wav.shape and torch.equal(lib_wav, fast_wav)
        ok &= same
        print(f"[end-to-end] chunk {i}: library {lib_s:.1f}s, fast {fast_s:.1f}s, "
              f"audio {'identical' if same else 'DIFFERENT'}")
    return ok


def main() -> int:
    import torch

    if not torch.cuda.is_available():
        print("[error] needs a CUDA GPU")
        return 1
    runner = _load_runner()
    device = runner._resolve_device("auto")
    engine, ref_wav = runner._load_engine(device, None, "fi")
    with torch.inference_mode():
        ok = teacher_forced(runner, engine, ref_wav)
    ok &= end_to_end(runner, engine, ref_wav)
    print("EQUIVALENT" if ok else "NOT EQUIVALENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
