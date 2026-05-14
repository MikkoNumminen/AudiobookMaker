# AudiobookMaker

Turn PDFs, EPUBs, and scanned books into audiobooks. Load a file, pick a voice, get an MP3.

Finnish-first, English supported. Windows installer for end users; full Python source for developers who want voice cloning, OCR fallback, and Chatterbox.

## Why this exists

Reading a 400-page PDF takes hours during which you can do nothing else. Listening to one takes the same hours but your hands and eyes are free — dishes, commute, walk. Commercial audiobook services don't carry the specific Finnish legal texts, niche academic papers, or that PDF a colleague sent you last week. This bridges the gap.

## Quick start

1. Download `AudiobookMaker-Setup-X.X.X.exe` from [Releases](https://github.com/MikkoNumminen/AudiobookMaker/releases).
2. Run it. Windows will warn it's from an unknown publisher — click **More info** → **Run anyway**.
3. Open the app, pick a PDF, pick a voice, click **Convert**.

That's the whole flow. Read on if you want to know what's underneath, or if you want to do something more interesting like clone your own voice into a Finnish reading.

## Hear it first

These clips were made by AudiobookMaker using the "Grandmom" voice
(Chatterbox engine). Both source texts are **public domain** — their
copyrights expired long ago because the authors died over 70 years
ago. We picked well-known classics so you can judge the voice quality
on text you might recognise.

**Finnish — Aleksis Kivi, *Seitsemän veljestä* (1870)**

https://github.com/MikkoNumminen/AudiobookMaker/raw/master/assets/demos/finnish_grandmom_kivi.mp3

**English — Edward Gibbon, *The Decline and Fall of the Roman Empire* (1776)**

https://github.com/MikkoNumminen/AudiobookMaker/raw/master/assets/demos/english_grandmom_gibbon.mp3

## Pick your engine

Four TTS engines, each with different tradeoffs:

| Engine | Quality | Offline | GPU | Voice cloning | Best for |
|---|---|---|---|---|---|
| **Edge-TTS** | Excellent | No | No | No | Default. Fast, free, great Finnish voices. |
| **Piper** | Good | Yes | No | No | Privacy-sensitive content, no internet, older machines. |
| **Chatterbox** | Excellent | Yes | NVIDIA | Voice packs | Grandmom / Isoäiti voice. Best Finnish quality. One-time in-app install (~15 GB). |
| **VoxCPM2** | Variable | Yes | NVIDIA | Zero-shot | Voice design from text descriptions, experimentation. Developer setup only. |

Edge-TTS is the default and what most users want. Don't overthink it unless you have a specific reason.

### Edge-TTS (online, default)

Microsoft's free online neural TTS. Fast, no model downloads, Finnish voices Noora and Harri are the best in their class. Requires an internet connection during synthesis. If Microsoft ever deprecates this service, all of us are in trouble — but it has been stable for years.

- Finnish voices: Noora, Harri
- English US voices: Jenny, Aria, Ava, Guy, Andrew
- English GB voices: Sonia, Ryan
- No GPU. No voice cloning.

### Piper (offline, CPU)

Local neural TTS that runs entirely on CPU. After the first voice download (~60 MB per voice, stored in `~/.audiobookmaker/piper_voices/`), no internet needed. The Finnish voice (Harri) has different pronunciation quirks than Edge-TTS's Harri — sometimes better for specific words, sometimes worse. Try both on a sample chapter and pick.

- Finnish voice: Harri
- English US voices: Lessac, Ryan-high
- English GB voice: Alan
- No GPU. No voice cloning.

### Chatterbox + Finnish-NLP/Chatterbox-Finnish (NVIDIA GPU)

The interesting one. Chatterbox is an open-source neural TTS from Resemble AI; `Finnish-NLP/Chatterbox-Finnish` is a Finnish fine-tune by the Finnish-NLP organization. Together they produce the best Finnish voice quality in this app — close to commercial audiobook quality on prepared text — and they support voice cloning from a short reference clip.

**How end users get Chatterbox.** The model weights (~15 GB) aren't bundled into the installer — they would balloon it past usability. Instead, open the app, click **Install engines…** in the Settings panel, and the GUI downloads the Chatterbox venv and the Finnish-NLP model on demand. After that initial setup, Chatterbox works fully offline. The default voice is **Grandmom** (Isoäiti in the app), a warm elderly speaker.

**How developers get voice cloning.** The voice-cloning pipeline that produces a custom voice pack from a sample of your own voice (analyze → export → train → package) lives in `scripts/voice_pack_*.py` and is **dev-only** — it needs a `HF_TOKEN` and a Python environment with CUDA. See [docs/DEVELOPER_SETUP.md](docs/DEVELOPER_SETUP.md). The GUI consumes the resulting voice packs via its **Import voice pack…** button; it does not produce them.

**Honest expectations:**

- Quality of a cloned voice depends heavily on the reference recording. `scripts/record_voice_sample.py` enforces an audio preflight: input volume ~85%, loudness in a healthy dBFS band, SNR 40+ dB, 12–20 second length. Skipping the preflight produces worse cloning. Do not skip it.
- Use it on voices you have the right to use. See [A note on voice cloning](#a-note-on-voice-cloning) at the bottom.

### VoxCPM2 (developer install only, NVIDIA GPU)

Open-source neural TTS from OpenBMB. Supports 30 languages including Finnish, runs locally, and offers two features the others don't:

- **Zero-shot voice cloning** from a short reference audio clip.
- **Voice design** — describe the desired voice in natural language (e.g. `warm baritone elderly male`) and the model steers toward that description.

**Honest expectations:**

- VoxCPM2's Finnish has not been A/B tested against Chatterbox-Finnish or Edge-TTS Noora by the project maintainer. Try a sample chapter on each before committing to one for a whole book.
- Voice description prompts work for broad characteristics (gender, age, tone). Specific ethnic accents across language boundaries (e.g. "African American accent reading Finnish") are well outside what any current open-source multilingual TTS handles reliably. For stronger persona matching, use voice cloning with a reference clip.

To install:

```bash
pip install voxcpm
```

Requires Python ≥ 3.10, PyTorch ≥ 2.5 with CUDA ≥ 12.0, NVIDIA GPU with ~8 GB VRAM. No CPU fallback. On machines without a CUDA GPU (including all Macs), the engine appears in the dropdown but reports itself as unavailable; Edge-TTS and Piper keep working normally.

## Features

- **PDF text extraction** via PyMuPDF, with cleanup heuristics: strips soft hyphens, fixes line-wrap hyphenation, flattens in-paragraph wraps, preserves compound hyphens.
- **EPUB support** via ebooklib + BeautifulSoup.
- **OCR fallback for scanned PDFs** via ocrmypdf + Tesseract. If PyMuPDF can't extract selectable text (because the PDF is image-based), the app falls back to OCR. The Windows installer bundles Tesseract + Finnish and English language packs; developers install Tesseract separately. Details: [docs/OCR_FALLBACK.md](docs/OCR_FALLBACK.md).
- **Automatic chapter detection.**
- **Context-aware sentence splitter** handling Finnish and English abbreviations, initials, decimals, and domain names.
- **Finnish text normalizer** with 16 normalization passes covering `-ismi` / `-tio` stems, abbreviations, ordinals, Latin phrases, Roman numerals, compound-word seam splitting, and acronym handling. 400+ unit tests for this module alone. The reason this is here at all is that Finnish TTS pronunciation gets weird in predictable ways, and the normalizer is what makes the difference between "robotic" and "audiobook-grade".
- **Preview button** — auditions an engine + voice on short text before committing to a full conversion. Use it. A 6-hour conversion in the wrong voice is no fun.
- **Make sample** — synthesizes only the first ~30 seconds and saves it next to where the full run would land, so you can A/B engines or voices in under a minute.
- **Import voice pack** — point the GUI at a voice pack folder produced by the dev-only voice-cloning pipeline and the cloned voice appears in the Voice dropdown.
- **Voice design text field** for natural-language voice direction (VoxCPM2 only).
- **Session memory** — remembers last-used engine, voice, language, speed, reference audio, and voice description between runs (`~/.audiobookmaker/config.json`).
- **Silence trimming between chunks** for seamless playback.
- **Single combined MP3 or one file per chapter** (one-per-chapter currently Edge-TTS only).
- **CustomTkinter GUI** (modern Tk theme).
- **Built-in CLI** for batch conversion, scripting, and headless use. Full reference: [docs/CLI.md](docs/CLI.md).
- **In-app auto-update** — when a new release ships, a banner appears at the top of the window. Click **Update now** and the app handles download, hash verification, and reinstall in one step.
- **Windows installer** — no Python, no ffmpeg, no other dependencies needed for end users. Everything is bundled.

## End-user installation

1. Download `AudiobookMaker-Setup-X.X.X.exe` from [Releases](https://github.com/MikkoNumminen/AudiobookMaker/releases).
2. Double-click it.
3. Windows shows a **"Windows protected your PC"** SmartScreen warning because the installer isn't code-signed. Click **More info** → **Run anyway**.
4. Click through the installer prompts (Next → Next → Install).
5. Find AudiobookMaker in the Start Menu.

Nothing else to install for the basic flow. Edge-TTS and Piper work out of the box. Tesseract for scanned PDFs is bundled.

**Already have an older version?** The app checks for updates automatically. When a new version is available, a banner appears at the top of the window — click **Update now** and the app handles the download, SHA-256 verification, and reinstall. No manual downloads, no installer prompts.

For **Chatterbox** voice cloning, open the GUI and click **Install engines…** in the Settings panel. The app downloads the Chatterbox venv + Finnish-NLP model on demand (one-time ~15 GB). After that it works fully offline.

For **VoxCPM2** or the dev-only voice-cloning pipeline (analyze / export / train / package), use the [Development setup](#development-setup) instead.

For scanned PDFs on a **developer install** (Tesseract is bundled in the released `.exe`), install Tesseract separately:

- **Windows:** Download from [tesseract-ocr.github.io](https://tesseract-ocr.github.io/tessdoc/Installation.html), and during install, tick the Finnish language pack.
- **macOS:** `brew install tesseract tesseract-lang`
- **Linux:** `apt install tesseract-ocr tesseract-ocr-fin`

### Why the SmartScreen warning?

Windows flags every unsigned installer from unknown publishers, regardless of whether the file is actually malicious. Silencing the warning requires a paid code-signing certificate ($100–300/year), which this project doesn't have. The installer is safe to run; its full source (PyInstaller spec + Inno Setup script + GitHub Actions build) lives in this repo and rebuilds automatically on every tagged release.

If you don't trust an unsigned installer (a reasonable default), build it yourself: see [BUILDING.md](BUILDING.md).

## Usage

1. Open AudiobookMaker.
2. Click **Select book file** and pick your PDF, EPUB, or `.txt`.
3. Choose TTS engine from the dropdown. **Edge-TTS** is the default and the right answer unless you have a specific reason otherwise.
4. Pick **Language** (Finnish / English).
5. Pick a **Voice**.
6. Click **Preview** to hear a short clip. If you don't like it, change it now, not after a 6-hour conversion.
7. (Chatterbox / VoxCPM2 only) Optionally **Import voice pack** if you have one from the dev-only voice-cloning pipeline, or provide a reference audio clip (VoxCPM2 zero-shot), and/or a voice description like `warm baritone elderly male` (VoxCPM2).
8. Adjust speech rate if needed.
9. Click **Convert**. Progress bar updates as it runs.
10. Save the MP3 (or use the **Open folder** button that appears when done).

## Limitations

- **Edge-TTS needs Microsoft's servers.** No internet, no Edge-TTS. Switch to Piper.
- **OCR is fallback, not first-class.** Tesseract handles scanned PDFs but it's slower and less accurate than native text extraction. If your PDF has selectable text (try Ctrl+A in any PDF reader), you'll get better results with PyMuPDF doing the work.
- **PDF cleanup heuristics aren't perfect.** Multi-column academic papers, unusual layouts, and PDFs with embedded tables can break them. Run **Make sample** on one chapter before committing to a 400-page book.
- **"One MP3 per chapter" works only with Edge-TTS currently.** Piper, Chatterbox, and VoxCPM2 produce a single combined MP3.
- **GPU engines need an NVIDIA card with ~8 GB VRAM and CUDA 12+.** No CPU fallback exists. On unsupported machines, these engines show as unavailable in the dropdown and the rest of the app keeps working normally.
- **Voice cloning quality depends on the reference clip.** A noisy reference produces a noisy clone. The audio preflight check exists for a reason; don't bypass it.

## Command-line use

AudiobookMaker ships a built-in CLI for batch conversion, scripting, and headless use. Full reference: [docs/CLI.md](docs/CLI.md).

```
python -m src.cli --help
python -m src.cli doctor
python -m src.cli convert book.pdf
```

Every subcommand supports `--json` for machine-readable output and `--quiet` for script-friendly minimal output. Run `python -m src.cli <command> --help` for per-command flags.

## Development setup

Required:

- **Python 3.11 or newer.** Older versions break some Tkinter / CustomTkinter dependencies.
- **ffmpeg on PATH** (or in `dist/ffmpeg/` for packaged builds).
- **For Chatterbox / VoxCPM2:** NVIDIA GPU, CUDA 12+, ~8 GB VRAM. No CPU path.
- **For OCR as a developer:** Tesseract on PATH plus the relevant language packs (`fin` for Finnish). End users get this bundled.

```bash
git clone https://github.com/MikkoNumminen/AudiobookMaker
cd AudiobookMaker
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m src.main
```

For **Chatterbox** developer setup, see [docs/DEVELOPER_SETUP.md](docs/DEVELOPER_SETUP.md). A `.env` at the repo root containing `HF_TOKEN=...` is needed to download gated Hugging Face models.

For **VoxCPM2**:

```bash
pip install voxcpm
```

Several GB of model weights download on first use.

Run tests:

```bash
pytest tests/
```

A handful of tests skip automatically when `ffmpeg` isn't on PATH (audio export tests). To run all tests, install ffmpeg first.

See [BUILDING.md](BUILDING.md) for full Windows installer build instructions.

## Upstream contribution

A patch for a memory-handler hook leak in resemble-ai/chatterbox lives in `docs/upstream/chatterbox/`:

- `repro_hook_leak.py` — minimal reproducer demonstrating the leak.
- `hook_leak_fix.patch` — the proposed fix.
- `BUG_REPORT.md` — write-up for upstream submission.

Status: prepared for submission to the upstream maintainers.

## Project structure

```
AudiobookMaker/
├── src/
│   ├── pdf_parser.py              # PDF parsing, OCR fallback
│   ├── tts_base.py                # Abstract TTSEngine interface + registry
│   ├── tts_edge.py                # Edge-TTS engine
│   ├── tts_piper.py               # Piper offline TTS engine
│   ├── tts_chatterbox_bridge.py   # Chatterbox + Finnish-NLP/Chatterbox-Finnish
│   ├── tts_voxcpm.py              # VoxCPM2 GPU engine (dev only)
│   ├── tts_normalizer_fi.py       # Finnish text normalizer (16-pass)
│   ├── tts_normalizer_en.py       # English text normalizer
│   ├── app_config.py              # Session preference persistence
│   ├── gui_unified.py             # CustomTkinter UI (main entry)
│   ├── cli/                       # Command-line interface (docs/CLI.md)
│   ├── ffmpeg_path.py             # Runtime ffmpeg path helper for bundled builds
│   └── main.py                    # Application entry point
├── scripts/                       # CLI tools, voice sample recorder, voice-pack pipeline
├── tests/                         # Unit tests (2000+)
├── docs/                          # Architecture, CLI, OCR, conventions, audits
├── installer/                     # Inno Setup script
├── .github/workflows/             # CI: build Windows installer and publish releases
├── assets/                        # Icon and other resources
├── .claude/skills/                # Reusable procedures for the project (see docs/SKILLS_AUDIT.md)
└── requirements.txt
```

## Tech stack

| Component | Library |
|---|---|
| PDF parsing | PyMuPDF |
| EPUB parsing | ebooklib + beautifulsoup4 |
| OCR | ocrmypdf + Tesseract (system binary) |
| Online TTS | edge-tts |
| Offline CPU TTS | piper-tts (ONNX Runtime) |
| GPU TTS | Chatterbox, VoxCPM2 (PyTorch + CUDA) |
| Audio processing | pydub + ffmpeg |
| In-process audio playback | pygame |
| GUI | CustomTkinter |
| Finnish text normalization | num2words + custom 16-pass normalizer |
| Windows packaging | PyInstaller |
| Installer | Inno Setup |

## License

MIT. See [LICENSE.txt](LICENSE.txt).

## A note on voice cloning

The voice cloning capability in this project is a tool. Tools can be used well or badly. This project assumes you'll use it well: cloning **your own** voice for your own reading, cloning a public-domain voice for a public-domain text, or cloning a voice from someone who has explicitly consented.

Cloning someone's voice without consent is harmful and illegal in many jurisdictions, regardless of what's technically possible. The bar for "yes, this is fine" is higher than "I really want to do this." If you're unsure, ask first or use Edge-TTS instead.

The voice-cloning **pipeline** (analyze → train → package) is intentionally kept out of the end-user installer. This isn't a packaging accident — it's a choice. The people most likely to do real harm are those who download a one-click installer and click around; the people most likely to do useful work are those willing to set up Python, install CUDA, read a README, and record their own voice. The friction is on purpose. The GUI consumes the resulting voice packs but does not produce them.
