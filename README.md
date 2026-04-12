# AudiobookMaker

[![Build main installer](https://github.com/MikkoNumminen/AudiobookMaker/actions/workflows/build-release.yml/badge.svg)](https://github.com/MikkoNumminen/AudiobookMaker/actions/workflows/build-release.yml)
[![Build launcher installer](https://github.com/MikkoNumminen/AudiobookMaker/actions/workflows/build-launcher.yml/badge.svg)](https://github.com/MikkoNumminen/AudiobookMaker/actions/workflows/build-launcher.yml)
[![Latest main release](https://img.shields.io/github/v/release/MikkoNumminen/AudiobookMaker?label=main%20installer&color=blue)](https://github.com/MikkoNumminen/AudiobookMaker/releases/latest)
[![Latest launcher release](https://img.shields.io/github/v/release/MikkoNumminen/AudiobookMaker?include_prereleases&label=launcher&color=orange)](https://github.com/MikkoNumminen/AudiobookMaker/releases)
[![License](https://img.shields.io/github/license/MikkoNumminen/AudiobookMaker?color=brightgreen)](LICENSE.txt)

Turn a PDF into an audiobook. Pick a file, press a button, get an MP3.

Works best with Finnish text, but English is supported too.

## Three ways to use AudiobookMaker

There are three ways to use this project, depending on what you need and
what hardware you have. Here's the short version:

| | Main installer | Launcher installer | Developer (clone the repo) |
|---|---|---|---|
| **Who is it for?** | Anyone with a Windows PC | Anyone with a Windows PC and an NVIDIA graphics card | Developers who want to tinker |
| **How do you get it?** | Download one .exe, install, done | Download one .exe, install, done | Clone the repo, set up Python |
| **Voice quality** | Good (Microsoft cloud voices) | Best available (Chatterbox AI + Finnish tuning) | Everything, including experimental engines |
| **Works offline?** | Only with Piper voice | Yes, after first setup | Yes, after first setup |
| **Needs a GPU?** | No | Yes, NVIDIA with 8+ GB memory | Depends on which engine you pick |
| **Voice cloning?** | No | Yes | Yes |
| **Finnish text intelligence?** | Basic | Full (numbers, abbreviations, legal terms read correctly) | Full |
| **Download size** | ~200 MB | ~15 GB (AI models are large) | Varies |

Read on for the details.

---

## Main installer — the simple option

**Best for:** You just want to turn a PDF into an audiobook and don't
have a fancy graphics card.

**What you get:**
- A window where you pick a PDF, choose a voice, and click Convert
- Two voice engines to choose from:
  - **Edge-TTS** (needs internet) — Microsoft's cloud voices. Finnish
    voices Noora and Harri, plus several English voices. This is the
    default and sounds good for most uses
  - **Piper** (works offline) — downloads a ~60 MB voice model once,
    then works without internet forever. Good if you're on a plane or
    don't want to depend on Microsoft's servers
- Settings for voice, language, speech speed, and per-chapter output
- No Python, no GPU, no command line needed

**What you don't get:**
- No Chatterbox engine (the highest-quality Finnish voice)
- No voice cloning (making the audiobook sound like a specific person)
- No Finnish text intelligence (numbers and abbreviations are read
  as-is, which sometimes sounds robotic)

**Download:** [AudiobookMaker v1.0.1](https://github.com/MikkoNumminen/AudiobookMaker/releases/tag/v1.0.1)

**How to install:**
1. Download `AudiobookMaker-Setup-1.0.1.exe`
2. Double-click it. Windows will show a SmartScreen warning because the
   installer isn't signed — click **More info**, then **Run anyway**
3. Click Next a few times, done
4. Open AudiobookMaker from the Start Menu

---

## Launcher installer — the best quality

**Best for:** You have a Windows PC with an NVIDIA graphics card (RTX
3060 or better, 8+ GB video memory) and you want the best possible
Finnish audio quality.

**What you get:**

Everything from the Main installer, plus:

- **Chatterbox-Finnish** — an AI voice engine fine-tuned specifically
  for Finnish. This is the best-sounding option by a wide margin. It
  runs on your graphics card, so it's fast and fully offline after setup
- **Voice cloning** — record a short clip of someone's voice, and the
  audiobook will sound like that person
- **Finnish text intelligence** — the app understands Finnish grammar
  and reads numbers, dates, abbreviations, legal references, and
  loanwords the way a human would. For example:
  - `1300-luvulla` is read as "tuhat kolmesataa luvulla" (not
    "yksi-kolme-nolla-nolla viiva luvulla")
  - `esim.` is read as "esimerkiksi"
  - `5 %` is read as "viisi prosenttia"
  - `sivulta 42` inflects the number to match the case:
    "sivulta neljaltakymmenelta kahdelta"
  - Latin legal terms like `ius commune` are respelled so the AI
    pronounces them correctly

**What you don't get:**
- The full settings window from the Main installer (the Launcher has a
  simpler drop-a-PDF interface instead)

**Download:** [AudiobookMaker Launcher v0.1.0](https://github.com/MikkoNumminen/AudiobookMaker/releases/tag/launcher-v0.1.0)

**How to install:**
1. Download `AudiobookMaker-Launcher-Setup-0.1.0.exe`
2. Double-click it. Same SmartScreen warning as above — click
   **More info**, then **Run anyway**
3. The wizard asks which engines to install. Pick **Full** to get
   Chatterbox (recommended) or **Compact** for Edge-TTS only
4. If you picked Chatterbox: the installer downloads about 15 GB of AI
   model files. This takes 15-45 minutes depending on your internet.
   A progress bar shows what's happening
5. When it's done, drop in a PDF and go

**First audiobook timing:** A 180-page Finnish book takes about 1-2
hours to synthesize on an RTX 3080 Ti. The result is a set of MP3
files, one per chapter, plus one combined full-book MP3.

See [`docs/turo_ohjeet_fi.md`](docs/turo_ohjeet_fi.md) for a
step-by-step Finnish walkthrough aimed at beginners.

### Pre-flight checks

Before downloading anything, the Launcher installer checks that your
machine is ready:

- Windows 10 (version 1809) or newer
- Enough free disk space (2 GB minimum, 16 GB if Chatterbox selected)
- NVIDIA GPU with driver 550+ (only if Chatterbox selected)

If something's wrong, the installer tells you what to fix in plain
Finnish. If you picked Chatterbox but don't have a GPU, it offers to
install just Edge-TTS + Piper instead.

---

## Why the SmartScreen warning?

Both installers are unsigned. Windows shows a scary-looking warning for
every unsigned program from an unknown publisher. Getting rid of this
warning requires a code-signing certificate ($100-300/year), which the
project doesn't have yet.

The installers are safe — their entire build process is open source
in this repository and runs automatically on GitHub's servers on every
release. You can read every line of code that goes into them.

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

**Edge-TTS** and **Piper** work the same as in the installers.

**Chatterbox-Finnish** needs a separate venv because it has heavy
dependencies (PyTorch, CUDA). The setup script handles everything:

```bash
powershell -ExecutionPolicy Bypass -File scripts\setup_chatterbox_windows.ps1
```

This creates `.venv-chatterbox/`, installs CUDA-enabled PyTorch,
downloads the AI models (~5 GB), and applies necessary patches.

**VoxCPM2** is an experimental engine from OpenBMB. It supports voice
cloning and natural-language voice design ("warm baritone elderly
male"). Not tested thoroughly — install with `pip install voxcpm` if
you want to experiment. Requires NVIDIA GPU with ~8 GB VRAM.

### Developer scripts

These are standalone tools at the repo root and in `scripts/`. They
are not part of the shipped installers.

- **`dev_chatterbox_fi.py`** — synthesize text with Chatterbox-Finnish
  from the command line. Useful for testing individual sentences or
  chunks. Run with `--help` for options
- **`scripts/generate_chatterbox_audiobook.py`** — full book synthesis
  from PDF to MP3 via Chatterbox. Resumable (safe to Ctrl-C and
  restart). This is what the Launcher uses under the hood
- **`scripts/generate_audiobook_parallel.py`** — parallel Edge-TTS
  generator, about 8x faster than the GUI for large books
- **`scripts/record_voice_sample.py`** — record a voice clip, validate
  its quality, and synthesize text in the cloned voice. Works on both
  Mac and Windows
- **`dev_qwen_tts.py`** — Qwen3-TTS experiment. **Abandoned** —
  Finnish isn't supported, MPS is broken, CPU is too slow. Kept in the
  repo so nobody re-investigates the same dead end

### Finnish text normalizer

The normalizer is the system that makes Finnish numbers, abbreviations,
and special terms sound natural when read aloud. It runs automatically
when using Chatterbox-Finnish (via the Launcher or dev scripts).

It works as a series of text transformations ("passes") that run before
the text reaches the TTS engine. There are 16 passes covering:

- Century expressions (`1300-luvulla`)
- Year numbers and numeric ranges
- Abbreviations (`esim.`, `prof.`, `jne.`)
- Roman numerals with context-aware ordinal detection
- Unit symbols (`%`, `km`, `kg`)
- Section signs (`§`)
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
│   ├── launcher.py      # Launcher window
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
├── .github/workflows/      # CI: auto-build installers on release
└── requirements.txt
```

## Limitations

- Edge-TTS needs an internet connection (it uses Microsoft's servers)
- Piper needs internet once to download each voice model (~60 MB)
- Chatterbox needs an NVIDIA GPU with 8+ GB video memory
- Scanned PDFs (where text is actually an image) don't work — the text
  must be selectable in a PDF reader
- The Finnish normalizer is tuned for legal/historical prose. Other
  domains may have terms it doesn't handle yet

## License

MIT
