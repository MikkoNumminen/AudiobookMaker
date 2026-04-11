"""
Minimal reproducer for chatterbox-tts hook leak bug.

Bug: ChatterboxMultilingualTTS.generate() leaks forward hooks on every call.
After the first call, accumulated stale hooks cause generations to collapse
into immediate forced-EOS, producing ~0.4s of audio instead of 15-30s.

Root cause:
  chatterbox/models/t3/inference/alignment_stream_analyzer.py:84
    target_layer.register_forward_hook(attention_forward_hook)
  The returned handle is discarded, so the hook is never removed. A new
  AlignmentStreamAnalyzer (and thus a new hook) is created on every
  generate() call via chatterbox/models/t3/t3.py:281.

Expected output:
  Call 1: ~15-30s of audio
  Call 2: ~15-30s of audio
  ...

Actual output:
  Call 1: ~15-30s of audio
  Call 2: ~0.4s  (degenerate, forced-EOS)
  Call 3: ~0.4s
  Call 4: ~0.4s
  Call 5: ~0.4s

See BUG_REPORT_chatterbox_hook_leak.md for the full issue writeup.
"""

import sys
from pathlib import Path

import torch
from chatterbox.tts import ChatterboxMultilingualTTS

# Edit these two paths before running.
AUDIO_PROMPT_PATH = "sample_voice.wav"  # any short reference voice clip
TEXT = "This is a short English sentence used to reproduce the hook leak bug."

DEVICE = "cpu"  # or "cuda" / "mps"
NUM_CALLS = 5

# Toggle this to True to apply the workaround (clear stale hooks before each call).
APPLY_WORKAROUND = False


def main() -> None:
    if not Path(AUDIO_PROMPT_PATH).exists():
        sys.exit(f"Set AUDIO_PROMPT_PATH to a real wav file (got: {AUDIO_PROMPT_PATH})")

    print(f"Loading ChatterboxMultilingualTTS on {DEVICE}...")
    engine = ChatterboxMultilingualTTS.from_pretrained(device=DEVICE)
    sr = engine.sr

    for i in range(1, NUM_CALLS + 1):
        # --- Workaround: remove all leaked forward hooks before generating. ---
        if APPLY_WORKAROUND:
            for layer in engine.t3.tfmr.layers:
                layer.self_attn._forward_hooks.clear()
        # ----------------------------------------------------------------------

        with torch.no_grad():
            wav = engine.generate(
                TEXT,
                language_id="en",
                audio_prompt_path=AUDIO_PROMPT_PATH,
            )

        num_samples = wav.shape[-1]
        duration_s = num_samples / sr
        hook_count = sum(
            len(layer.self_attn._forward_hooks) for layer in engine.t3.tfmr.layers
        )
        print(
            f"Call {i}: duration={duration_s:6.2f}s  "
            f"samples={num_samples}  total_stale_hooks={hook_count}"
        )


if __name__ == "__main__":
    main()
