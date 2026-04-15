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

## Check first — do you already have Chatterbox installed?

If you've run AudiobookMaker on this machine before — either via the
GUI's "Install engines" button, or via a previous pass through this
guide — you probably already have a working Chatterbox environment
and model cache. Setting it up again takes 15 to 30 minutes you don't
need to spend.

Run this to see what the project can find:

```powershell
python -c "from src.launcher_bridge import resolve_chatterbox_python; p = resolve_chatterbox_python(); print(p or 'no chatterbox venv found')"
```

If it prints a path to a `python.exe`, you already have a Chatterbox
venv ready to go. **Skip the next section.** In the synthesis commands
further down, replace `.venv-chatterbox\Scripts\python.exe` with
whatever path this printed — everything else works identically.

The function checks a handful of known locations:

- `<repo>\.venv-chatterbox\Scripts\python.exe` (where the setup script
  below creates it)
- `%LOCALAPPDATA%\Programs\AudiobookMaker\.venv-chatterbox\Scripts\python.exe`
  (where the in-app "Install engines" button creates it on a normal
  install)
- A couple of legacy paths from older app versions

The HuggingFace model cache at `~/.cache/huggingface/hub/` is shared
across all of these, so once one install has downloaded the weights,
every later install reuses them instantly.

If nothing was found, proceed with the section below.

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

For this walkthrough, we'll use an EPUB called `Rubicon.epub`.
**If your book is a PDF or a plain text file instead, the flow is the
same — you just swap a flag and, in Way B, a Python import. The table
below shows the substitutions:**

| Format | Script flag (Way A) | Parser module + function (Way B) |
|---|---|---|
| EPUB | `--epub Rubicon.epub` | `from src.epub_parser import parse_epub` → `parse_epub('Rubicon.epub')` |
| PDF | `--pdf Rubicon.pdf` | `from src.pdf_parser import parse_pdf` → `parse_pdf('Rubicon.pdf')` |
| Plain text `.txt` | `--text-file my_book.txt` | No parser needed — the file already contains the text. Skip the extraction step in Way B entirely. |

Everything else — `--language`, `--out`, `--device`, the resume
behaviour, the progress output, the flag reference table below —
works identically regardless of input format. PDFs with unusual
layouts (multi-column academic papers, scanned pages where text is
actually an image, heavy footnote cross-references) can produce
noisy text; run your output text through a quick eyeball pass before
feeding it to a long synthesis.

There are two ways to run the synthesis: on the **whole book** in one
go, or on a **short excerpt** you've carved out for testing. Pick
whichever fits what you're trying to do.

## Choosing the voice and the language

Before running the script, decide two things: what language the text
is in, and what voice should read it. The command-line script handles
both through one flag each.

The command-line guide here covers one TTS engine — **Chatterbox**.
It's the highest-quality option, runs on your GPU, and can clone
voices from a reference clip. AudiobookMaker has two other engines
(**Edge-TTS** for fast cloud synthesis and **Piper** for offline CPU
synthesis) — those are best used through the GUI or the parallel
generator script and aren't covered here.

**Language selection**, via the `--language` flag:

| Flag value | What happens inside |
|---|---|
| `--language fi` (default) | Loads the Finnish-NLP T3 fine-tuned model on top of the base multilingual Chatterbox. Reads Finnish text with native Finnish phonemes. Any non-Finnish words come out Finnish-accented. |
| `--language en` | Loads the base multilingual Chatterbox only (no Finnish finetune). Reads English text with native English phonemes. Feeding Finnish text here would produce English-accented Finnish — so match the flag to the text. |

**Voice selection**, via the `--ref-audio` flag:

| Flag | Voice you get |
|---|---|
| No flag (default) | **Grandmom** — the warm-elderly-narrator voice that comes with the project. Works in both languages: pair with `--language fi` for Grandmom reading Finnish, or with `--language en` for Grandmom reading English. Details in `assets/voices/grandmom_reference.wav` and the Finnish-NLP reference clip. |
| `--ref-audio path\to\voice.wav` | **Any voice you've recorded.** The script clones the timbre of the reference clip. 10–20 s of clean speech, 24 kHz mono WAV. Works with either language. |

So the two common recipes are:

- **Finnish book, Grandmom voice:** `--language fi` alone. Finnish
  text goes through the Finnish-trained model and out in Grandmom's
  voice. This is the default — if you pass neither flag, you get this.
- **English book, Grandmom voice:** `--language en` alone. English
  text goes through the base English model; Grandmom's timbre is
  carried over via a bundled reference clip that ships with the app.

Mismatch warning: `--language en` on a Finnish book will still
produce audio, but the model will try to pronounce Finnish as if it
were English. It sounds wrong in a specific and charming way. The
reverse — `--language fi` on English text — sounds like a Finn
reading English, which is also wrong-in-a-specific-way. Match the
flag to the text's language.

## Way A — the whole book in one command

This is the simplest path. Point the script at the EPUB (or PDF) and
it handles everything: extracting the text, splitting it into
chapters, normalizing the text, chunking it for the model, and
stitching the chunks back into one MP3 at the end.

```powershell
mkdir out
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py `
    --epub Rubicon.epub `
    --out out\rubicon.mp3 `
    --language en `
    --device cuda
```

For a typical novel this runs for several hours. The synthesis caches
every chunk to disk as it's produced, so if you have to stop and
restart (power blip, Windows Update, whatever) the same command just
picks up where it left off — you don't lose the already-synthesized
material.

If you're starting out and want to know the run time before you
commit to it, tack on `--dry-run`. That parses the book, splits it
into chunks, and prints the estimate without using the GPU:

```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py `
    --epub Rubicon.epub --language en --dry-run
```

The output tells you how many chunks the model will process, the
estimated audio length, and the estimated wall time. Useful for
"can I kick this off overnight?" decisions.

## Way B — a short excerpt for testing

Full audiobooks take hours. For a first run — checking that Chatterbox
is working, that the voice sounds right, that `--language en` routes
correctly — you want something much shorter. Carve out two chapters,
which will produce roughly an hour of audio and take roughly an hour
of GPU time.

The project has a helper to parse an EPUB into a list of chapters.
Use it from Python to write the chapters you want into a plain text
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

Now run the synthesis on the text file:

```powershell
mkdir out
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py `
    --text-file my_input.txt `
    --out out\book.mp3 `
    --language en `
    --device cuda
```

If you already know the chapter indices you want from a full book and
don't need to edit the text first, there's a faster way that skips the
extraction step — use `--epub` with `--chapters`:

```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py `
    --epub Rubicon.epub `
    --chapters 10,11 `
    --out out\book.mp3 `
    --language en `
    --device cuda
```

This gives you the same result as Way B's two-step flow in one
command. The two-step flow is still useful when you want to clean up
the extracted text, trim a messy chapter heading, or glue together
chapters from different books.

## Shared details for both ways

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

## Cheatsheet — four copy-paste commands

Once both venvs are set up and your book is in the repo root, these
are the four commands you need, one per language/format pair. All of
them use the Grandmom voice (the default — no extra flag needed).
Replace `Book.epub` / `Book.pdf` with your actual filename and adjust
the `--out` path if you want the MP3 somewhere specific.

**Finnish book, EPUB:**
```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py `
    --epub Book.epub `
    --out out\book.mp3 `
    --language fi `
    --device cuda
```

**Finnish book, PDF:**
```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py `
    --pdf Book.pdf `
    --out out\book.mp3 `
    --language fi `
    --device cuda
```

**English book, EPUB:**
```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py `
    --epub Book.epub `
    --out out\book.mp3 `
    --language en `
    --device cuda
```

**English book, PDF:**
```powershell
.venv-chatterbox\Scripts\python.exe scripts\generate_chatterbox_audiobook.py `
    --pdf Book.pdf `
    --out out\book.mp3 `
    --language en `
    --device cuda
```

That's it. The only two things that change between the four are the
input flag (`--epub` vs `--pdf`) and the language (`--language fi` vs
`--language en`). Everything else is identical — Grandmom voice,
CUDA device, resume-on-restart, MP3 output.

If you prefer a ready-made script, you can also paste any of these
into a `.ps1` file (say `run_en_epub.ps1`) and double-click it from
Explorer.
