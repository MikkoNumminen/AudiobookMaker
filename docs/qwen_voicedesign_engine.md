# Qwen3-TTS VoiceDesign engine (developer-only, GPU)

This is a **describe-a-voice** TTS engine. Instead of picking a voice from a
list, you write a sentence describing the voice you want — *"a warm male
narrator in his mid-30s, calm and measured"* — and the model invents a voice
to match. It is built on Alibaba's open-source **Qwen3-TTS VoiceDesign**
model.

It is a **developer / power-user engine**, not a feature of the installed
desktop app:

- It needs an NVIDIA GPU and a manual `pip install`.
- It is **never bundled** in the Windows installer (same as VoxCPM2).
- It is **never the default**, and it is **blocked for Finnish** (the model
  cannot speak Finnish — see the language section below).

> **History note.** An earlier look at Qwen3-TTS was dropped because the plain
> text-to-speech model does not speak Finnish and needs a GPU. This engine is a
> deliberate, narrower revival: it adds the *VoiceDesign* (describe-a-voice)
> capability for the 10 languages Qwen *does* support, as an opt-in developer
> engine, with Finnish explicitly guarded out. The old objections (no Finnish,
> GPU-only) are accepted on purpose and handled by the gating below.

---

## Phase 0 — recon (what the official repo says)

Everything here is confirmed from the official sources, not assumed:

- Official repo: <https://github.com/QwenLM/Qwen3-TTS>
- Model card: <https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign>

| Fact | Value |
|------|-------|
| **Model id** | `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` (Hugging Face & ModelScope) |
| **Python package** | `qwen-tts` (import name `qwen_tts`) |
| **Model size** | 1.7B parameters; the `12Hz` is the audio codec frame rate, not the audio sample rate |
| **Approx VRAM** | ~4 GB for the weights in `bfloat16`; comfortably fits a 12 GB card |
| **Licence** | Apache 2.0 |
| **Languages** | 10: Chinese, English, Japanese, Korean, German, French, Russian, Portuguese, Spanish, Italian. **No Finnish.** |

### The inference API

Loading the model and generating one sample looks like this (from the model
card, corrected per HF discussion #5 — the original card pasted the
*CustomVoice* example by mistake):

```python
import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

model = Qwen3TTSModel.from_pretrained(
    "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
    device_map="cuda:0",
    dtype=torch.bfloat16,
    attn_implementation="sdpa",  # "flash_attention_2" is faster but needs flash-attn
)

wavs, sr = model.generate_voice_design(
    text="Hello there, and welcome to the show.",
    language="English",
    instruct="A warm male narrator in his mid-30s, calm and measured.",
)
sf.write("output.wav", wavs[0], sr)
```

The three things worth remembering:

1. **The description goes in `instruct=`**, not glued onto the text. (This is
   different from VoxCPM2, where the description is prepended in parentheses to
   the text.)
2. **`language=` wants the full English name** of the language (`"English"`,
   `"German"`, …), not the short `en`/`de` code AudiobookMaker uses
   internally. The engine adapter maps between the two.
3. **`generate_voice_design` returns `(wavs, sr)`** — a list of waveforms plus
   the model's own sample rate. We take `wavs[0]` and trust the returned `sr`
   rather than hard-coding one.

VoiceDesign has no speaker list and no reference-audio cloning — the *only*
control is the natural-language `instruct`. (CustomVoice picks from preset
speakers; Base clones from reference audio. We deliberately wire up
**neither** of those — VoiceDesign only.)

### Dependencies & GPU requirements

```bash
pip install -U qwen-tts          # pulls torch, transformers, soundfile, etc.
pip install -U flash-attn --no-build-isolation   # OPTIONAL, faster attention
```

- An NVIDIA GPU with CUDA. `bfloat16` needs an Ampere card or newer (the
  project's RTX 3080 Ti qualifies).
- `flash_attention_2` is optional and can be painful to build on Windows. The
  POC and the engine default to `sdpa` (built into PyTorch, no extra build).

### Integration approach (one paragraph)

Qwen3-TTS VoiceDesign slots into AudiobookMaker's existing pluggable
`TTSEngine` interface (`src/tts_base.py`) as one more implementation, modelled
directly on the VoxCPM2 adapter (`src/tts_voxcpm.py`): a GPU, in-process,
developer-only engine that lazy-loads its model on first `synthesize()`,
caches it on the instance, normalizes + chunks text through the shared
`split_text_into_chunks` / `combine_audio_files` pipeline, writes one WAV per
chunk with `soundfile`, and stitches them with `pydub`. It registers itself
only when **not** running frozen (so it never reaches the installer), advertises
the 10 Qwen languages **minus Finnish**, and hard-blocks any unsupported
language at synthesis time. The free-text voice description flows in through
the engine contract's existing `voice_description` parameter (already wired
through the CLI's `--voice-description` flag) and is passed to
`generate_voice_design(instruct=...)`.

---

## Phase 1 — standalone POC (GO / NO-GO gate)

Before any of the above is wired into the app, prove the model actually runs
here and sounds acceptable. The script `scripts/qwen_voicedesign_poc.py` does
exactly that and **nothing else** — it does not import or touch the engine
architecture.

### Running it

On the GPU machine, in a Python environment with the package installed:

```bash
pip install -U qwen-tts
python scripts/qwen_voicedesign_poc.py
```

It will:

1. Load `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` once.
2. Generate **three** samples from three different voice descriptions (a warm
   male narrator, a bright young female narrator, a deep documentary voice).
3. Write them to `.local/scratch/qwen_voicedesign_poc/` as `.wav` files.
4. Print, for each sample, the generation time and the real-time factor
   (audio-seconds produced per wall-clock-second), plus the **peak VRAM** used.

Useful flags:

```bash
python scripts/qwen_voicedesign_poc.py --help
python scripts/qwen_voicedesign_poc.py --text "Your own test sentence." --language English
python scripts/qwen_voicedesign_poc.py --attn flash_attention_2   # if flash-attn is installed
python scripts/qwen_voicedesign_poc.py --out-dir D:\some\where
```

### What to check before saying GO

- It loads and runs on the 12 GB GPU without an out-of-memory error.
- Peak VRAM is in the expected ballpark (~4–6 GB).
- The three samples sound like three distinctly different, usable voices.

If the samples sound good, the engine integration (Phase 2+) is the next step.
