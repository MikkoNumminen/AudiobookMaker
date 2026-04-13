# AudiobookMaker

[![Build installer](https://github.com/MikkoNumminen/AudiobookMaker/actions/workflows/build-release.yml/badge.svg)](https://github.com/MikkoNumminen/AudiobookMaker/actions/workflows/build-release.yml)
[![Latest release](https://img.shields.io/github/v/release/MikkoNumminen/AudiobookMaker?label=installer&color=blue)](https://github.com/MikkoNumminen/AudiobookMaker/releases/latest)
[![License](https://img.shields.io/github/license/MikkoNumminen/AudiobookMaker?color=brightgreen)](LICENSE.txt)

Turn a PDF (or plain text) into an audiobook. Pick a file, press a button, get an MP3.

Works best with Finnish text, but English, German, Swedish, French, and
Spanish are also supported.

## What's new

**v2.3** -- Major update. Modern UI, more voices, auto-updates, and
a lot of fixes to make everything actually work reliably:

- **Modern look** -- the app uses CustomTkinter with dark/light mode
  that follows your Windows theme automatically
- **Listen button** -- type text, click Listen, hear it spoken right
  away. No need to save a file first. Great for trying out voices
- **30+ voices in 6 languages** -- Finnish, English, German, Swedish,
  French, and Spanish voices from Edge-TTS. Offline Piper voices for
  Finnish, English, and German
- **Auto-updates** -- the app checks for new versions every 5 minutes.
  When one is found, a banner appears at the top. Click it and the app
  downloads, installs, and restarts itself. No manual downloads needed
  after the first install
- **Voice recording** -- record your own voice directly from the app
  and use it for voice cloning with Chatterbox
- **Chatterbox works with text** -- you can type or paste text and
  synthesize it with Chatterbox. Previously only PDF input was
  supported
- **Smart language detection** -- the app detects your Windows language
  and picks Finnish or English UI automatically on first run
- **Single-instance guard** -- prevents accidentally opening two copies
  of the app, which could cause file conflicts or GPU crashes. If you
  need two windows (e.g. different engines on different files), the app
  asks you to confirm
- **Automatic output paths** -- no more file-picker dialogs before you
  start. PDF input saves the MP3 next to the PDF. Text input saves to
  Documents/AudiobookMaker with auto-incrementing filenames
- **500+ tests** -- pre-commit hooks and CI enforce that all tests pass
  before any code ships

**v2.0.0** -- Unified app:

- One download replaces both old installers (Main and Launcher)
- Finnish/English UI toggle
- Plain text input alongside PDF
- In-app engine installer for Chatterbox

---

## Two ways to use AudiobookMaker

| | Installer | Developer (clone the repo) |
|---|---|---|
| **Who is it for?** | Anyone with a Windows PC | Developers who want to tinker |
| **How do you get it?** | Download one .exe, install, done | Clone the repo, set up Python |
| **Voice engines** | Edge-TTS + Piper out of the box; Chatterbox via in-app install | Everything, including experimental engines |
| **Works offline?** | With Piper or Chatterbox | Yes, after first setup |
| **Needs a GPU?** | No (Chatterbox needs NVIDIA 8+ GB) | Depends on which engine you pick |
| **Voice cloning?** | Yes, with Chatterbox | Yes |
| **Languages** | Finnish, English, German, Swedish, French, Spanish | Same |
| **Download size** | ~200 MB (Chatterbox adds ~15 GB) | Varies |

---

## Installation

**Download:** [AudiobookMaker v2.3.3](https://github.com/MikkoNumminen/AudiobookMaker/releases/tag/v2.3.3)

**How to install:**
1. Download `AudiobookMaker-Setup-2.3.3.exe`
2. Double-click it. Windows will show a SmartScreen warning because the
   installer isn't signed -- click **More info**, then **Run anyway**
3. Click Next a few times, done
4. Open AudiobookMaker from the Start Menu

**Already have an older version?** The app checks for updates
automatically. When a new version is available, a banner appears at the
top of the window -- click "Update now" and the app handles everything.
No manual downloads, no installer prompts.

**What you get right away:**
- A window where you pick a PDF or type/paste text, choose a voice, and
  click Convert -- or click Listen to hear it spoken immediately
- Two voice engines:
  - **Edge-TTS** (needs internet) -- Microsoft's cloud voices. 30+
    voices across 6 languages. Fast, free, sounds good
  - **Piper** (works offline) -- downloads a voice model once (~60 MB),
    then works without internet forever
- Modern dark/light mode UI that follows your Windows setting
- The app detects your system language and starts in Finnish or English
  automatically
- No Python, no GPU, no command line needed

**Want the best Finnish voice quality?**

If you have an NVIDIA graphics card (RTX 3060 or better, 8+ GB video
memory), you can add Chatterbox right from inside the app:

1. Click **Install engines** in the app
2. The app downloads and sets up Chatterbox (~15 GB). A progress
   indicator shows what's happening
3. When it's done, Chatterbox appears as a voice engine option

With Chatterbox you also get:
- **Voice cloning** -- record a short clip of someone's voice, and the
  audiobook will sound like that person
- **Finnish text intelligence** -- the app understands Finnish grammar
  and reads numbers, dates, abbreviations, legal references, and
  loanwords the way a human would. For example:
  - `1300-luvulla` is read as "tuhat kolmesataa luvulla"
  - `esim.` is read as "esimerkiksi"
  - `5 %` is read as "viisi prosenttia"
  - `sivulta 42` inflects the number to match Finnish case grammar

**First audiobook timing:** A 180-page Finnish book takes about 1-2
hours to synthesize with Chatterbox on an RTX 3080 Ti. The result is a
set of MP3 files, one per chapter, plus one combined full-book MP3.

See [`docs/turo_ohjeet_fi.md`](docs/turo_ohjeet_fi.md) for a
step-by-step Finnish walkthrough aimed at beginners.

---

## Why the SmartScreen warning?

The installer is unsigned. Windows shows a scary-looking warning for
every unsigned program from an unknown publisher. Getting rid of this
warning requires a code-signing certificate ($100-300/year), which the
project doesn't have yet.

The installer is safe -- its entire build process is open source
in this repository and runs automatically on GitHub's servers on every
release. You can read every line of code that goes into it.

---

## Developer setup

**Best for:** You want to modify the code, experiment with different
TTS engines, or contribute to the project.

Cloning the repo gives you access to everything: all TTS engines, all
normalizer passes, experimental scripts, voice cloning tools, and the
full test suite.

### Getting started

Requires Python 3.11+, ffmpeg on PATH.

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

### TTS engines available in dev mode

**Edge-TTS** and **Piper** work the same as in the installer.

**Chatterbox-Finnish** needs a separate venv because it has heavy
dependencies (PyTorch, CUDA). The setup script handles everything:

```bash
powershell -ExecutionPolicy Bypass -File scripts\setup_chatterbox_windows.ps1
```

This creates `.venv-chatterbox/`, installs CUDA-enabled PyTorch,
downloads the AI models (~5 GB), and applies necessary patches.

**VoxCPM2** is an experimental engine from OpenBMB. It supports voice
cloning and natural-language voice design ("warm baritone elderly
male"). Not tested thoroughly -- install with `pip install voxcpm` if
you want to experiment. Requires NVIDIA GPU with ~8 GB VRAM.

### Developer scripts

These are standalone tools at the repo root and in `scripts/`. They
are not part of the shipped installer.

- **`dev_chatterbox_fi.py`** -- synthesize text with Chatterbox-Finnish
  from the command line. Run with `--help` for options
- **`scripts/generate_chatterbox_audiobook.py`** -- full book synthesis
  from PDF (or plain text file) to MP3 via Chatterbox. Resumable (safe
  to Ctrl-C and restart)
- **`scripts/generate_audiobook_parallel.py`** -- parallel Edge-TTS
  generator, about 8x faster than the GUI for large books
- **`scripts/record_voice_sample.py`** -- record a voice clip, validate
  its quality, and synthesize text in the cloned voice
- **`dev_qwen_tts.py`** -- Qwen3-TTS experiment. **Abandoned** --
  Finnish isn't supported, MPS is broken, CPU is too slow. Kept so
  nobody re-investigates the same dead end

### Finnish text normalizer

The normalizer makes Finnish numbers, abbreviations, and special terms
sound natural when read aloud. It runs automatically when using
Chatterbox-Finnish (via the app or dev scripts).

It works as a series of 16 text transformation passes covering:

- Century expressions (`1300-luvulla`)
- Year numbers and numeric ranges
- Abbreviations (`esim.`, `prof.`, `jne.`)
- Roman numerals with context-aware ordinal detection
- Unit symbols (`%`, `km`, `kg`)
- Section signs
- Finnish case inflection for numbers after prepositions
- Loanword respelling for words the AI mispronounces
- Various cleanup (ISBN stripping, TOC dot-leaders, metadata)

The normalizer has 400+ unit tests. See
[`docs/tts_text_normalization_cases.md`](docs/tts_text_normalization_cases.md)
for the full inventory.

### Known upstream issue

Chatterbox-TTS v0.1.7 has a bug where repeated calls to `generate()`
leak PyTorch hooks and corrupt internal state. Our scripts work around
this automatically. We've reported the bug and submitted a fix:
[resemble-ai/chatterbox#504](https://github.com/resemble-ai/chatterbox/issues/504),
[resemble-ai/chatterbox#505](https://github.com/resemble-ai/chatterbox/pull/505).

---

## Project structure

```
AudiobookMaker/
├── src/
│   ├── main.py              # App entry point + single-instance guard
│   ├── gui_unified.py       # CustomTkinter GUI (unified window)
│   ├── auto_updater.py      # GitHub-based auto-update checker
│   ├── system_checks.py     # GPU, disk, Python detection
│   ├── engine_installer.py  # In-app engine installation
│   ├── single_instance.py   # Prevent multiple app instances
│   ├── voice_recorder.py    # In-app voice recording for cloning
│   ├── pdf_parser.py        # PDF text extraction and cleanup
│   ├── tts_base.py          # TTS engine interface + registry
│   ├── tts_edge.py          # Edge-TTS adapter
│   ├── tts_piper.py         # Piper adapter
│   ├── tts_voxcpm.py        # VoxCPM2 adapter (dev only)
│   ├── tts_engine.py        # Text chunking, normalizer, audio combining
│   ├── fi_loanwords.py      # Finnish loanword respelling
│   ├── app_config.py        # Settings persistence
│   └── ffmpeg_path.py       # ffmpeg path helper
├── data/
│   └── fi_loanwords.yaml    # Loanword lexicon
├── tests/                   # Unit tests (460+)
├── scripts/                 # CLI tools and setup scripts
├── docs/                    # Documentation and research notes
├── installer/               # Inno Setup build scripts
├── assets/                  # Icons
├── .github/workflows/       # CI: auto-build installer on release
└── requirements.txt
```

## Limitations

- Edge-TTS needs an internet connection (it uses Microsoft's servers)
- Piper needs internet once to download each voice model (~60 MB)
- Chatterbox needs an NVIDIA GPU with 8+ GB video memory
- Scanned PDFs (where text is actually an image) don't work -- the text
  must be selectable in a PDF reader
- The Finnish normalizer is tuned for legal/historical prose. Other
  domains may have terms it doesn't handle yet

## License

MIT

