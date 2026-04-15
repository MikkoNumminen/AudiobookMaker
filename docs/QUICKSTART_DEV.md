# Setting up AudiobookMaker to synthesize a book from the command line

This guide assumes a Windows PC with an NVIDIA graphics card, and a
developer comfortable with a terminal. At the end you'll have produced
an English audiobook spoken by the "Grandmom" voice, straight from the
command line.

## Why two Python environments?

AudiobookMaker ships with three text-to-speech engines. Two of them
(Edge-TTS and Piper) are small and can live with the app's regular
Python packages. The third one, Chatterbox, drags in PyTorch, CUDA, and
several gigabytes of AI model weights. Mixing all of that into one
environment would make the app's main installer huge and fragile.

So the project uses a split: a regular venv for the app itself, and a
separate venv dedicated to Chatterbox. When the GUI wants to synthesize
with Chatterbox, it launches a subprocess using the Chatterbox venv's
Python interpreter. You'll set both up below.

## What you need installed first

Install these manually, once per machine:

- **Python 3.11** from python.org. During installation, tick the
  "Add Python to PATH" checkbox — otherwise the `python` command won't
  be found in your terminal.
- **Git for Windows**. You'll use this to clone the repository and to
  pull future updates.
- **An NVIDIA graphics card** with a recent driver (CUDA 12 or newer).
  Chatterbox runs the speech synthesis on the GPU, and without one it
  falls back to CPU mode that's too slow to be useful — hours for what
  should take minutes.
- **ffmpeg and ffprobe**. These two tools handle the audio file
  combining. Grab them from ffmpeg.org or from a trusted Windows build
  like gyan.dev. You need the two `.exe` files somewhere the app can
  find them — either on your system PATH, or copied into a
  `dist/ffmpeg/` folder inside the cloned repo.

## Cloning and setting up the main app

Open PowerShell and run:

```powershell
git clone https://github.com/MikkoNumminen/AudiobookMaker.git
cd AudiobookMaker
```

Now create the regular Python environment and install the app's
dependencies:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

The `.venv` folder is where the app's Python packages live — it's local
to the project, so you can delete it and start over without affecting
other Python projects on your machine. When you see `(.venv)` in your
prompt, you're working inside that environment.

## Setting up the Chatterbox environment

Chatterbox has its own setup script because it needs a special version
of PyTorch compiled for CUDA, plus several large model downloads. Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_chatterbox_windows.ps1
```

The `-ExecutionPolicy Bypass` part tells Windows to allow this
particular script to run even if your system is configured to block
unsigned PowerShell scripts. It only affects this one invocation.

The script will:

1. Create a second virtual environment called `.venv-chatterbox`.
2. Install PyTorch with CUDA support.
3. Install Chatterbox itself.
4. Download the Finnish-NLP fine-tuned model (around 500 MB) and the
   underlying multilingual model (around 7 GB) from HuggingFace into a
   cache folder.

This takes 15 to 30 minutes depending on your internet and disk speed.
The models land in `~/.cache/huggingface/hub/` and are shared across
all projects on the same machine, so if you ever delete and recreate
`.venv-chatterbox` the model download won't repeat.

## Picking a book

Put the book you want to read aloud somewhere the project can see it.
Dropping it in the repository root is fine. The command-line script
accepts three input formats:

- `.epub` — modern ebook format, cleanest to parse
- `.pdf` — works but PDFs with weird layouts, scanned pages, or
  footnotes can confuse the text extraction
- `.txt` — plain text, if you've already cleaned it up yourself

For this walkthrough, we'll use an EPUB. Say it's called `Rubicon.epub`.

## Extracting a chunk to test with

Full audiobooks take hours to synthesize — a typical novel might be 10
hours of GPU time. For your first run you'll want something smaller to
confirm the setup works. Let's extract the first two chapters, which
will produce roughly an hour of audio.

The project has a helper to parse an EPUB into a list of chapters. We
can use it from Python to write the chapters we want into a plain text
file:

```powershell
python -c "from src.epub_parser import parse_epub; b = parse_epub('Rubicon.epub'); open('my_input.txt','w',encoding='utf-8').write(b.chapters[10].content + '\n\n' + b.chapters[11].content)"
```

This opens the book, grabs the 11th and 12th items in its internal
chapter list (the first ten are usually front matter — title pages,
copyright notices, acknowledgements, things like that), concatenates
them, and writes the result to `my_input.txt`. You can open that file
in any text editor to sanity-check what you extracted.

Every book is different, so the exact indices might be off. You can
print the chapter list first:

```powershell
python -c "from src.epub_parser import parse_epub; b = parse_epub('Rubicon.epub'); [print(i, repr(c.title), len(c.content), 'chars') for i, c in enumerate(b.chapters)]"
```

That lists every chapter, its title, and how many characters it
contains. The first real narrative chapters are usually the biggest
ones after the front matter.

## Running the synthesis

Make a folder for the output, then invoke the synthesis script using
the Chatterbox environment's Python:

```powershell
mkdir out
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py `
    --text-file my_input.txt `
    --out out\book.mp3 `
    --language en `
    --device cuda
```

The backticks at the end of each line tell PowerShell that the command
continues on the next line. What the flags do:

- **`--text-file my_input.txt`** — the text to read aloud.
  Alternatively `--pdf` or `--epub` with a path to the original file,
  and the script will parse it for you.
- **`--out out\book.mp3`** — where to save the final MP3.
- **`--language en`** — the language to synthesize in. Setting this to
  `en` tells the script to use the base multilingual Chatterbox model
  (which handles English natively) together with a pre-recorded
  Grandmom reference clip that ships with the project. The result is
  native-sounding English in the Grandmom voice. If you leave this off
  or set it to `fi`, you get the Finnish fine-tuned model, which
  produces lovely Finnish but very accented English.
- **`--device cuda`** — tell PyTorch to use the NVIDIA GPU. If you set
  this to `cpu` it'll still work, but expect a 20× slowdown.

The script loads the model (about ten seconds on a warm cache), splits
your text into chunks of about three hundred characters each, and
synthesizes them one by one. Every few seconds you'll see a progress
line in the terminal like:

```
[chapter 1/1] chunk 31/389 (31/389 total) - 6m13s elapsed, ~1h11m remaining, RTF 1.01x
```

`RTF` is the "real-time factor" — how much wall-clock time the GPU
needs per second of output audio. An RTF of 1.0 means it takes one
minute of synthesis to produce one minute of listening. Numbers under 1
mean the GPU is faster than real-time; numbers over 1 mean slower.

When the script finishes, the individual chunks are stitched together
with ffmpeg and saved as a single MP3 at the path you specified.

## If you interrupt the script

Every chunk gets cached to disk as it's produced. If you have to stop
and restart, re-running the same command picks up where it left off —
no work is lost. This matters for long books, where a power blip or a
Windows Update popup would otherwise send you back to zero.

If you want a clean run instead, either delete the output folder or
pass `--no-resume`.

## What other flags exist

| Flag | What it does |
|---|---|
| `--dry-run` | Parse, chunk, and estimate only — no GPU work. Useful for checking "how long would this take?" |
| `--ref-audio PATH` | Use your own reference audio instead of Grandmom. You record a WAV of somebody's voice (10–20 seconds, clean, no background noise, 24 kHz mono) and point at it, and Chatterbox will imitate that voice instead. |
| `--chapters 1,3,5` | Only synthesize specific chapter indices. Handy when the first run is good but chapter 4's audio came out glitchy. |
| `--chunks-per-chapter N` | Cap the number of chunks per chapter — useful for quick tests. |

## Expected timing

On an RTX 3080 Ti:

- **10 minutes of audio** → about **12 minutes** of synthesis
- **1 hour of audio** → about **1 hour 10 minutes** of synthesis
- **A typical 10-hour audiobook** → about **11 hours 30 minutes**

Lower-end GPUs scale roughly with their tensor compute. If you go below
an RTX 3060 things start to hurt; below an RTX 2060 you may find
yourself waiting days for a book.

## A non-developer alternative

If this whole setup sounds like too much for the book you want to read
aloud, the project also ships as a ready-made Windows installer. It
includes the GUI, bundles Edge-TTS and Piper out of the box, and can
install Chatterbox for you from inside the app. You can download it
from the [Releases page](https://github.com/MikkoNumminen/AudiobookMaker/releases/latest).
You get the same engines and the same Grandmom voice, just without the
command line. The setup above is for people who want to script things,
dig into the code, or produce books in batches.
