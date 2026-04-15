# Setting up AudiobookMaker to synthesize a book from the command line

This guide gets you from a fresh machine to an MP3 audiobook read
aloud by the "Grandmom" voice. It covers **Windows** and **Linux**,
the two platforms where you can realistically run Chatterbox at speed
(both need an NVIDIA GPU). By the end, one command turns a book file
into an audiobook.

**macOS note:** Apple Macs don't have NVIDIA GPUs and therefore can't
run CUDA. Chatterbox on a Mac falls back to CPU, which is slow enough
(days per novel) to be impractical. If you're on macOS, use the GUI
app with Edge-TTS or Piper instead — those don't need a GPU and work
fine on any platform.

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

Install Python 3.11, Git, and ffmpeg. Pick the block for your OS.

**Windows** (PowerShell as Administrator, winget ships with Win10+):
```powershell
winget install Python.Python.3.11
winget install Git.Git
winget install Gyan.FFmpeg
```
Close and reopen PowerShell afterwards so `PATH` takes effect.

**Linux — Ubuntu / Debian:**
```bash
sudo apt update
sudo apt install python3.11 python3.11-venv python3-pip git ffmpeg
```

**Linux — Arch / Manjaro:**
```bash
sudo pacman -S python git ffmpeg
```

Two things no package manager can install for you:

- **An NVIDIA GPU driver.** Download from
  [nvidia.com/drivers](https://www.nvidia.com/drivers). You need a
  driver recent enough to support CUDA 12 — the release notes on the
  driver page will say. Without a supported driver, Chatterbox falls
  back to CPU and becomes too slow to be practical (days for a
  novel). **You do not need to install the CUDA Toolkit separately.**
  The CUDA runtime libraries come bundled inside the PyTorch wheel
  that `pip install` fetches in step 4. The Toolkit (the full nvcc
  compiler + headers) is only needed if you're compiling CUDA code
  yourself, which we never do.
- **Python's "Add to PATH" setting** (Windows only). Tick the box if
  you use the python.org installer instead of winget.

Quick sanity check before moving on:

```bash
python --version         # -> Python 3.11.x   (try python3 on Linux)
git --version            # -> git version 2.x
ffmpeg -version          # -> ffmpeg version n...
nvidia-smi               # -> GPU + driver table
```

If any of the four fail, fix them before going further. On Linux,
`python` may be Python 2 on older distros — use `python3` (or
`python3.11`) explicitly throughout this guide.

Now the step-by-step. Commands below show Windows first, then the
Linux equivalent where they differ.

**1. Clone the repo and `cd` into it:**

```powershell
git clone https://github.com/MikkoNumminen/AudiobookMaker.git
cd AudiobookMaker
```

**2. Create the main app environment and install its dependencies.**

Windows (PowerShell):
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Linux (bash):
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

This creates a `.venv/` folder inside the repo and installs the app's
Python packages into it. When you see `(.venv)` in your prompt,
you're working inside that environment.

**3. Check whether Chatterbox is already installed somewhere on this
machine.** The GUI's "Install engines" button and earlier passes
through this guide may have already done the setup. Save yourself
15–30 minutes:

```bash
python -c "from src.launcher_bridge import resolve_chatterbox_python; p = resolve_chatterbox_python(); print(p or 'not found')"
```

If this prints a path to a `python` / `python.exe`, **skip step 4**
and use that path wherever this guide says
`.venv-chatterbox\Scripts\python.exe` (Windows) or
`.venv-chatterbox/bin/python` (Linux). If it prints `not found`,
continue to step 4.

**4. Create the Chatterbox environment** (skip if step 3 found one).

Windows has a single setup script that does everything:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_chatterbox_windows.ps1
```

On Linux there's no setup script yet — run the equivalent commands
manually:

```bash
# Create the venv (separate from the main .venv)
python3.11 -m venv .venv-chatterbox
source .venv-chatterbox/bin/activate
pip install --upgrade pip

# PyTorch 2.6.0 with CUDA 12.4 runtime
pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124

# Chatterbox + the extras the Windows script also installs:
pip install chatterbox-tts safetensors num2words silero-vad PyMuPDF pydub

# Optional: Finnish "gemination patch" that reduces stuttering on long
# sequences. Windows applies it automatically. On Linux, open
# .venv-chatterbox/lib/python3.11/site-packages/chatterbox/models/t3/inference/alignment_stream_analyzer.py
# and refer to scripts/setup_chatterbox_windows.ps1 (Step 7/8) for the
# exact edits if you want it applied. It's optional and the synthesis
# works without it.

deactivate  # leave the Chatterbox venv; main .venv is what you use day-to-day
```

On all platforms this is a one-time **15–30 minute** install (PyTorch
is ~3 GB, the Chatterbox models another ~5 GB). HuggingFace caches
the models under `~/.cache/huggingface/hub/`; they're shared across
all projects on the machine so later rebuilds skip the heavy download.

**5. Drop your book in the repo root.** An EPUB, a PDF, or a plain
`.txt` file will all work. Copy `Rubicon.epub` (or whatever book you
have) into the `AudiobookMaker/` folder you cloned in step 1.

**6. Make an output folder.** The synthesis script doesn't create it
for you:

```bash
mkdir out
```

You're now ready to synthesize. Continue to the next section.

## Run it

One command produces an MP3.

Windows (PowerShell — backticks are line-continuations):
```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py `
    --epub Rubicon.epub `
    --out out\rubicon.mp3 `
    --language en `
    --device cuda
```

Linux (bash — backslashes are line-continuations):
```bash
.venv-chatterbox/bin/python scripts/generate_chatterbox_audiobook.py \
    --epub Rubicon.epub \
    --out out/rubicon.mp3 \
    --language en \
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
filename. Each case shows Windows first, then Linux.

**Finnish EPUB:**
```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py --epub Book.epub --out out\book.mp3 --language fi --device cuda
```
```bash
.venv-chatterbox/bin/python scripts/generate_chatterbox_audiobook.py --epub Book.epub --out out/book.mp3 --language fi --device cuda
```

**Finnish PDF:**
```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py --pdf Book.pdf --out out\book.mp3 --language fi --device cuda
```
```bash
.venv-chatterbox/bin/python scripts/generate_chatterbox_audiobook.py --pdf Book.pdf --out out/book.mp3 --language fi --device cuda
```

**English EPUB:**
```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py --epub Book.epub --out out\book.mp3 --language en --device cuda
```
```bash
.venv-chatterbox/bin/python scripts/generate_chatterbox_audiobook.py --epub Book.epub --out out/book.mp3 --language en --device cuda
```

**English PDF:**
```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py --pdf Book.pdf --out out\book.mp3 --language en --device cuda
```
```bash
.venv-chatterbox/bin/python scripts/generate_chatterbox_audiobook.py --pdf Book.pdf --out out/book.mp3 --language en --device cuda
```


## Not a developer?

Install the [ready-made Windows installer](https://github.com/MikkoNumminen/AudiobookMaker/releases/latest)
and use the GUI. Same engines, same Grandmom voice, no command line
needed.
