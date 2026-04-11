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

There are **two installers** with different engine selections. Pick the
one that matches what you want.

### Which installer do I want?

| I want… | Pick |
|---|---|
| Finnish/English audiobooks at decent quality, fast, any hardware | **Main installer** |
| Full engine/voice/rate/reference settings matrix | **Main installer** |
| **Best Finnish quality** via Chatterbox + Finnish-NLP finetune | **Launcher installer** |
| Simple drop-a-PDF workflow, no settings screen | **Launcher installer** |
| Voice cloning from a reference audio clip | **Launcher installer** (Chatterbox engine) |

Both installers include Edge-TTS Noora and Piper Harri. **Only the
Launcher installer offers the Chatterbox Finnish engine** (the one that
produces the cleanest voice quality after the v7 fix stack) — it is
GPU-only and downloads ~15 GB of model weights during install. Everyone
else should pick the main installer.

### Main installer — advanced settings window

**Latest release:** [AudiobookMaker v1.0.1](https://github.com/MikkoNumminen/AudiobookMaker/releases/tag/v1.0.1)

- Bundles: Edge-TTS (online Noora), Piper (offline Harri), full Tkinter
  GUI with voice picker, rate slider, reference audio, voice description,
  per-chapter vs single-file output toggle
- **Does NOT bundle Chatterbox** — no voice cloning, no v7 Finnish quality
- One file (~200 MB), no Python or ffmpeg to install separately

1. Download `AudiobookMaker-Setup-1.0.1.exe` from the release page above
2. Double-click, click **More info → Run anyway** on the SmartScreen warning
3. Next → Next → Install
4. Launch from the Start Menu

### Launcher installer — simple window + optional Chatterbox

**Latest release:** [AudiobookMaker Launcher v0.1.0 (prerelease)](https://github.com/MikkoNumminen/AudiobookMaker/releases/tag/launcher-v0.1.0)

- Small launcher window: pick a PDF, click a button, get an MP3
- Wizard asks which engines to install:
  - **Edge-TTS Noora** — always included, ~0 MB extra
  - **Piper Harri** — ~60 MB voice download
  - **Chatterbox Finnish** *(opt-in, NVIDIA GPU required)* — ~15 GB
    download, installs a dedicated Python 3.11 venv at
    `C:\AudiobookMaker\.venv-chatterbox`, pulls the Chatterbox
    multilingual base model + the Finnish-NLP T3 finetune from
    HuggingFace, applies the Finnish gemination patch to the
    upstream `chatterbox-tts` package, and auto-installs Python 3.11
    silently if it's missing. First synthesis takes ~1–2 hours for a
    180-page book on an RTX 3080 Ti.

1. Download `AudiobookMaker-Launcher-Setup-0.1.0.exe` from the release
   page above
2. Double-click, click **More info → Run anyway** on the SmartScreen warning
3. Pick engines in the wizard (Full / Compact / Custom)
4. Wait — Chatterbox install takes 15–45 minutes visibly, downloads ~15 GB
5. Launcher opens when install completes; drop in a PDF and go

The launcher installer is a **per-user install** to
`%LOCALAPPDATA%\Programs\AudiobookMaker-Launcher\` — no admin / UAC
prompt. See [`docs/turo_ohjeet_fi.md`](docs/turo_ohjeet_fi.md) for the
Finnish end-user walkthrough.

#### Pre-flight checks the Launcher installer runs

Before it downloads anything the wizard verifies:

- Windows 10 build 17763 or newer (Windows 10 1809, Windows 11)
- 2 GB free disk (16 GB if Chatterbox is selected)
- NVIDIA GPU present + driver 550 or newer (only if Chatterbox is selected) —
  detected via `nvidia-smi`, falling back to PowerShell WMI and WMIC
- Not running ARM64 Windows (torch CUDA wheels don't exist for ARM64)

Each failed check shows a Finnish dialog explaining what's wrong and
what to do about it. If you picked Chatterbox but don't have a GPU the
wizard offers to proceed without it and install only Edge-TTS + Piper.

### Why the SmartScreen warning?

Both installers are unsigned. Windows flags all unsigned installers from
unknown publishers. Silencing the warning requires a paid code-signing
certificate (~$100-300/year), which the project does not currently have.
The installers are safe to run — their full source (PyInstaller spec +
Inno Setup script + post-install Python + GitHub Actions build) lives
in this repository and is built automatically on every tagged release.

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

### `dev_qwen_tts.py` — Qwen3-TTS feasibility probe (DROPPED)

A standalone script for trying [Qwen3-TTS](https://huggingface.co/Qwen)
locally. **This experiment is closed** — Qwen3-TTS is not a viable
engine for this project for three independent reasons:

1. **Finnish is not in its supported-language list** — Qwen3-TTS
   officially supports only 10 languages (Chinese, English, Japanese,
   Korean, German, French, Russian, Portuguese, Spanish, Italian).
   Passing `--language Finnish` raises a hard `ValueError`.
2. **MPS is blocked by a convolution channel limit** on Apple Silicon
   (`Output channels > 65536 not supported`), so the model cannot run
   on any Mac.
3. **CPU inference is slower than realtime even on an RTX 4090** per
   community reports.

The script is kept at the repo root so future developers who wonder
"why not Qwen?" can read its docstring and the failure chain in the
git history instead of re-doing the investigation. Run it with
`--help` for the full set of flags it was designed to take. Do not
expect audio output on any Mac.

**Use Chatterbox Finnish (via the Launcher installer) or Edge-TTS
Noora for actual Finnish audiobooks.**

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
# Dedicated venv for Chatterbox (Python 3.11, needs torch + chatterbox-tts)
python3.11 -m venv .venv-chatterbox
.venv-chatterbox/bin/pip install chatterbox-tts safetensors silero-vad \
    num2words pydub PyMuPDF

# Smoke test: one Finnish gemination/long-vowel probe sentence
.venv-chatterbox/bin/python dev_chatterbox_fi.py

# Real prose from a PDF, first 5 chunks, with the Finnish finetune
.venv-chatterbox/bin/python dev_chatterbox_fi.py \
    --pdf book.pdf --chunks 5 --finnish-finetune

# Custom voice clone
.venv-chatterbox/bin/python dev_chatterbox_fi.py --ref-audio my_voice.wav
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

**Honest verdict:** Chatterbox-Finnish produces listenable Finnish
audio — the finetune clones the reference voice convincingly and
handles gemination and long vowels correctly. The catch is pure
speed: 6–7× slower than realtime on Mac CPU makes it painful for
interactive work but feasible for overnight batch synthesis of a
whole book. On a NVIDIA RTX 3080 Ti it runs ~5–7× faster than
realtime (~80–110 min for a 180-page Finnish book).

**If you are an end user who wants this quality:** install the
[Launcher installer](#launcher-installer--simple-window--optional-chatterbox)
and pick the Chatterbox component during the wizard. The installer
does all of the dev-script plumbing described above (venv, torch,
chatterbox-tts, Finnish-NLP finetune, gemination patch) automatically
behind a progress bar. **You do NOT need to run `dev_chatterbox_fi.py`
or touch Python directly.**

If you want to run the whole book synthesis from a terminal instead
of the launcher, see `scripts/generate_chatterbox_audiobook.py` for
the full-book runner with per-chunk resumable caching. For a GPU
cloud alternative (RTX 4090 for ~$0.35 per 6-hour audiobook) see
`scripts/chatterbox_cloud_runbook.md`.

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
