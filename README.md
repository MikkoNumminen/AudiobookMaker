# AudiobookMaker

Converts PDF files into audiobooks. Load a PDF, press a button, get an MP3.

## Features

- Automatic PDF text extraction and cleanup (page numbers, headers, footers)
- Robust text cleaning: strips soft hyphens, fixes line-wrap hyphenation,
  flattens in-paragraph line wraps, preserves compound hyphens
- Automatic chapter detection
- Multiple TTS engines selectable from the GUI:
  - **Edge-TTS** (online, Microsoft, default) — Finnish and English neural voices
  - **Piper** (offline, CPU-only) — offline neural voices, models auto-downloaded on first use
  - **VoxCPM2** (developer-install only, NVIDIA GPU) — voice cloning + natural-language voice design
- "Test voice" button to preview the selected engine + voice before converting
- Optional reference audio picker for voice cloning (VoxCPM2)
- Optional free-text "voice description" field for natural-language voice design (VoxCPM2)
- Remembers the last-used engine, voice, language, speed, reference audio, and
  voice description between sessions (config in `~/.audiobookmaker/config.json`)
- Context-aware sentence splitter that handles Finnish and English
  abbreviations, initials, decimals, and domain names
- Silence trimming between chunks for seamless audio
- Single combined MP3 or one file per chapter
- Simple Tkinter GUI
- Parallel CLI generator for large books
- Windows installer — no Python or other dependencies required

## TTS engines

### Edge-TTS (online, default)

Microsoft's free online neural TTS. Fast, high quality, no local model
files. Requires an internet connection during synthesis.

- Finnish voices: Noora, Harri
- English US voices: Jenny, Aria, Ava, Guy, Andrew
- English GB voices: Sonia, Ryan
- Does not need a GPU. Does not support voice cloning.

### Piper (offline, CPU)

Local neural TTS that runs entirely on CPU — no internet needed after the
first voice download. Better Finnish pronunciation than Edge-TTS for some
phrases. Voice models are ~60 MB each and are downloaded automatically on
first use to `~/.audiobookmaker/piper_voices/` (not bundled with the
installer).

- Finnish voice: Harri
- English US voices: Lessac (female), Ryan-high (male)
- English GB voice: Alan (male)
- Does not need a GPU. Does not support voice cloning.

### VoxCPM2 (developer-install only, NVIDIA GPU)

Open-source neural TTS from OpenBMB that supports 30 languages including
Finnish, runs locally, and offers two advanced features not available in
the other engines:

- **Zero-shot voice cloning** from a short reference audio clip
- **Voice design** — describe the desired voice in natural language
  (e.g. `"warm baritone elderly male"`) and the model steers its output
  toward that description

**Not bundled with the Windows installer.** PyTorch + the model weights
are several gigabytes, which would make the installer unusable. Users
who want to try VoxCPM2 must run the project from source and install
the package manually:

```bash
pip install voxcpm
```

Requirements: Python ≥ 3.10, PyTorch ≥ 2.5 with CUDA ≥ 12.0, NVIDIA GPU
with roughly 8 GB VRAM. There is no CPU fallback — on machines without a
CUDA GPU (including all Macs) the engine appears in the dropdown but
reports itself as unavailable and the existing Edge-TTS / Piper engines
keep working.

**Honest expectations:**

- VoxCPM2's Finnish quality has not been A/B tested against Edge-TTS
  Noora by the project maintainers. It may be better, comparable, or
  worse depending on the text. Try it yourself before committing to it
  for a full book.
- Voice description prompts work best for broad characteristics
  (age, tone, gender). Specific ethnic accents across language boundaries
  (e.g. "African American accent reading Finnish") are well outside what
  any current open-source multilingual model handles reliably. Use
  voice cloning with a reference clip for stronger persona matching.

## Installation (end users)

**Latest release:** [AudiobookMaker v1.0.1](https://github.com/MikkoNumminen/AudiobookMaker/releases/tag/v1.0.1)

1. Download `AudiobookMaker-Setup-1.0.1.exe` from the release above
   (or browse all releases on the [Releases](../../releases) page)
2. Double-click the downloaded file
3. Windows will show a **"Windows protected your PC"** SmartScreen warning
   because the installer is not code-signed. Click **More info** → **Run anyway**
4. Follow the installer prompts (Next → Next → Install)
5. Find the app in the Start Menu

No Python, ffmpeg, or other dependencies need to be installed separately —
everything is bundled in the single `.exe`.

### Why the SmartScreen warning?

Windows flags all unsigned installers from unknown publishers. Silencing
the warning requires a paid code-signing certificate (~$100-300/year),
which the project does not currently have. The installer is safe to run;
its full source (PyInstaller spec + Inno Setup script + GitHub Actions
build) lives in this repository and is built automatically on every
tagged release.

## Usage

1. Open the app
2. Select a PDF file
3. Choose the TTS engine from the dropdown (Edge-TTS, Piper, or VoxCPM2)
   - The first Piper conversion will trigger a ~60 MB voice-model download
   - VoxCPM2 only activates if `pip install voxcpm` has been run and a
     CUDA GPU is available
4. Choose language (Finnish / English)
5. Pick a voice; press **Kuuntele näyte** to hear a short sample before committing
6. (VoxCPM2 only) optionally supply a **reference audio clip** for voice
   cloning and / or a **voice description** like `warm baritone elderly male`
7. Adjust speech rate if needed
8. Click **Convert** — the progress bar shows status
9. Save the MP3

## Development setup

Requires Python 3.11+, ffmpeg on system PATH or in `dist/`.

```bash
git clone <repo>
cd AudiobookMaker
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m src.main
```

Run tests:

```bash
pytest tests/
```

## Command-line parallel generator

For large PDFs the GUI's sequential synthesis can be slow. The
`scripts/generate_audiobook_parallel.py` script runs multiple edge-tts
requests concurrently and finishes ~8× faster on a typical book:

```bash
python scripts/generate_audiobook_parallel.py <input.pdf> <output.mp3> [concurrency]
```

Example: a 180-page, 375k-character Finnish book converts in ~6 minutes at
concurrency 8, versus ~55 minutes sequentially.

## Developer scripts (local experiments)

Standalone scripts at the repo root are **not** part of the shipped app.
They exist so you can poke at experimental TTS stacks locally without
polluting the main installer or the main project venv. Each one is a
single file, reads its own docstring for usage, and has dedicated tests
under `tests/`.

### `dev_qwen_tts.py` — Qwen3-TTS runner (experimental, Mac-friendly)

Standalone script for trying [Qwen3-TTS](https://huggingface.co/Qwen)
locally. Supports three modes via flags:

- **Preset voice** (default) — `Qwen3-TTS-12Hz-0.6B-CustomVoice`, pick a
  speaker with `--speaker`
- **Voice cloning** — `--ref-audio my_voice.wav` runs the `Base` model
- **Voice design** — `--voice-description "warm baritone elderly male"`
  runs the `VoiceDesign` model

```bash
# One-time setup: dedicated Python 3.11+ venv (Qwen uses PEP 604 syntax)
brew install python@3.11 sox
python3.11 -m venv .venv-qwen
.venv-qwen/bin/pip install \
    torch torchaudio transformers==4.57.3 accelerate \
    einops librosa sox soundfile onnxruntime \
    huggingface_hub PyMuPDF pydub

# Run against the first few chunks of a PDF
.venv-qwen/bin/python dev_qwen_tts.py book.pdf --max-chunks 4
.venv-qwen/bin/python dev_qwen_tts.py book.pdf --voice-description "tired narrator"
.venv-qwen/bin/python dev_qwen_tts.py book.pdf --ref-audio voice.wav --language English
```

Useful flags: `--device {mps,cpu,cuda}`, `--language {auto,english,...}`,
`--max-chunks N` (smoke-test the first N chunks only), `--speaker NAME`.
Run with `--help` for the full list.

**Known limitations on Mac (Apple Silicon):**

- Qwen3-TTS is officially CUDA-only and relies on flash-attn3 kernels
- MPS hits a hard `Output channels > 65536 not supported` convolution
  limit inside the decoder — the script forces `float32` everywhere to
  dodge float16 NaNs, but the channel limit is a real blocker for MPS
- `--device cpu` loads, but inference is far slower than realtime even
  on an M3/M4 — fine for a 1-sentence sanity check, not for a whole book
- **Finnish is not in Qwen3-TTS's supported-language list.** The script
  warns at startup and defaults `--language` to `auto`. For actual
  Finnish audiobooks use the Edge-TTS or Piper engines in the main GUI

Treat this script as feasibility probe, not a production path.

### `dev_chatterbox_fi.py` — Chatterbox-Finnish voice-cloning TTS (experimental, CPU-slow)

Standalone script for trying
[Chatterbox](https://huggingface.co/ResembleAI/chatterbox) and its
Finnish finetune
[Finnish-NLP/Chatterbox-Finnish](https://huggingface.co/Finnish-NLP/Chatterbox-Finnish)
locally. Chatterbox is a zero-shot voice-clone TTS — there is no
speakerless mode; every run needs a reference WAV. Supports three modes:

- **Base multilingual** (default) — `ResembleAI/chatterbox`, Finnish via
  `language_id='fi'`, cloned from the bundled `samples/reference_finnish.wav`
- **Finnish-NLP finetune** — `--finnish-finetune` swaps the T3 weights
  for `best_finnish_multilingual_cp986.safetensors` (~2 GB), claiming
  MOS 4.34 / WER 2.76% on Finnish
- **Custom voice clone** — `--ref-audio my_voice.wav` uses your own
  reference clip instead of the Finnish sample

```bash
# Reuse the existing .venv-qwen (Python 3.11, already has torch)
.venv-qwen/bin/pip install chatterbox-tts safetensors

# Smoke test: one Finnish gemination/long-vowel probe sentence
.venv-qwen/bin/python dev_chatterbox_fi.py

# Real prose from a PDF, first 5 chunks, with the Finnish finetune
.venv-qwen/bin/python dev_chatterbox_fi.py \
    --pdf book.pdf --chunks 5 --finnish-finetune

# Custom voice clone
.venv-qwen/bin/python dev_chatterbox_fi.py --ref-audio my_voice.wav
```

Useful flags: `--device {cpu,mps,cuda}` (default `cpu` — safest on Mac),
`--chunks N`, `--chunk-chars 500`, `--text "..."`, `--output out.mp3`,
`--cfg-weight`, `--exaggeration`, `--temperature`.

**Measured behavior on this Mac (CPU):**

- First run downloads ~5.3 GB of model weights into the HF cache
  (multilingual base + s3gen + tokenizer + reference WAV), plus ~2 GB
  for the Finnish finetune if `--finnish-finetune` is used
- Synthesis runs ~6–7× slower than realtime on CPU — a 25 s chunk
  costs ~150 s wall-clock. MPS mostly works but silently falls back
  to CPU for parts of `s3gen` and the perth watermarker
- **`--finnish-finetune` is effectively required for stable Finnish
  prose.** The stock multilingual weights trigger a token-repetition
  forced-EOS partway through most Finnish sentences; the finetune
  runs cleanly at the documented "golden settings"
  (`repetition_penalty=1.5`, `temperature=0.8`, `cfg_weight=0.3`)
- **Upstream bug workaround:** `chatterbox-tts` v0.1.7 leaks a
  PyTorch forward hook inside `alignment_stream_analyzer.py` on
  every `generate()` call, plus it mutates `tfmr.config` without
  restoring it. After chunk #1 the accumulated state collapses
  chunks 2+ to ~0.4 s of audio. The dev script clears
  `self_attn._forward_hooks`, resets `engine.t3.compiled = False`,
  and restores `tfmr.config._attn_implementation` and
  `output_attentions` before each chunk to work around this — no
  action needed from you, but see `docs/upstream/chatterbox/BUG_REPORT.md`
  and `docs/upstream/chatterbox/hook_leak_fix.patch` for the upstream fix
- **Cross-language voice cloning is weak** — the T3 finetune owns
  Finnish pronunciation, the reference WAV only conditions speaker
  pitch/timbre. An English or African English reference will produce
  Finnish audio in a different voice character, NOT Finnish spoken
  with an English accent. Upstream README confirms this and suggests
  `cfg_weight=0.0` to MITIGATE accent bleed-through

**Honest verdict:** unlike Qwen3-TTS, Chatterbox-Finnish actually
produces listenable Finnish audio on this Mac — the finetune clones
the reference voice convincingly and handles gemination and long
vowels correctly. The catch is pure speed: 6–7× slower than realtime
on CPU makes it painful for interactive work but feasible for
overnight batch synthesis of a whole book. For real use see
`scripts/chatterbox_cloud_runbook.md` — an RTX 4090 on RunPod runs
at ~0.1× RTF (10× faster than realtime) for ~$0.35 per 6-hour book.
Until you have a GPU path, the main app's Edge-TTS and Piper engines
remain the practical Finnish path.

## Project structure

```
AudiobookMaker/
├── src/
│   ├── pdf_parser.py    # PDF parsing and text cleaning
│   ├── tts_base.py      # Abstract TTSEngine interface + registry
│   ├── tts_edge.py      # Edge-TTS engine adapter
│   ├── tts_piper.py     # Piper offline TTS engine adapter
│   ├── tts_voxcpm.py    # VoxCPM2 GPU engine (developer install only)
│   ├── tts_engine.py    # Shared text chunking + audio combining
│   ├── app_config.py    # GUI preference persistence
│   ├── gui.py           # Tkinter UI
│   ├── ffmpeg_path.py   # Runtime ffmpeg path helper for bundled builds
│   └── main.py          # Application entry point
├── tests/               # Unit tests
├── scripts/             # CLI helpers (parallel audiobook generator)
├── assets/              # Icon and other resources
├── installer/           # Inno Setup script
├── .github/workflows/   # CI: build Windows installer and publish releases
├── dist/                # Compiled binaries (not version-controlled)
└── requirements.txt
```

## Tech stack

| Component | Library |
|-----------|---------|
| PDF parsing | PyMuPDF (fitz) |
| Online TTS | edge-tts |
| Offline TTS | piper-tts (ONNX Runtime) |
| Audio processing | pydub + ffmpeg |
| GUI | Tkinter |
| Windows packaging | PyInstaller |
| Installer | Inno Setup |

## Limitations

- Edge-TTS uses Microsoft's servers — requires an internet connection
- Piper voice downloads require internet on first use of each voice
- VoxCPM2 requires an NVIDIA GPU (~8 GB VRAM); no CPU fallback exists
- VoxCPM2 voice-description prompts for specific ethnic accents across
  language boundaries are not reliably supported by any current open-
  source multilingual TTS
- Scanned PDFs (image-based) are not supported — text must be selectable
- Text cleanup heuristics may not work perfectly for all PDF formats
- "One MP3 per chapter" output currently only works with Edge-TTS
  (Piper and VoxCPM2 conversions always produce a single combined MP3)

## License

MIT
