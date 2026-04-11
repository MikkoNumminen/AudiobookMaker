# AudiobookMaker

Converts PDF files into audiobooks. Load a PDF, press a button, get an MP3.

## Features

- Automatic PDF text extraction and cleanup (page numbers, headers, footers)
- Robust text cleaning: strips soft hyphens, fixes line-wrap hyphenation,
  flattens in-paragraph line wraps, preserves compound hyphens
- Automatic chapter detection
- Text-to-speech via edge-tts (Finnish and English voices: Noora, Harri,
  Jenny, Aria, Ava, Guy, Andrew, Sonia, Ryan)
- Context-aware sentence splitter that handles Finnish and English
  abbreviations, initials, decimals, and domain names
- Silence trimming between chunks for seamless audio
- Single combined MP3 or one file per chapter
- Simple Tkinter GUI
- Parallel CLI generator for large books
- Windows installer — no Python or other dependencies required

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
3. Choose language (Finnish / English)
4. Adjust speech rate if needed
5. Click **Convert** — the progress bar shows status
6. Save the MP3

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

## Project structure

```
AudiobookMaker/
├── src/
│   ├── pdf_parser.py    # PDF parsing and text cleaning
│   ├── tts_engine.py    # edge-tts integration and audio combining
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
| Text-to-speech | edge-tts |
| Audio processing | pydub + ffmpeg |
| GUI | Tkinter |
| Windows packaging | PyInstaller |
| Installer | Inno Setup |

## Limitations

- edge-tts uses Microsoft's servers — requires an internet connection
- Scanned PDFs (image-based) are not supported — text must be selectable
- Text cleanup heuristics may not work perfectly for all PDF formats

## License

MIT
