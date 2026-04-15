# Setting up AudiobookMaker to synthesize a book from the command line

This guide gets you from a fresh Windows PC to an MP3 audiobook read
aloud by the "Grandmom" voice. It assumes you have an NVIDIA graphics
card and are comfortable with a terminal. By the end, one command
turns a book file into an audiobook.

## Why two Python environments?

AudiobookMaker ships three TTS engines. Two of them (Edge-TTS, Piper)
are small. The third — Chatterbox, the high-quality one this guide
uses — drags in PyTorch, CUDA, and several gigabytes of model weights.
Mixing all of that into the main app environment would make it huge
and fragile.

So the project uses two Python environments: a small one for the app
itself, and a large one just for Chatterbox. When the GUI wants to
synthesize with Chatterbox, it launches a subprocess using the large
env's interpreter. You'll create both below.

## Install once

Install these manually, once per machine:

- **Python 3.11** from python.org. Tick "Add Python to PATH".
- **Git for Windows**.
- **An NVIDIA GPU** with a recent CUDA 12+ driver. Without one
  Chatterbox falls back to CPU, which is too slow to use in practice
  (days for a novel, not hours).
- **ffmpeg and ffprobe** on PATH, or copied into `dist/ffmpeg/` inside
  the cloned repo. Grab them from ffmpeg.org or gyan.dev.

Then clone and set up both environments:

```powershell
git clone https://github.com/MikkoNumminen/AudiobookMaker.git
cd AudiobookMaker

# Main app environment
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Chatterbox environment (15-30 min — heavy PyTorch + model downloads)
powershell -ExecutionPolicy Bypass -File scripts\setup_chatterbox_windows.ps1
```

The Chatterbox script creates `.venv-chatterbox\`, installs CUDA-
enabled PyTorch, and downloads the speech models from HuggingFace.
Models go into `~/.cache/huggingface/hub/` and are shared across all
projects on the machine, so if you ever rebuild the venv the model
download won't repeat.

**Already have Chatterbox installed on this machine?** Check before
running the setup script — the GUI's "Install engines" button and
earlier passes through this guide may have already done the work.

```powershell
python -c "from src.launcher_bridge import resolve_chatterbox_python; p = resolve_chatterbox_python(); print(p or 'not found')"
```

If it prints a path to a `python.exe`, skip the Chatterbox setup and
use that path wherever this guide says `.venv-chatterbox\Scripts\python.exe`.

## Run it

Drop your book (`.epub`, `.pdf`, or plain `.txt`) in the repo root.
Then one command produces an MP3:

```powershell
mkdir out
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py `
    --epub Rubicon.epub `
    --out out\rubicon.mp3 `
    --language en `
    --device cuda
```

The backticks at the end of each line tell PowerShell the command
continues on the next line.

What the script does: parses the book, splits each chapter into
chunks of about 300 characters, sends each chunk to the model, and
stitches the output WAVs into one MP3. Every chunk gets cached to
disk as it's produced — if you Ctrl-C and re-run the same command, it
picks up where it left off. Useful for long books where a power blip
or Windows Update could otherwise send you back to zero.

What the flags mean:

- **`--epub` / `--pdf` / `--text-file`** — the input. Swap based on
  format; the script has a dedicated parser for each.
- **`--out`** — where the final MP3 lands.
- **`--language fi` or `en`** — which model pipeline to use (see
  next section).
- **`--device cuda`** — use the GPU.

Progress lines look like:

```
[chapter 1/1] chunk 31/389 (31/389 total) - 6m13s elapsed, ~1h11m remaining, RTF 1.01x
```

RTF is the "real-time factor" — wall-clock seconds divided by output
audio seconds. RTF 1.0 means one minute of synthesis produces one
minute of listening. Below 1.0, the GPU is faster than real time.

## Picking the language and voice

Two choices: the language, and optionally a custom voice. The default
voice is **Grandmom** — the warm elderly narrator that comes with the
project.

| Goal | Flags |
|---|---|
| Finnish book, Grandmom | `--language fi` |
| English book, Grandmom | `--language en` |
| Any language, your own voice | `--language {fi,en} --ref-audio voice.wav` |

`--language fi` loads the Finnish fine-tuned model on top of the base
multilingual Chatterbox. Reads Finnish with native phonemes.

`--language en` loads only the base multilingual model — no Finnish
finetune — and uses a bundled reference clip to clone Grandmom's
timbre. Reads English with native English phonemes.

`--ref-audio path\to\voice.wav` replaces Grandmom with whatever voice
is in the clip. Record 10–20 seconds of clean speech, 24 kHz mono,
no background noise, save as WAV, point at it.

**Match the language flag to the text's language.** Crossed wires
(`--language en` on Finnish text, or vice versa) still produce audio,
but the model tries to read the text as if it were the other
language. Charming in a specific way, not what you want.

## Quick-test a book without running the full thing

A novel takes hours. When you just want to confirm everything works,
add `--chapters N,M` to synthesize only those chapters:

```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py `
    --epub Rubicon.epub `
    --chapters 10,11 `
    --out out\test.mp3 `
    --language en --device cuda
```

To see the chapter list first — so you know which indices are real
chapters and which are front matter:

```powershell
python -c "from src.epub_parser import parse_epub; b = parse_epub('Rubicon.epub'); [print(i, repr(c.title), len(c.content), 'chars') for i, c in enumerate(b.chapters)]"
```

For a PDF, use `src.pdf_parser` and `parse_pdf` instead. The first
several entries are usually title pages, copyright notices, and
acknowledgements; the big ones that come after are the real chapters.

## Other useful flags

- **`--dry-run`** — parse, chunk, and estimate only, no GPU work.
  Useful for answering "how long would this take?" before committing
  to an overnight run.
- **`--device cpu`** — runs without a GPU. 20× slower; really only
  usable for tiny test snippets.
- **`--no-resume`** — ignore any cached chunks and start fresh.
- **`--chunks-per-chapter N`** — hard cap on chunks per chapter,
  handy for quick slicing.

## Expected timing on an RTX 3080 Ti

| Input | Audio out | Wall time |
|---|---|---|
| 10-min excerpt | 10 min | ~12 min |
| 1-hour chapter | 1 h | ~1 h 10 min |
| 10-hour novel | 10 h | ~11 h 30 min |

Other GPUs scale roughly with their tensor compute. Below an RTX
3060, things start to hurt. Below an RTX 2060, it's not practical.

## Cheatsheet — four copy-paste commands

Once the environments are set up, these four commands cover the
common cases. Replace `Book.epub` / `Book.pdf` with your actual
filename.

**Finnish EPUB:**
```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py --epub Book.epub --out out\book.mp3 --language fi --device cuda
```

**Finnish PDF:**
```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py --pdf Book.pdf --out out\book.mp3 --language fi --device cuda
```

**English EPUB:**
```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py --epub Book.epub --out out\book.mp3 --language en --device cuda
```

**English PDF:**
```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py --pdf Book.pdf --out out\book.mp3 --language en --device cuda
```

## Not a developer?

Install the [ready-made Windows installer](https://github.com/MikkoNumminen/AudiobookMaker/releases/latest)
and use the GUI. Same engines, same Grandmom voice, no command line
needed.
