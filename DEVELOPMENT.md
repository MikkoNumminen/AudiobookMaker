# Developer Guide

Read this once before touching the code. It points you at everything else.

For end-user install instructions, see [README.md](README.md). This file
is for developers and contributors.

## What this is

AudiobookMaker turns a PDF (or plain text) into an MP3 audiobook. A
single Tk/CustomTkinter window lets the user pick input, pick a voice,
press a button. Under the hood it normalizes Finnish text, chunks it,
runs one of three TTS engines (Edge-TTS online, Piper offline,
Chatterbox-Finnish on GPU), and stitches the output together with
ffmpeg.

The project ships as a Windows installer built by GitHub Actions on
every `v*` tag. Auto-update is built in: a running app polls GitHub
Releases and offers a one-click upgrade.

## Where to look first

| You want to… | Read |
|---|---|
| Understand the module layout & data flow | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Know the coding/commit/PR style | [docs/CONVENTIONS.md](docs/CONVENTIONS.md) |
| See what's planned or in flight | [TODO.md](TODO.md) |
| Browse Finnish normalizer cases | [docs/tts_text_normalization_cases.md](docs/tts_text_normalization_cases.md) |
| Judge audio quality systematically | [docs/audiobook_quality_rubric.md](docs/audiobook_quality_rubric.md) |
| Set up a dev environment | [README.md](README.md#developer-setup) |
| Synthesize a book from the command line (step-by-step) | [docs/QUICKSTART_DEV.md](docs/QUICKSTART_DEV.md) |

## Architecture in one screen

```
PDF/EPUB/text ─► pdf_parser / epub_parser ─► tts_normalizer ─► tts_chunking ─► engine.synthesize ─► tts_audio ─► MP3
                                                  │                                  │
                                       ┌──────────┴──────────┐         ┌─────────────┼─────────────┐
                                       ▼                     ▼         ▼             ▼             ▼
                                tts_normalizer_fi   tts_normalizer_en  tts_edge   tts_piper    Chatterbox
                                (Finnish, 16 passes)  (English, 12 passes) (online) (offline)  (GPU subprocess)
```

Three layers, each replaceable:

- **GUI** — `gui_unified.py` is the host class; `gui_synth_mixin.py`,
  `gui_update_mixin.py`, `gui_engine_dialog.py` are the heavy pieces
  extracted out. Mixins use `typing.Protocol` to declare what the host
  must provide. The main bar exposes Language, Engine, Voice and the
  three action buttons (Convert, Make Sample, Preview).
- **Engines** — anything implementing the `TTSEngine` ABC in
  `src/tts_base.py` and decorated with `@register_engine` shows up in
  the GUI dropdown, gated by the Language picker through the engine's
  `supported_languages()`. See "Adding a new engine" below.
- **Pipeline** — `tts_normalizer.py` (language dispatcher) routes to
  `tts_normalizer_fi.py` or `tts_normalizer_en.py`, then `tts_chunking.py`
  splits, `tts_engine.py` orchestrates Edge synthesis + chapters,
  `tts_audio.py` combines.

Detailed module map and Mermaid diagrams live in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Adding a new TTS engine

1. Create `src/tts_<name>.py`.
2. Subclass `TTSEngine` from `src/tts_base.py`. Set the class
   attributes: `id` (stable identifier, lowercase, no spaces),
   `display_name`, `description`, plus the capability flags
   (`requires_gpu`, `requires_internet`, `supports_voice_cloning`,
   `supports_voice_description`).
3. Implement five methods:
   - `check_status()` → `EngineStatus(available, reason, needs_download)`
   - `list_voices(language)` → `list[Voice]`
   - `default_voice(language)` → `str | None`
   - `synthesize(text, output_path, voice_id, language, progress_cb,
     reference_audio=None, voice_description=None)`
   - `supported_languages()` → `set[str]` — which short language codes
     this engine can speak. The Language combobox uses this to hide
     engines that don't support the user's choice. Default returns
     `{"fi"}`; override in your subclass.
4. Decorate the class with `@register_engine`.
5. Import the module in `src/gui_unified.py` (a top-level
   `from src import tts_<name>  # noqa: F401` is enough — the decorator
   fires at import time).
6. Add unit tests in `tests/test_tts_<name>.py` covering at least:
   missing-dependency `check_status`, voice list for the supported
   languages, a synthesize call with the engine's heavy lifting mocked.

The registry is a plain `dict[str, type[TTSEngine]]`, keyed by `id`.
`get_engine(id)` returns a fresh instance every call (no caching). Look
in [src/tts_edge.py](src/tts_edge.py) for the smallest concrete example.

## Text normalizers

Two language-specific normalizers run before chunking, fronted by a
language dispatcher:

- `src/tts_normalizer.py` — the dispatcher. Public entry
  `normalize_text(text, language)` routes to the per-language module
  and lazy-imports it so the unused side stays out of memory.
  Supported codes: `"fi"` and `"en"` (case-insensitive). Unknown codes
  raise `ValueError`.
- `src/tts_normalizer_fi.py` — Finnish normalizer (16 passes).
- `src/tts_normalizer_en.py` — English normalizer (12 passes A-K + L
  currency, M units, N time, O dates, P telephone, R URLs/emails, S
  acronyms; passes O/P/R/S live in `_en_pass_*.py` helper modules).

The Chatterbox subprocess uses the dispatcher: English text bypasses
the Finnish rules so Roman numerals, abbreviations and number-case
inflection don't bleed Finnish words into English audio.

## Finnish normalizer

`src/tts_normalizer_fi.py` runs 16 ordered passes over Finnish text
before it reaches the engine. Each pass has a single responsibility
(citation drop, Roman numerals, abbreviations, governor-aware integer
inflection, etc.). Order matters — comments at each pass document why.

The passes in call order (see `normalize_finnish_text`):

| # | Pass | What it does | Ordering constraint |
|---|------|--------------|---------------------|
| 1 | A | Drop bibliographic citations and metadata parens | First — strips parens with periods that would confuse later passes |
| 2 | J1 | Collapse 3+ dots to ellipsis (`...` → `…`) | Before J2 so TOC dot-leaders remain distinguishable |
| 3 | J2 | Drop TOC dot-leaders (4+ dots + page number) | After J1 |
| 4 | J3 | Strip bare ISBN-13 numbers | Before numeric passes so digits don't get spelled out |
| 5 | B | Split elided-hyphen compounds (insert space) | Runs before abbreviation expansion |
| 6 | K | Expand Finnish abbreviations | Before C — abbreviation periods must go before period-sensitive patterns |
| 7 | L | Expand Roman numerals (regnal, chapter, cardinal) | After K so abbreviation periods don't bleed in; before M |
| 8 | N | Expand whitelisted acronyms (exact case) | After L, before unit expansion |
| 9 | M | Expand measurement units / currency symbols | Before D/F/G so digit prefix stays for governor detection |
| 10 | C | Expand century expressions | After unit expansion |
| 11 | D | Split numeric ranges (3–4 digit) on dash | Before G so each endpoint tokenizes independently |
| 12 | E | Expand page abbreviations (`s.` → `sivu`, `ss.` → `sivut`) | Digits left for G to inflect |
| 13 | F | Spell decimals (nominative float) | Before G; decimals rarely take governor case |
| 14 | G | Governor-aware integer expansion (num2words + case) | After all digit-producing passes |
| 15 | I | Loanword respelling (foreign names, `-ismi`, `-tio`) | After G (post num2words), before H |
| 16 | H | Split glued compound-number morphemes | Last — runs after num2words output settles |

To add a pass:

1. Decide where in the order it belongs (read the comments at
   neighbouring passes; some early passes consume punctuation that
   later ones rely on being absent).
2. Add a `_pass_X_<name>(text: str) -> str` function near the others.
3. Call it from `normalize_finnish_text` in the right slot.
4. Add cases to [docs/tts_text_normalization_cases.md](docs/tts_text_normalization_cases.md).
5. Add tests in `tests/test_tts_normalizer_fi.py`. Most cases use
   `@pytest.mark.parametrize` per topic — add a row to the existing
   table for trivial input/output cases.

The whole pipeline has 1000+ unit tests. Anything you add should too.

## English normalizer

`src/tts_normalizer_en.py` mirrors the Finnish normalizer's structure
with passes for English-specific concerns (Roman numerals as ordinals
or cardinals, dates, currency symbols, units, telephone numbers,
URLs/emails, acronyms). Read the file for the call order.

The four heaviest English passes (O dates, P telephone, R URLs, S
acronyms) live in `src/_en_pass_*.py` so each can be unit-tested in
isolation. New English passes should follow the same convention if
they grow past ~100 LoC.

## Tests

```bash
python -m pytest tests/ -x -q --tb=short
```

- 1000+ tests, mostly fast (<15 s total). Pre-commit hook runs them
  automatically (`scripts/pre-commit`) with a one-shot retry to absorb
  the Windows asyncio ProactorEventLoop teardown flake.
- CI on every push runs the same suite plus coverage
  (`--cov=src --cov-report=term-missing`).
- The voice-recording test (`tests/test_record_voice_sample.py`) needs
  PortAudio and is skipped in CI with `--ignore=…`.
- Tkinter tests in `tests/test_gui_e2e.py` reuse a single `Tk()` root
  via a module-scoped `_shared_app` fixture — Tkinter crashes if you
  create and destroy multiple roots in the same interpreter. An
  autouse `_reset_app_state` fixture wipes per-test mutations to
  prevent cross-test contamination.
- Unit tests for GUI validation paths live in `tests/test_gui_unified.py`;
  use those for fast feedback. Reserve `test_gui_e2e.py` for flows that
  need a real Tk window.
- Per-module test files mirror `src/`: `tests/test_tts_normalizer.py`
  (dispatcher), `tests/test_tts_normalizer_fi.py`,
  `tests/test_tts_normalizer_en.py`, `tests/test_tts_chunking.py`,
  `tests/test_tts_audio.py`, etc.

## Build & release

The Windows installer is produced entirely by
`.github/workflows/build-release.yml`:

1. Push a tag like `v3.4.0`.
2. CI checks out, runs the test suite (`pytest`), downloads pinned
   ffmpeg (verified by SHA-256), stamps the version into
   `src/auto_updater.py`, builds the PyInstaller bundle, wraps it with
   Inno Setup (`installer/setup.iss`), uploads `AudiobookMaker-Setup-X.Y.Z.exe`
   as a Release asset.
3. The release notes must contain a `SHA-256: <hex>` line — the
   running app refuses to install an update without one (see
   [src/auto_updater.py](src/auto_updater.py)).
4. CI then updates README download badges on master.

`APP_VERSION` in `src/auto_updater.py` is the source of truth for the
running version; CI rewrites it from the tag at build time. In dev,
the committed value is what you'll see.

## Multi-agent workflow

Several developers (sometimes humans, sometimes coding assistants)
work in parallel via git worktrees. To avoid collisions:

1. Read [TODO.md](TODO.md) before starting any task.
2. Move the item to "In Progress" with your name tag *before* touching
   files — `[Claude 1, main]`, `[mikko, dev-docs]`, etc.
3. Don't pick up an item already tagged in "In Progress".
4. Worktrees live under `.claude/worktrees/<name>` and are git-isolated.
5. Keep commits small and topical — see
   [docs/CONVENTIONS.md](docs/CONVENTIONS.md).

## Known constraints

- **Scanned PDFs don't work.** No OCR step. Text must be selectable in
  a PDF reader.
- **Chatterbox-Finnish needs an NVIDIA GPU** with ≥8 GB VRAM. It runs
  in its own venv (~15 GB) so the main installer stays ~200 MB.
- **VoxCPM2 is dev-only / experimental.** Not shipped in the installer.
- **Qwen3-TTS was investigated and dropped** — Finnish unsupported,
  CUDA-only with flash-attn3, slower than realtime even on RTX 4090.
  `dev_qwen_tts.py` stays as a feasibility marker, not a viable engine.
- **Edge-TTS needs internet** (it's a Microsoft cloud API).
- **Finnish normalizer is tuned for legal/historical prose.** Other
  domains may need lexicon extensions in `fi_loanwords.py` and the
  governor table in `tts_normalizer_fi.py`.

## Paths and configuration

- `~/.audiobookmaker/config.json` — `UserConfig` dataclass; load/save
  in [src/app_config.py](src/app_config.py). Unknown keys are
  filtered, types are checked.

  | Key | Type | Default | Notes |
  |-----|------|---------|-------|
  | `engine_id` | str | `"edge"` | Which TTS engine the app starts with |
  | `language` | str | `"fi"` | Short language code of the last selected language |
  | `voice_id` | str | `""` | Engine-specific voice id; empty means use engine default |
  | `speed` | str | `"+0%"` | edge-tts style speed adjustment string |
  | `reference_audio` | str | `""` | Path to a reference audio file for cloning engines |
  | `voice_description` | str | `""` | Free-text voice description (VoxCPM2); ignored by engines that don't support it |
  | `input_mode` | str | `"pdf"` | Last used input mode: `"pdf"` or `"text"` |
  | `output_mode` | str | `"single"` | `"single"` (one MP3) or `"chapters"` (per chapter) |
  | `log_panel_visible` | bool | `True` | Whether the log panel is visible |
  | `ui_language` | str | `""` | UI language: `"fi"`, `"en"`, or empty for locale auto-detect |

- `~/.cache/huggingface/hub/models--rhasspy--piper-voices/` — Piper
  voice models, ~60 MB each. Downloaded on demand by
  [src/tts_piper.py](src/tts_piper.py).
- `.venv-chatterbox/` — Chatterbox venv. Installed by
  [src/engine_installer.py](src/engine_installer.py) using
  [scripts/setup_chatterbox_windows.ps1](scripts/setup_chatterbox_windows.ps1).
- ffmpeg/ffplay/ffprobe — bundled at PyInstaller root in releases;
  fall back to `dist/ffmpeg/` in dev. Resolution lives in
  [src/ffmpeg_path.py](src/ffmpeg_path.py), which also patches `pydub`
  to suppress console windows on Windows.
- Output MP3s default next to the PDF (PDF input mode) or to
  `~/Documents/AudiobookMaker/` (text input mode), with
  auto-incrementing filenames so nothing is overwritten.

## Languages used in this repo

- **Code, comments, docstrings, type names** — English.
- **Commit messages, PR titles, branch names** — English.
- **User-visible UI strings** — Finnish + English in `_STRINGS` dict
  in `src/gui_unified.py`; `self._s("key")` looks them up by current
  UI language.
- **TODO.md** — mixed Finnish/English, whichever fits the item.
- **README.md** — English.

## Updating this guide

Keep DEVELOPMENT.md short. If a section grows past ~30 lines, move it
to a dedicated doc under `docs/` and link to it from here. The point
of this file is "where do I look?", not "everything I need to know".
