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

If the samples sound good, enable the engine (below).

---

## Using the engine

The engine is `src/tts_qwen_voicedesign.py` (`QwenVoiceDesignEngine`, id
`qwen_voicedesign`). It is built exactly like the VoxCPM2 adapter — one more
implementation of the shared `TTSEngine` interface, not a parallel system.

### Enabling it (developer-only)

```bash
pip install qwen-tts          # not in requirements.txt; never bundled
audiobookmaker-cli engines list   # qwen_voicedesign should now show "yes"
```

It registers itself **only when running from source** (the same `sys.frozen`
gate VoxCPM2 uses), so it can never reach the Windows installer. It is never a
default engine.

### Synthesizing from the CLI

The voice is the description. Pass it with `--voice-description` (or pick one of
the built-in preset voices with `--voice`):

```bash
# Free-text described voice:
audiobookmaker-cli convert book.txt \
    --engine qwen_voicedesign \
    --language en \
    --voice-description "A warm male narrator in his mid-30s, calm and measured."

# Or a built-in preset voice (no description needed):
audiobookmaker-cli convert book.txt \
    --engine qwen_voicedesign --language en --voice qwen-bright-female
```

Built-in preset voices (`--voice`): `qwen-neutral-narrator` (default),
`qwen-warm-male`, `qwen-bright-female`. A free-text `--voice-description` always
overrides the preset.

Optional environment variables:

| Variable | Default | Meaning |
|----------|---------|---------|
| `AUDIOBOOKMAKER_QWEN_ATTN` | `sdpa` | Attention impl; set to `flash_attention_2` if flash-attn is built |
| `AUDIOBOOKMAKER_QWEN_DEVICE` | `cuda:0` | Which GPU to load the model on |

### Language limitation & guard

Qwen3-TTS speaks **10 languages**: Chinese, English, Japanese, Korean, German,
French, Russian, Portuguese, Spanish, Italian. **Finnish is not supported.**

Two layers enforce this:

1. `supported_languages()` returns those 10 short codes **without `fi`**, so the
   GUI's Language → Engine funnel never offers this engine for a Finnish book
   (and never auto-selects it for one).
2. `synthesize()` hard-fails with a clear `ValueError` on any language outside
   the 10 — so even a direct CLI call with `--language fi` is blocked rather
   than producing garbage.

Text normalization runs before chunking **only** for the languages the
project's normalizer handles (`fi`/`en`); the other Qwen languages pass through
unmodified, because the normalizer is Finnish/English-specific and would
mis-handle (or reject) them.

### What is NOT in the VoiceDesign engine

- **No preset speakers here.** The preset-speaker mode is the separate
  **CustomVoice engine** (`qwen_customvoice`, see below) — VoiceDesign's only
  control is the natural-language description.
- **No voice cloning here.** Reference-audio cloning is its own path (the
  `qwen_clone` engine, below); `supports_voice_cloning` is `False` and any
  `reference_audio` passed to VoiceDesign is ignored.

### Example voice descriptions

- *"A warm male narrator in his mid-30s, calm and measured, with a friendly,
  reassuring tone."*
- *"A bright, expressive young woman with a light, energetic voice and clear
  diction."*
- *"A deep, resonant documentary voice, slow and authoritative, with dramatic
  gravitas."*
- *"An older gentleman with a gentle, grandfatherly voice and a slow, soothing
  pace."*

### Testing

`tests/test_tts_qwen_voicedesign.py` mocks the heavy model (it is never
installed in CI) and covers status, voices, the language guard, the
description-vs-preset resolution, and the chunk/combine wiring. One real
end-to-end test, `test_real_synthesis_smoke`, is gated behind the `gpu` (plus
`slow` + `network`) markers and is skipped unless a CUDA GPU and `qwen-tts` are
actually present:

```bash
pytest tests/test_tts_qwen_voicedesign.py            # mocked unit tests
pytest -m gpu tests/test_tts_qwen_voicedesign.py     # real smoke test (needs GPU + qwen-tts)
```

---

## CustomVoice engine (preset speakers)

`src/tts_qwen_customvoice.py` (`QwenCustomVoiceEngine`, id `qwen_customvoice`) is
the sibling engine for Qwen3-TTS's **preset-speaker** mode. Instead of describing
a voice, you pick one of **9 built-in premium speakers** and optionally steer the
delivery with a free-text style instruction.

It is a **separate ~4 GB checkpoint** (`Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`),
downloaded on demand the first time you use it. Same dev-only / GPU / no-Finnish
gating as VoiceDesign; the shared plumbing lives in `src/tts_qwen_common.py`.

### Speakers (`--voice`)

`Vivian`, `Serena`, `Uncle_Fu`, `Dylan`, `Eric`, `Ryan`, `Aiden`, `Ono_Anna`,
`Sohee` (default: `Vivian`). Each speaker can read any of the 10 languages, best
in its native one.

### Synthesizing from the CLI

```bash
# Pick a preset speaker:
audiobookmaker-cli convert book.txt \
    --engine qwen_customvoice --language en --voice Ryan

# Steer the delivery style with --voice-description (optional):
audiobookmaker-cli convert book.txt \
    --engine qwen_customvoice --language en --voice Vivian \
    --voice-description "read this in a calm, slow, soothing tone"
```

The difference from VoiceDesign: here `--voice` is the **speaker** (the voice
identity) and `--voice-description` is an **optional style/emotion** layered on
top, whereas in VoiceDesign the description *is* the voice. The same
`AUDIOBOOKMAKER_QWEN_ATTN` / `AUDIOBOOKMAKER_QWEN_DEVICE` env vars and the same
Finnish/language guard apply. Tests: `tests/test_tts_qwen_customvoice.py`.

---

## Voice Clone engine (clone from a sample)

`src/tts_qwen_clone.py` (`QwenVoiceCloneEngine`, id `qwen_clone`) clones a voice
from a short **reference clip**: give it a few seconds of someone's speech and it
reads your text in that voice. It loads the separate ~4 GB Base checkpoint
(`Qwen/Qwen3-TTS-12Hz-1.7B-Base`). Same dev-only / GPU / no-Finnish gating.

> **Local-use only.** This clones real voices. It is a developer engine that
> produces local output; do not redistribute clones of real people. The
> reference transcript is treated as potentially personal and is never logged.

### The reference transcript

Qwen's clone path wants a transcript of the reference clip (`ref_text`) for best
quality. You only pass the **audio**, so the engine fills the transcript in
priority order:

1. an explicit transcript in `AUDIOBOOKMAKER_QWEN_REF_TEXT` (skips Whisper);
2. otherwise it auto-transcribes the clip with **faster-whisper** (best quality
   — `pip install faster-whisper`; model size via `AUDIOBOOKMAKER_QWEN_WHISPER_MODEL`,
   default `small`);
3. if faster-whisper is absent or fails, it falls back to Qwen's
   `x_vector_only_mode` (no transcript, slightly lower quality) so it still runs.

### Synthesizing from the CLI

```bash
# Clone the voice in a reference clip (auto-transcribed if faster-whisper is present):
audiobookmaker-cli convert book.txt \
    --engine qwen_clone --language en --ref-audio path/to/voice_sample.wav

# Provide the transcript yourself to skip Whisper:
AUDIOBOOKMAKER_QWEN_REF_TEXT="exact words spoken in the clip" \
audiobookmaker-cli convert book.txt \
    --engine qwen_clone --language en --ref-audio path/to/voice_sample.wav
```

The reference clip should be a few seconds of clean speech. There are no preset
voices — the clip *is* the voice. Tests: `tests/test_tts_qwen_clone.py`.

---

## Shared code

All three engines subclass `QwenEngineBase` in `src/tts_qwen_common.py`, which
holds everything they have in common: the 10-language map (no Finnish), the model
load (with `attn` validation), the GPU/availability check, the waveform coercion
to CPU float32, and the chunk → generate → combine driver. Each engine only adds
its voice catalogue (or, for Clone, the reference handling) and its one
`generate_*` call:

| Engine | id | "Custom reader" via | `generate_*` |
|--------|-----|--------------------|--------------|
| VoiceDesign | `qwen_voicedesign` | describe a voice in words | `generate_voice_design` |
| CustomVoice | `qwen_customvoice` | pick 1 of 9 preset speakers (+ style) | `generate_custom_voice` |
| Voice Clone | `qwen_clone` | clone from a reference clip | `generate_voice_clone` |
