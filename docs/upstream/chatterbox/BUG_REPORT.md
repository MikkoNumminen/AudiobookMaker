# `AlignmentStreamAnalyzer` leaks a forward hook per `generate()` call, causing state leak across runs

## Summary

Repeated calls to `ChatterboxMultilingualTTS.generate()` on the same engine
instance collapse after the first call: call 1 returns the expected
full-length audio (~15-30 s for a short sentence), but calls 2+ return roughly
0.4 s of garbage (the analyzer forces EOS almost immediately). This makes the
multilingual engine unusable for any multi-chunk workflow such as audiobook
narration, long-form synthesis, or any service that reuses a loaded model
across requests.

The cause is a forward-hook leak in `AlignmentStreamAnalyzer.__init__`: the
`RemovableHandle` returned by `register_forward_hook` is discarded, and a new
analyzer (and thus a new hook) is constructed on every `T3.inference()` call.
Stale hooks from previous runs keep firing and corrupt the alignment state
used to force EOS.

## Observed behaviour

```
Call 1: duration= 18.42s  samples=883200  total_stale_hooks=1
Call 2: duration=  0.40s  samples=19200   total_stale_hooks=2
Call 3: duration=  0.40s  samples=19200   total_stale_hooks=3
Call 4: duration=  0.40s  samples=19200   total_stale_hooks=4
Call 5: duration=  0.40s  samples=19200   total_stale_hooks=5
```

Expected: every call returns a full-length synthesis, and
`len(engine.t3.tfmr.layers[layer_idx].self_attn._forward_hooks)` stays constant.

## Minimal reproducer

```python
import torch
from chatterbox.mtl_tts import ChatterboxMultilingualTTS

engine = ChatterboxMultilingualTTS.from_pretrained(device="cpu")
text = "This is a short English sentence used to reproduce the hook leak bug."

for i in range(1, 6):
    with torch.no_grad():
        wav = engine.generate(
            text,
            language_id="en",
            audio_prompt_path="sample_voice.wav",  # any short reference clip
        )
    hooks = sum(
        len(layer.self_attn._forward_hooks) for layer in engine.t3.tfmr.layers
    )
    print(f"Call {i}: {wav.shape[-1] / engine.sr:5.2f}s  hooks={hooks}")
```

Call 1 prints ~15-30 s; calls 2-5 print ~0.40 s, and the hook count grows
monotonically.

## Root cause

`src/chatterbox/models/t3/inference/alignment_stream_analyzer.py`, line 84:

```python
target_layer = tfmr.layers[layer_idx].self_attn
# Register hook and store the handle
target_layer.register_forward_hook(attention_forward_hook)
```

The return value of `register_forward_hook` (a `torch.utils.hooks.RemovableHandle`)
is discarded, so the hook is never removed. `_add_attention_spy` is called
once per entry in `LLAMA_ALIGNED_HEADS` (three entries), so each analyzer
instance actually registers three hooks.

A fresh `AlignmentStreamAnalyzer` is constructed on every call via
`src/chatterbox/models/t3/t3.py`, lines 277-287:

```python
if not self.compiled:
    # Default to None for English models, only create for multilingual
    alignment_stream_analyzer = None
    if self.hp.is_multilingual:
        alignment_stream_analyzer = AlignmentStreamAnalyzer(
            self.tfmr,
            None,
            text_tokens_slice=(len_cond, len_cond + text_tokens.size(-1)),
            alignment_layer_idx=9, # TODO: hparam or something?
            eos_idx=self.hp.stop_speech_token,
        )
```

Note that `self.compiled = False` is force-set on line 273 immediately before
this block, so the `if not self.compiled` guard never short-circuits and a new
analyzer is built on every call. After N calls the three attention layers
targeted by `LLAMA_ALIGNED_HEADS` carry N stale hooks apiece, all closing over
earlier analyzer instances whose `last_aligned_attns` buffers still update on
every forward pass. The result is that the alignment matrix fed to
`AlignmentStreamAnalyzer.step()` is polluted by stale state from previous
generations, which trips the `long_tail` / `alignment_repetition` heuristics
and forces EOS via the branch at line 174-178.

## End-user workaround

Before every `generate()` call, clear the leaked hooks on the attention
layers:

```python
for layer in engine.t3.tfmr.layers:
    layer.self_attn._forward_hooks.clear()
wav = engine.generate(text, language_id="en", audio_prompt_path="voice.wav")
```

This restores correct full-length output on every call. It relies on the
private `_forward_hooks` attribute, so it is only a stopgap.

A fully correct workaround also needs to:

1. Reset `engine.t3.compiled = False` (it already is, but the intent matters
   if a future fix flips it to `True`) so `patched_model` is rebuilt against a
   clean transformer.
2. Restore the two `tfmr.config` fields that
   `alignment_stream_analyzer.py` mutates on lines 85-90 without ever
   restoring them: `tfmr.config.output_attentions` (set to `True`) and
   `tfmr.config._attn_implementation` (flipped from `sdpa` to `eager`). Every
   analyzer construction re-saves the *already-mutated* values as
   "original", so the original `sdpa` / `output_attentions=False` settings
   are lost after the first call. This is a second, smaller state leak in
   the same file.

## Proper fix

The accompanying patch at `hook_leak_fix.patch` (same directory) (against
`src/chatterbox/models/t3/inference/alignment_stream_analyzer.py` and
`src/chatterbox/models/t3/t3.py`) does the following:

- Store every `RemovableHandle` on `self._hook_handles` in
  `_add_attention_spy`.
- Save the original `tfmr.config.output_attentions` and
  `_attn_implementation` exactly once (guarded by `self._config_patched`) so
  repeated analyzer construction can no longer overwrite the true originals
  with already-patched values.
- Add `AlignmentStreamAnalyzer.close()` that removes all stored hooks and
  restores the saved config values. Safe to call multiple times. Also
  implements `__enter__` / `__exit__` for context-manager use.
- In `T3.inference()`, wrap the generation body in `try: ... finally:
  alignment_stream_analyzer.close()` so hooks are always removed, even on
  exception. Relying on `__del__` is not sufficient because CPython may not
  collect the analyzer promptly, especially under CUDA/MPS.

Happy to open a PR with the patch if the maintainers confirm this is the
preferred direction.

## Environment

- `chatterbox-tts` 0.1.7 (PyPI, `chatterbox_tts-0.1.7.dist-info`)
- `torch` 2.6.0
- `transformers` 5.2.0
- Python 3.11.15
- macOS 14 (Darwin 23.5), Apple Silicon; also reproducible on Linux CUDA
- Reproduced on `device="cpu"` and `device="mps"`
- Model: `ChatterboxMultilingualTTS.from_pretrained(...)` (the English-only
  `ChatterboxTTS` path is unaffected because `self.hp.is_multilingual` is
  `False` and the analyzer is never constructed, per `t3.py` line 280)
