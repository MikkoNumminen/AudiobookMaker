# AudiobookMaker

Converts PDF files into audiobooks. Load a PDF, press a button, get an MP3.

## Features

- Automatic PDF text extraction and cleanup (page numbers, headers, footers)
- Automatic chapter detection
- Text-to-speech via edge-tts (Finnish and English voices)
- Single combined MP3 or one file per chapter
- Simple Tkinter GUI
- Windows installer — no Python or other dependencies required

## Installation (end users)

1. Download `AudiobookMaker-Setup.exe` from the Releases page
2. Double-click and follow the prompts
3. Find the app in the Start Menu

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
├── assets/              # Icon and other resources
├── installer/           # Inno Setup script
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
