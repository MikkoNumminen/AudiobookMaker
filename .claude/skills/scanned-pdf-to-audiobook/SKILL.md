---
name: scanned-pdf-to-audiobook
description: Convert a scanned / image-only PDF into an audiobook end-to-end via the OCR fallback (ocrmypdf + Tesseract, merged in PR #26). Use whenever the user says "this PDF is scanned", "OCR this and read it aloud", "the app says no text", "the EmptyPDFError says it might be scanned", or hands you a `.pdf` PyMuPDF can't extract text from. Encodes the voice / language / sample-first decisions, surfaces the `tessdata/configs/` gotcha that crashes ocrmypdf at 100%, and walks long-running OCR jobs (300+ pages, 100+ MB) through the right pre-flight + cancellation handling. CRITICAL — never commit the source PDF or put its filename / title / author in any tracked file; all artefacts stay in `.local/`.
---

# scanned-pdf-to-audiobook

Take a scanned / image-only PDF (court docs, old book scans, screenshot-collated
material) and walk it end-to-end into an audiobook MP3 using the OCR fallback
that landed on master in PR #26.

## Why this skill exists

Users hand over scanned PDFs expecting the app to "just read it." Without OCR
that's an immediate [`EmptyPDFError`](../../../src/pdf_parser.py#L23) with the
hint *"may be scanned — try OCR first"*. PR #26 made the fallback automatic
inside [`parse_pdf`](../../../src/pdf_parser.py#L378): empty pages trigger
[`_run_ocr_fallback`](../../../src/pdf_parser.py#L324), which shells out to
ocrmypdf-17.4.2 → Tesseract → Ghostscript and re-extracts text from the
ocrmypdf'd PDF.

The "automatic" part covers the happy path. Multi-step decisions still need
a human (or this skill) in the loop:

- **OCR language** — Tesseract needs the right `--language` (`fin` vs `eng`).
  Wrong language = garbage characters = mispronounced output.
- **Voice choice** — every minute of full synth on the wrong voice is wasted
  GPU. Always sample 30 s first.
- **Output location** — `.local/audiobooks/` in dev mode (the Chatterbox
  runner script adds a per-book `<stem>/` subdir when invoked; Edge-TTS
  / text-mode writes a single `<stem>.mp3` directly under that dir), next
  to the exe in frozen mode. Never the repo root, never `~/Documents`.
- **Source secrecy** — the PDF's filename / title / author / characters never
  appear in any tracked file. CLAUDE.md treats a leak the same severity as
  leaked secrets.

This skill encodes those decisions so a future session doesn't relitigate
them.

## Trigger phrases

- "this PDF is scanned" / "this book is scanned"
- "the app says no text" / "the app says it can't read this"
- "convert this image PDF / image book / image document to audio"
- "OCR this and read it aloud"
- "the EmptyPDFError says it might be scanned"
- User drops a `.pdf` into `.local/` and asks for synthesis
- [`parse_pdf`](../../../src/pdf_parser.py#L378) raises
  [`EmptyPDFError`](../../../src/pdf_parser.py#L23) in the conversation
  transcript

## When NOT to invoke

- **PDF already has a text layer.** PyMuPDF returns text on first try in
  [`_extract_pages`](../../../src/pdf_parser.py#L282); `parse_pdf` never even
  calls the OCR fallback. Don't force OCR via this skill — it would just
  double the wall-clock for no gain. (If you *want* to force OCR for quality
  reasons on a low-quality embedded text layer, that's a separate task and
  should use `ocrmypdf --redo-ocr`, which this skill does not cover.)
- **Source is an audio file** (the user wants voice cloning, not OCR) — see
  [voice-clone-finnish](../voice-clone-finnish/SKILL.md).
- **Source is EPUB or `.txt`.**
  [`parse_book`](../../../src/synthesis_orchestrator.py#L45) routes those past
  the OCR path entirely; the `ocr_language` arg is ignored for non-PDF input.
- **User wants a custom OCR engine** (cloud OCR, Adobe, etc.). We ship only
  Tesseract bundled into the frozen build per
  [`audiobookmaker.spec`](../../../audiobookmaker.spec#L241). Plugging in a
  different engine is a code-change task, not a runbook task.

## Hard constraints (read before every step)

- **Never commit the source PDF.** Never put its filename, title, author,
  character names, or any identifying path in any tracked file — code,
  tests, docs, `TODO.md`, commit messages, PR titles/bodies, release notes.
  CLAUDE.md "No third-party copyrighted material" rule. A leak is P0.
- **Source PDF stays in `.local/`** (gitignored). OCR cache lives in
  `.local/ocr/cache/<sha256>.pdf` or the OS temp dir (the resolver in
  [`_ocr_cache_path`](../../../src/pdf_parser.py#L311) picks based on the
  `ocr_cache_dir` arg). Final MP3 goes under
  [`default_output_dir()`](../../../src/synthesis_orchestrator.py#L105),
  which is `.local/audiobooks/` in dev mode (the Chatterbox runner script
  nests a per-book `<stem>/` subdir; Edge-TTS / text-mode writes a flat
  `<stem>.mp3`). Frozen mode writes next to the exe, same function.
- **One ML subprocess at a time on this machine.** OCR itself is CPU-bound
  and fine alongside other work, but the *synth* step that follows is the
  heavy one — single Chatterbox / VoxCPM run per box, per CLAUDE.md
  "Resource discipline".
- **Default to a 30-second sample before the full synth.** Voice mismatch
  is the most common waste of GPU time on multi-hour books. Sample first,
  ear-check, then commit.

## Pre-flight checks

Before kicking anything off, verify the toolchain. The OCR fallback silently
degrades to no-op if any piece is missing
([`is_ocr_available`](../../../src/ocr_path.py#L108) returns `False`), so a
missing dep manifests as the same `EmptyPDFError` the user already hit.

```powershell
tesseract --version
gswin64c --version           # or: where.exe gswin64c
.venv-chatterbox/Scripts/python.exe -c "import ocrmypdf; print(ocrmypdf.__version__)"
.venv-chatterbox/Scripts/python.exe -c "from src.ocr_path import is_ocr_available; print(is_ocr_available())"
```

Expected: tesseract 5.x, ghostscript 10.x, ocrmypdf == 17.4.2 (the pin in
[`requirements.txt`](../../../requirements.txt#L18)), and `is_ocr_available()`
prints `True`.

If any of those fail, walk the user through the install steps before
proceeding:

- **Tesseract:** `winget install UB-Mannheim.TesseractOCR`. The installer
  adds it to PATH; restart the shell after.
- **Ghostscript:** download the latest installer from the Artifex
  Ghostscript GitHub releases (the `gsXXXw64.exe` artifact); winget's
  Ghostscript package is sometimes out-of-date. Installer adds
  `gswin64c.exe` to PATH.
- **ocrmypdf:**
  `.venv-chatterbox/Scripts/pip install ocrmypdf==17.4.2`. The pin
  matters — ocrmypdf has renamed public API entry points across minor
  versions and PR #26's smoke tests are against 17.4.2 specifically.

### The `tessdata/configs/` gotcha

When the user is operating from a **frozen build** (not the dev venv) and
sees "100% progress then `WinError 2 *_ocr_hocr.hocr not found`", the
installer was built without `tessdata/configs/`. Tesseract's output-format
presets (`hocr`, `txt`, `pdf`) live there; ocrmypdf invokes them via
subprocess and crashes reading empty `.hocr` output when they're missing.
[`audiobookmaker.spec:259`](../../../audiobookmaker.spec#L259) bundles
`tessdata/configs/*` next to `*.traineddata`; if the spec block didn't run
(because `dist/ocr/` wasn't populated before PyInstaller), re-run the CI
bundling step or copy the `configs/` directory from the system Tesseract
install at `C:\Program Files\Tesseract-OCR\tessdata\configs\` into the
frozen build's `tessdata/configs/`.

## Step-by-step workflow

### Step 1 — verify OCR is actually reachable

The user might already have hit `EmptyPDFError` precisely because OCR isn't
reachable. Don't assume the pre-flight passed — probe the same code path
the parser will:

```powershell
.venv-chatterbox/Scripts/python.exe -c @'
from src.ocr_path import is_ocr_available, get_tesseract_exe, get_ghostscript_exe, get_tessdata_dir
print("ocr_available:", is_ocr_available())
print("tesseract:    ", get_tesseract_exe())
print("ghostscript:  ", get_ghostscript_exe())
print("tessdata:     ", get_tessdata_dir())
'@
```

If `is_ocr_available()` is `False`, stop and walk back through the pre-flight
install steps. Do not try to "OCR anyway" — `parse_pdf` will skip the
fallback and re-raise `EmptyPDFError`.

### Step 2 — determine OCR language

Default = `tesseract_lang_for(<the active TTS book-language code>)` from
[`ocr_path.py:103`](../../../src/ocr_path.py#L103). The GUI Convert path
already does this at [`gui_unified.py:1436`](../../../src/gui_unified.py#L1436)
with `tesseract_lang_for(self._current_language())`; the CLI launcher
hard-codes `"fin"` at [`launcher.py:426`](../../../src/launcher.py#L426).
The mapping:

- `fi` → `fin`
- `en` → `eng`
- anything else / empty → `eng` (English letterforms are a superset of the
  diacriticless Latin alphabet — better than guessing wrong).

If the user explicitly says *"this PDF is in <lang>"*, honor it. Otherwise
infer from filename hints if obvious, or make the reasonable call and
proceed (the user can redirect after a sample synth if the OCR output is
gibberish — that's the cheapest validation).

Mixed-language documents (Finnish prose with English block quotes, e.g.)
work with `fin+eng` passed to Tesseract; ocrmypdf forwards multi-language
strings verbatim. Use it sparingly — each extra language slows OCR ~30%.

### Step 3 — run the OCR'd parse

One Python probe surfaces character count, page count, wall-clock, and a
sanity snippet of the OCR'd text:

```powershell
.venv-chatterbox/Scripts/python.exe -c @'
import time
from pathlib import Path
from src.pdf_parser import parse_pdf
from src.ocr_path import tesseract_lang_for

source = Path(".local/<stem>.pdf")
cache  = Path(".local/ocr/cache")
cache.mkdir(parents=True, exist_ok=True)

t0 = time.monotonic()
book = parse_pdf(str(source), ocr_language=tesseract_lang_for("fi"), ocr_cache_dir=cache)
dt = time.monotonic() - t0

print(f"pages={book.metadata.num_pages} chars={book.total_chars} chapters={len(book.chapters)} wall={dt:.1f}s")
print("--- first 200 chars ---")
print(book.full_text[:200])
'@
```

Surface to the user:

- char count (10 000 chars ≈ 30 min of audio at average speech rate)
- page count
- wall-clock (OCR is ~3-8 s per page on a modern CPU; 200-page book ≈
  10-25 min — if you're seeing per-page times an order of magnitude over
  that, Tesseract is probably hitting the wrong language pack)
- the first ~100-200 chars of `book.full_text` as a sanity check.

If the snippet is garbage characters, the language is wrong (or the source
quality is too low for Tesseract's default DPI). Re-run Step 3 with the
correct `ocr_language=`; the cache key is the source's sha256 so re-runs
with different language args produce different cache files and don't
clobber each other.

### Step 4 — pick the voice

If the user has a voice pack in `.local/voice_packs/`, list them with
their `meta.yaml` `language` field so the user can match the OCR'd text's
language:

```powershell
.venv-chatterbox/Scripts/python.exe -c @'
from pathlib import Path
import yaml
for d in sorted(Path(".local/voice_packs").iterdir()) if Path(".local/voice_packs").exists() else []:
    meta = d / "meta.yaml"
    if meta.is_file():
        data = yaml.safe_load(meta.read_text(encoding="utf-8")) or {}
        print(f"{d.name}: lang={data.get('language', '?')} tier={data.get('tier', '?')}")
'@
```

Otherwise default to Edge-TTS for a fast first sample:

- English text → Edge-TTS **Jenny** (`en-US-JennyNeural`).
- Finnish text → Edge-TTS **Noora** (`fi-FI-NooraNeural`).

Edge-TTS is the right sampler because it's near-instant (no model warm-up),
runs on CPU, and reveals OCR quality problems immediately. Chatterbox /
VoxCPM samples take 10× longer to produce and obscure OCR-vs-voice failure
modes during the ear-check.

### Step 5 — synthesize a 30-second sample

Pick the first ~800 characters of `book.full_text` (≈ 30 s of speech at
160 wpm), write to a file under `.local/audiobooks/`, and synthesize.

For an Edge-TTS sample, the GUI's "Convert" path on a 600-char text file
is fastest; or the headless equivalent goes through the synthesis
orchestrator with the Engine set to `edge_tts` and Voice set to `Jenny`
(English) or `Noora` (Finnish).

For a Chatterbox sample with a local voice pack:

```powershell
.venv-chatterbox/Scripts/python.exe scripts/generate_chatterbox_audiobook.py `
  --text-file .local/audiobooks/<stem>_sample/sample_input.txt `
  --voice-pack .local/voice_packs/<pack> `
  --ref-audio .local/voice_packs/<pack>/reference.wav `
  --language fi `
  --out .local/audiobooks/<stem>_sample/
```

(Pack the sample input and output under
`.local/audiobooks/<stem>_sample/`, not the final-synth dir — keeps the
sample artefacts separate from the eventual full audiobook so a botched
sample doesn't get mailed to the user.)

Surface the output WAV / MP3 path. Do not auto-play.

### Step 6 — user ear-check

Wait for the user to confirm the sample sounds right:

- Voice matches expectation? If wrong gender / wrong language / wrong
  persona, loop back to Step 4 with a different voice.
- OCR readable? If the synth mispronounces every other word, OCR went
  wrong; loop back to Step 3 with the correct `ocr_language=` or with the
  user's confirmation that the source is just too low-quality to OCR
  cleanly (in which case manual text repair on the OCR'd PDF's text
  layer is the next move).
- Speed / prosody OK? Engine speed defaults are usually fine; rate
  adjustment is a runtime knob in the GUI's Speed slider.

Only proceed when the user explicitly approves the sample.

### Step 7 — full synthesis

Same voice / language flags but the full text. Output lands under
`.local/audiobooks/` (the Chatterbox runner script nests a per-book
`<stem>/` subdir; the Edge-TTS / text-mode path writes a flat
`<stem>.mp3`). No `_sample` suffix — the full run is the canonical one.

Estimate ETA from the sample's real-time factor (RTF). A 30-second sample
that took 10 s wall-clock has RTF ≈ 0.33; a 200 000-char book at ~160
wpm ≈ 350 minutes of audio × 0.33 RTF ≈ 115 minutes of wall-clock. Tell
the user the ETA upfront — multi-hour synth runs that nobody warned them
about are a common annoyance.

Surface the final MP3 path when done.

## Very long PDFs (300+ pages, 100+ MB)

The pipeline handles long PDFs mechanically — Tesseract processes one page
at a time so RAM stays bounded — but the wall-clock and disk story changes
enough that it deserves its own pre-flight. Apply this whole section before
Step 3 when the source PDF is over ~100 MB or you can see it has hundreds
of pages.

### Pre-flight: size + disk

```bash
# Source size and rough page count
python -c "import fitz, sys; d=fitz.open(sys.argv[1]); print(f'pages={len(d)}, size_mb={__import__(\"os\").path.getsize(sys.argv[1])/1e6:.1f}')" .local/<source>.pdf

# OS temp dir free space — ocrmypdf writes intermediates here.
# Roughly 1-2 MB of intermediate state per scanned page; 1000 pages ≈ 2 GB
# Always check before kicking off a multi-hour run.
df -h "$TEMP" 2>/dev/null || powershell -NoProfile -Command "Get-PSDrive -Name C | Select-Object Used,Free"
```

If temp has less than 2× the source's worst-case intermediate footprint,
either free space, or set `TMPDIR` to a roomier partition before invoking
parse_pdf so ocrmypdf lands its scratch there.

### Wall-clock expectations

Tesseract scales linearly in pages on CPU. The 32-page smoke test took 25 s
end-to-end on this dev box — about **~0.8 s per page** in the best case.
Rough rules of thumb:

| Pages | OCR wall-clock | What the user should be told |
|-------|---------------|-------------------------------|
| < 50  | < 1 min       | "kicking off OCR" |
| 50-200 | 1-5 min      | "this'll take a few minutes" |
| 200-500 | 5-15 min    | "go grab coffee" |
| 500-1000 | 15-30 min  | "this is a 15-30 min OCR pass, then a multi-hour synth" |
| 1000+ | 30+ min      | "run OCR in a dedicated step; ear-check the OCR'd text BEFORE committing to synth" |

ocrmypdf 17.x defaults to `jobs=os.cpu_count()`, so on a 16-core box you'll
see speedups close to linear up to ~8 jobs (Tesseract's per-page work is
single-threaded internally; parallelism comes from running multiple page
jobs concurrently).

### Run OCR as a dedicated step on long sources

Don't bundle a multi-hour OCR into the same invocation as the synthesis run.
Do this instead:

1. Call `parse_pdf(source_pdf, ocr_language=..., ocr_cache_dir=Path('.local/ocr/cache'))`.
   This runs OCR, populates the cache, and returns a `ParsedBook`.
2. Sanity-check the OCR'd output — pull `book.full_text[:1500]` and read it.
   If it's garbled (wrong language, low-DPI source, bad confidence), fix
   the language or pre-process the source BEFORE wasting a long synth run.
3. **Only then** kick off the synthesis run (Step 5+ of the main workflow).
   The synth re-calls `parse_pdf` against the same source; it's a cache hit
   (~100 ms) and zero re-OCR work.

### Cancellation and resume

ocrmypdf runs as a synchronous Python call. Ctrl-C / process-kill /
ocrmypdf crash → [`_run_ocr_fallback`'s `except` branch](../../../src/pdf_parser.py#L368)
deletes the partial output PDF so the cache stays clean. There is **no
per-page resume** — restart re-OCRs from page 1. Two implications:

- For a 30-minute OCR job, an interrupt costs the full 30 minutes on retry.
- Telling the user the ETA upfront is non-negotiable on long runs.

If you absolutely must split a 1000-page PDF into batches (e.g. you keep
losing the run to system reboots), use PyMuPDF to slice the source into
N smaller PDFs, OCR each separately (each produces its own cache entry
keyed by its sha256), then concatenate the OCR'd outputs. The skill does
not automate this — it's manual-task territory and the operator should
flag the unusual workflow to the user before doing it.

### What "garbage OCR" looks like at scale

On long PDFs you'll occasionally see Tesseract degrade on:
- Pages with heavy artefacts (margins, page-numbers running into body text)
- Mid-document language shifts (an English paper quotes a Finnish source)
- Faded / low-DPI source scans (< 200 DPI)

The skill ships no confidence filtering (Phase 0 deferred — see
[OCR_FALLBACK.md §11](../../../docs/OCR_FALLBACK.md)). For long runs,
spot-check 3-5 sample pages from across the doc (first / 25% / 50% / 75% /
last) by reading `book.chapters[i].content` and dumping the first 200 chars.
A 5-second visual scan catches most failures cheaper than a 2-hour synth.

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `"OCR engine does not have language data for: fin"` | `TESSDATA_PREFIX` points to wrong dir | Export `TESSDATA_PREFIX=<dir-containing-traineddata>` — *not* the parent dir. The bundled-build resolver in [`setup_ocr_path`](../../../src/ocr_path.py#L123) gets this right; dev installs may need a manual env-var override. |
| 100% OCR progress then `WinError 2 *_ocr_hocr.hocr not found` | `tessdata/configs/` missing | Copy `configs/` from a system Tesseract install (`C:\Program Files\Tesseract-OCR\tessdata\configs\`) into the bundle's `tessdata/configs/`, OR re-run the CI bundling step that populates `dist/ocr/` before PyInstaller. See [`audiobookmaker.spec:259`](../../../audiobookmaker.spec#L259). |
| [`is_ocr_available()`](../../../src/ocr_path.py#L108) returns `False` even after Tesseract install | ocrmypdf can't see Ghostscript | Verify `gswin64c --version` works from the same shell where Python runs. The path resolver in [`get_ghostscript_exe`](../../../src/ocr_path.py#L55) checks `shutil.which`, so PATH needs to include Ghostscript's `bin\` dir. Restart the shell after a fresh installer run. |
| Garbage OCR output triggering false chapter headings | OCR confidence filtering not implemented yet (Phase 0 deferred) | Manual fix: edit the OCR'd PDF's text layer in Acrobat / a similar editor, OR pass the OCR'd `.pdf` through and accept that the chapter detector in [`_split_into_chapters`](../../../src/pdf_parser.py#L196) will produce noise — wrap-everything-in-one-chapter fallback kicks in only when *no* headings match. |
| Synth pronounces every word wrong | Wrong `ocr_language=` | Re-run Step 3 with the correct Tesseract code. Cache key is per-content + per-language, so the corrected run produces a different cache file and the original is preserved (delete it manually from `.local/ocr/cache/` if you want to reclaim disk). |
| OCR cache fills `.local/ocr/cache/` over time | No automatic eviction | Manually delete stale `.pdf` files under `.local/ocr/cache/`. Each file is keyed by source sha256, so removing a file just forces a re-OCR if the same source comes back. |
| Long OCR run interrupted halfway (Ctrl-C, OS kill, ocrmypdf crash) | Synchronous subprocess; no per-page checkpointing | The partial output PDF is deleted by [`_run_ocr_fallback`'s except block](../../../src/pdf_parser.py#L368), so the next run sees a cache miss and re-OCRs from scratch (no "zombie half-OCR'd cache file" risk). Wall-clock cost: full re-run. To minimise risk on multi-hour OCRs, run OCR in a dedicated step before kicking off any synthesis. |

## What this skill does NOT do

- **Does not train new voice packs.** Voice cloning is a separate runbook —
  see [voice-clone-finnish](../voice-clone-finnish/SKILL.md).
- **Does not cut releases of the OCR feature.** Bumping APP_VERSION,
  tagging, and verifying the auto-update SHA-256 contract is the
  [release-cut](../release-cut/SKILL.md) skill.
- **Does not modify the spec for installer bundling.** When `dist/ocr/`
  needs new files (new traineddata language, new Tesseract version),
  that's a [release-bundle-audit](../release-bundle-audit/SKILL.md)
  task — measure the size impact and decide which language packs ship
  in the installer vs. which are optional.
- **Does not force-OCR a PDF that already has a text layer.** That's a
  text-quality fix (`ocrmypdf --redo-ocr`), not an audiobook-generation
  task.
- **Does not download source PDFs from URLs.** If the user points at a
  URL, ask them to download to `.local/` themselves — auto-fetching
  copyrighted material is the same hazard as auto-fetching audio
  source clips.
