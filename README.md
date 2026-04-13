# AudiobookMaker

[![Build installer](https://github.com/MikkoNumminen/AudiobookMaker/actions/workflows/build-release.yml/badge.svg)](https://github.com/MikkoNumminen/AudiobookMaker/actions/workflows/build-release.yml)
[![Latest release](https://img.shields.io/github/v/release/MikkoNumminen/AudiobookMaker?label=installer&color=blue)](https://github.com/MikkoNumminen/AudiobookMaker/releases/latest)
[![License](https://img.shields.io/github/license/MikkoNumminen/AudiobookMaker?color=brightgreen)](LICENSE.txt)

Turn a PDF (or plain text) into an audiobook. Pick a file, press a button, get an MP3.

Works best with Finnish text, but English is supported too.

## New in v2.0.0

- **Unified app** -- one download replaces both old installers (Main and Launcher). Everything is in one place now.
- **Finnish/English UI** -- toggle the interface language from inside the app.
- **Plain text input** -- type or paste text directly. You no longer need a PDF to get started.
- **In-app engine installer** -- if you have an NVIDIA GPU, you can add Chatterbox without leaving the app. Click "Install engines", wait, done.

---

## Two ways to use AudiobookMaker

| | Installer | Developer (clone the repo) |
|---|---|---|
| **Who is it for?** | Anyone with a Windows PC | Developers who want to tinker |
| **How do you get it?** | Download one .exe, install, done | Clone the repo, set up Python |
| **Voice engines included** | Edge-TTS + Piper out of the box; Chatterbox available via in-app install | Everything, including experimental engines |
| **Works offline?** | Only with Piper (or Chatterbox once installed) | Yes, after first setup |
| **Needs a GPU?** | No (but Chatterbox needs NVIDIA 8+ GB) | Depends on which engine you pick |
| **Voice cloning?** | Yes, if Chatterbox is installed | Yes |
| **Finnish text intelligence?** | Yes, if Chatterbox is installed | Full |
| **Download size** | ~200 MB (Chatterbox adds ~15 GB if you install it) | Varies |

Read on for the details.

---

## Installation

**Download:** [AudiobookMaker v2.2.0](https://github.com/MikkoNumminen/AudiobookMaker/releases/tag/v2.2.0)

**How to install:**
1. Download `AudiobookMaker-Setup-2.2.0.exe`
2. Double-click it. Windows will show a SmartScreen warning because the
   installer isn't signed -- click **More info**, then **Run anyway**
3. Click Next a few times, done
4. Open AudiobookMaker from the Start Menu

**What you get right away:**
- A window where you pick a PDF or type/paste text, choose a voice, and click Convert
- Two voice engines to choose from:
  - **Edge-TTS** (needs internet) -- Microsoft's cloud voices. Finnish
    voices Noora and Harri, plus several English voices. This is the
    default and sounds good for most uses
  - **Piper** (works offline) -- downloads a ~60 MB voice model once,
    then works without internet forever. Good if you're on a plane or
    don't want to depend on Microsoft's servers
- Settings for voice, language, speech speed, and per-chapter output
- Finnish/English interface language toggle
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
  - `1300-luvulla` is read as "tuhat kolmesataa luvulla" (not
    "yksi-kolme-nolla-nolla viiva luvulla")
  - `esim.` is read as "esimerkiksi"
  - `5 %` is read as "viisi prosenttia"
  - `sivulta 42` inflects the number to match the case:
    "sivulta neljaltakymmenelta kahdelta"
  - Latin legal terms like `ius commune` are respelled so the engine
    pronounces them correctly

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
  from the command line. Useful for testing individual sentences or
  chunks. Run with `--help` for options
- **`scripts/generate_chatterbox_audiobook.py`** -- full book synthesis
  from PDF to MP3 via Chatterbox. Resumable (safe to Ctrl-C and
  restart)
- **`scripts/generate_audiobook_parallel.py`** -- parallel Edge-TTS
  generator, about 8x faster than the GUI for large books
- **`scripts/record_voice_sample.py`** -- record a voice clip, validate
  its quality, and synthesize text in the cloned voice. Works on both
  Mac and Windows
- **`dev_qwen_tts.py`** -- Qwen3-TTS experiment. **Abandoned** --
  Finnish isn't supported, MPS is broken, CPU is too slow. Kept in the
  repo so nobody re-investigates the same dead end

### Finnish text normalizer

The normalizer is the system that makes Finnish numbers, abbreviations,
and special terms sound natural when read aloud. It runs automatically
when using Chatterbox-Finnish (via the app or dev scripts).

It works as a series of text transformations ("passes") that run before
the text reaches the TTS engine. There are 16 passes covering:

- Century expressions (`1300-luvulla`)
- Year numbers and numeric ranges
- Abbreviations (`esim.`, `prof.`, `jne.`)
- Roman numerals with context-aware ordinal detection
- Unit symbols (`%`, `km`, `kg`)
- Section signs (`\S`)
- Finnish case inflection for numbers after prepositions
- Loanword respelling for words the AI mispronounces
- Various cleanup (ISBN stripping, TOC dot-leaders, metadata)

The normalizer has 400+ unit tests. See
[`docs/tts_text_normalization_cases.md`](docs/tts_text_normalization_cases.md)
for the full inventory of what it handles.

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
│   ├── main.py          # App entry point
│   ├── gui.py           # Tkinter window
│   ├── pdf_parser.py    # PDF text extraction and cleanup
│   ├── tts_base.py      # TTS engine interface
│   ├── tts_edge.py      # Edge-TTS adapter
│   ├── tts_piper.py     # Piper adapter
│   ├── tts_voxcpm.py    # VoxCPM2 adapter (dev only)
│   ├── tts_engine.py    # Text chunking, normalizer, audio combining
│   ├── fi_loanwords.py  # Finnish loanword respelling
│   ├── app_config.py    # Settings persistence
│   └── ffmpeg_path.py   # ffmpeg path helper
├── data/
│   └── fi_loanwords.yaml  # Loanword lexicon
├── tests/                  # Unit tests (400+)
├── scripts/                # CLI tools and setup scripts
├── docs/                   # Documentation and research notes
├── installer/              # Inno Setup build scripts
├── assets/                 # Icons
├── .github/workflows/      # CI: auto-build installer on release
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
