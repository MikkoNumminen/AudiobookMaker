# AudiobookMaker — command-line interface

This document is two things in one file:

1. **An audit** of every command-line tool that exists in the repo today,
   what it does, who it's for, and what shape it ships in.
2. **A design proposal** for a single `audiobookmaker-cli` entry point
   that exposes every GUI capability to non-GUI users — packaged and
   versioned as a parallel release artifact alongside the installer.

If you only care about which script to run for a given job today, jump
to [Inventory](#inventory). If you're thinking about how to evolve this
into a maintainable user-facing CLI, jump to [Design](#design).

## Why a CLI at all

The GUI is the primary surface for end users. But the project has three
audiences a CLI serves better:

- **Power users and sysadmins.** People who want to batch-convert a
  whole shelf of PDFs without clicking through dialogs, or who run on
  headless servers.
- **Pipeline integrators.** People who want to feed AudiobookMaker into
  a larger automation flow — cron, CI, a Makefile, another script —
  and don't want to drive a Tk window.
- **Developers and testers.** People reproducing a bug, comparing
  engines, or running stress tests. They already do this today via the
  scripts under `scripts/`, but the surface is uneven and undocumented.

A unified, versioned `audiobookmaker-cli` makes all three groups
first-class without bolting "headless mode" onto the GUI.

## Inventory

Everything below is what exists in `scripts/`, `src/`, and at the repo
root as of this audit. Each entry lists what it does, what it needs to
run, and its maturity tier.

### Tier 1 — production CLIs (stable, documented, frozen-bundle-ready)

These are the scripts safe to point users at. Argparse-driven, robust
error handling, predictable output.

#### `scripts/generate_chatterbox_audiobook.py`

The big one. Full PDF / EPUB / TXT → MP3 via Chatterbox + the Finnish
fine-tune. This is the script the GUI shells out to when you pick the
Chatterbox engine; running it directly skips the GUI entirely.

- Flags: `--pdf`, `--epub`, `--text-file`, `--out`, `--chapters`,
  `--chunks-per-chapter`, `--device`, `--resume`, `--chunk-chars`,
  `--rtf`, `--ref-audio`, `--language`, `--voice-pack`, `--dry-run`.
- Needs: a `.venv-chatterbox` virtualenv with PyTorch + the Chatterbox
  package, an NVIDIA GPU (8 GB+ VRAM), bundled ffmpeg.
- Writes: `.local/audiobooks/<book>/` with per-chapter MP3s, a
  combined `00_full.mp3`, a chunk cache, and a `.progress.json` so
  resume works.

#### `scripts/voice_pack_analyze.py`

Stage 1 of the voice-pack pipeline. Takes a long audio source, runs
ASR (faster-whisper) plus diarization (pyannote by default, ECAPA as a
fallback), and writes per-speaker chunks with quality tiers.

- Flags: `--input`, `--out`, `--hf-token`, `--asr-model`,
  `--asr-device`, `--min-duration`, `--max-duration`,
  `--min-confidence`, `--num-speakers`, `--min-speakers`,
  `--max-speakers`, `--diarizer`.
- Needs: `HF_TOKEN` (from `.env` or the OS environment) if you use
  pyannote; nothing extra if you use `--diarizer ecapa`.
- Writes: `transcripts.jsonl`, `speakers.yaml`, `report.md`.

#### `scripts/voice_pack_export.py`

Stage 2. Filters the analyze output by speaker, slices WAV clips at the
right sample rate, and emits a training manifest.

- Flags: `--transcripts`, `--source`, `--speaker`, `--out`,
  `--emotion-label`, `--sample-rate-hz`, `--rebalance-by-emotion`,
  `--character`.
- Needs: bundled ffmpeg.
- Writes: `wavs/`, `manifest.json`, `metadata.csv`.

#### `scripts/voice_pack_train.py`

Stage 3. LoRA fine-tune on the per-speaker dataset. Includes an upstream
T3 loss monkey-patch, mixed-precision, early stopping, and step-level
metric logging.

- Flags: `--manifest`, `--out`, `--base-model`, `--lora-rank`,
  `--lora-alpha`, `--lora-dropout`, `--lr`, `--batch-size`,
  `--grad-accum`, `--epochs`, `--max-steps`, `--warmup-ratio`,
  `--weight-decay`, `--mixed-precision`, `--early-stopping-patience`,
  `--seed`, `--save-every-n-steps`, `--eval-every-n-steps`,
  `--dry-run`, `-v`.
- Needs: CUDA GPU, PyTorch + PEFT + Chatterbox.
- Writes: `config.json`, `manifest_snapshot.json`, `run_command.txt`,
  `adapter/`, `training.log`.

#### `scripts/voice_pack_package.py`

Stage 4. Assembles the on-disk pack format the GUI knows how to import.

- Flags: `--out`, `--name`, `--language`, `--tier`, `--tier-reason`,
  `--total-source-minutes`, `--sample`, `--adapter`, `--reference`,
  `--emotion-coverage`, `--base-model`, `--notes`, `--slug`,
  `--overwrite`.
- Needs: PyYAML.
- Writes: `<slug>/meta.yaml`, `sample.wav`, plus either `adapter.pt`
  (LoRA tier) or `reference.wav` (few-shot tier).

#### `scripts/record_voice_sample.py`

Interactive voice-clone prep. Records a WAV through ffmpeg, runs the v7
preflight QA (SNR, loudness, duration, clipping), optionally synthesizes
a test sentence with the resulting sample as the reference.

- Flags: `--list-devices`, `--input-device`, `--duration`,
  `--sample-rate`, `--output`, `--use-existing`, `--skip-preflight`,
  `--skip-trim`, `--no-playback`, `--no-countdown`, `--synthesize`,
  `--synthesize-file`, `--synthesis-output`, `--tts-device`,
  `--chunk-chars`.
- Needs: ffmpeg; optional torch + chatterbox for the synthesis step.

#### `scripts/ci_status.py`

Watches GitHub Actions runs from the terminal. Useful when you've just
pushed a release tag and want to see the build go green without
flipping to the browser.

- Flags: `-n/--limit`, `--watch`, `--interval`.
- Needs: nothing (stdlib-only, public GitHub API).

### Tier 2 — dev-only utilities

These are smaller targeted tools. Documented less, less polish, but
real CLIs.

- **`scripts/check_spec_runner_imports.py`** — CI guard that compares
  the imports of `generate_chatterbox_audiobook.py` against the spec's
  `datas=` list. Zero arguments; exit 0 on pass, 1 on drift.
- **`scripts/voice_pack_characters.py`** — optional clustering step
  between analyze and export that separates distinct character voices
  inside one speaker.
- **`scripts/diagnose_turo_swallowing.py`** — long-run MP3 quality
  diagnostic. Splits an MP3 into windows, reports speech / silence
  metrics, flags monotonic degradation.
- **`scripts/stress_test_chatterbox_longrun.py`** — Tier 1 validation
  for long-run state-leak fixes. Loops `engine.generate()` N times,
  collects per-call stats, flags drift.
- **`scripts/generate_audiobook_parallel.py`** — early Edge-TTS
  parallel runner. Predates the GUI's in-process path. No argparse;
  positional `pdf out [concurrency]`. **Candidate for removal** — the
  GUI's own path is the canonical Edge-TTS pipeline now.
- **`dev_chatterbox_fi.py`** at the repo root — Finnish smoke test for
  iterating on Chatterbox params (`--cfg-weight`, `--exaggeration`,
  `--temperature`, etc.).
- **`dev_qwen_tts.py`** at the repo root — abandoned Qwen3-TTS
  experiment. **Candidate for removal** — Finnish unsupported,
  acknowledged as dropped.

### Tier 3 — build / asset generators (no CLI)

Run once after design changes; nobody calls these from the command
line during normal work.

- **`scripts/generate_icons.py`** — renders 24 PNG icons from PIL
  vectors. No flags.
- **`scripts/generate_social_preview.py`** — renders the GitHub social
  preview card. No flags.

### Tier 4 — GUI entry points (not CLIs, but invoked from the shell)

- **`src/main.py`** — `python -m src.main` launches the unified GUI.
  No arguments.
- **`src/gui_unified.py`** — same entry, also invokable as
  `python -m src.gui_unified`. Accepts `--self-test` for headless
  smoke testing.
- **`src/launcher.py`** — legacy simple launcher, frozen separately by
  `audiobookmaker_launcher.spec`. Accepts `--self-test`.

## The gap

The GUI today exposes 31 distinct user actions (Convert, Make Sample,
Preview, Import voice pack, Test voice, Set language / engine / voice,
Browse reference audio, Set speed, change output mode, install engines,
auto-update, and so on — see `docs/ARCHITECTURE.md` for the
breakdown). Of those, **only one** has a direct CLI equivalent today:
"Convert book" via `generate_chatterbox_audiobook.py`, and that
script only covers the Chatterbox engine.

What a CLI user cannot do without writing Python:

- Convert a book with Edge-TTS or Piper. (No script wraps the
  in-process engine path.)
- Make a 30 s sample without doing the full book.
- List available voices for a given engine and language.
- Test a single voice without writing a tiny Python harness.
- Import a voice pack into the user data directory.
- Pre-flight a synthesis job for disk space + ETA estimate.
- Configure persistent defaults (language, engine, voice, output mode).
- Trigger the auto-update check or apply an update.

That's the surface to close.

## Design

The proposal is a single console script — `audiobookmaker` — that
exposes every capability above through subcommands. Implemented as a
thin module under `src/cli/` that calls the same backend functions the
GUI calls. No business logic in the CLI layer; if the GUI and the CLI
ever disagree, both are reading the same orchestrator.

### Top-level shape

```
audiobookmaker --help
audiobookmaker --version

audiobookmaker convert <input> [--out PATH] [--language fi|en]
                                [--engine edge|piper|chatterbox]
                                [--voice ID] [--speed -25|0|+25|+50]
                                [--ref-audio PATH] [--voice-pack PATH]
                                [--output-mode single|chapters]
                                [--chunk-chars N] [--resume]
                                [--dry-run]

audiobookmaker sample  <input> [same flags as convert, but
                                synthesizes only ~500 chars]

audiobookmaker preview <text> [--engine ...] [--voice ...]
                                [plays through default audio device]

audiobookmaker voices  list [--engine X] [--language Y]
audiobookmaker voices  test ID [--engine X] [--text "..."]

audiobookmaker packs   list
audiobookmaker packs   import <directory>
audiobookmaker packs   remove <slug>
audiobookmaker packs   info <slug>

audiobookmaker engines list [--installed-only]
audiobookmaker engines install <id>
audiobookmaker engines remove <id>
audiobookmaker engines check <id>

audiobookmaker config  show
audiobookmaker config  set KEY VALUE
audiobookmaker config  reset

audiobookmaker update  check
audiobookmaker update  apply [--silent]

audiobookmaker doctor          # disk, GPU, Python, ffmpeg, engines
audiobookmaker estimate <input> [--engine X]
                                # ETA + audio duration, no synthesis

audiobookmaker pack    analyze   ...   # alias for scripts/voice_pack_analyze.py
audiobookmaker pack    export    ...   # alias for scripts/voice_pack_export.py
audiobookmaker pack    train     ...   # alias for scripts/voice_pack_train.py
audiobookmaker pack    package   ...   # alias for scripts/voice_pack_package.py
audiobookmaker pack    record    ...   # alias for record_voice_sample.py
```

The `pack` subcommands are aliases so the existing scripts keep working
for muscle memory and CI, but new users see a single coherent surface.

### Implementation outline

```
src/cli/
  __init__.py
  __main__.py          # entry point, dispatches subcommands
  convert.py           # wraps synthesis_orchestrator
  sample.py            # wraps sample_helpers + orchestrator
  preview.py           # wraps engine.synthesize + _audio_player
  voices.py            # reads engine_registry, calls list_voices
  packs.py             # wraps src/voice_pack/pack.py
  engines.py           # wraps src/engine_installer.py
  config.py            # wraps src/app_config.py
  update.py            # wraps src/auto_updater.py
  doctor.py            # wraps src/system_checks.py + ffmpeg_path
  estimate.py          # wraps src/duration_estimate.py
  pack_pipeline.py     # thin dispatcher to scripts/voice_pack_*
```

Every subcommand is a function that takes parsed args and returns an
exit code. No subcommand owns synthesis logic; they all call into the
existing modules. This keeps the CLI a presentation layer and prevents
the kind of drift where the GUI fixes a bug and the CLI doesn't.

### Output and exit codes

- Default output is human-readable, single-line per progress step.
- `--json` on any subcommand emits machine-readable progress events
  (the same `ProgressEvent` shape `launcher_bridge.py` already
  produces), one JSON object per line.
- `--quiet` suppresses progress and prints only the final result path.
- Exit codes: 0 success; 1 bad input / validation; 2 missing dependency
  (engine not installed, ffmpeg missing); 3 user cancelled (Ctrl-C);
  4 transient runtime failure (network, GPU); 5 unexpected internal
  error.

### Configuration precedence

Highest wins:

1. Command-line flag.
2. `AUDIOBOOKMAKER_*` environment variable
   (e.g. `AUDIOBOOKMAKER_ENGINE=piper`).
3. The same `~/.audiobookmaker/config.json` the GUI persists.
4. Built-in defaults.

A CLI user who never opens the GUI gets sensible defaults; a user who
uses both gets one set of preferences across surfaces.

## Distribution

The user framing was "like a release I upkeep" — so this is a separate
release artifact, not buried inside the installer.

### Two artifacts per release

1. **`AudiobookMaker-Setup-X.Y.Z.exe`** — what ships today. Bundles the
   GUI plus its own copy of the CLI binary so installed users get both.
2. **`AudiobookMaker-CLI-X.Y.Z-windows-x64.zip`** — new. A standalone
   PyInstaller bundle of the CLI only, plus ffmpeg, plus a `README.txt`
   and the `audiobookmaker.exe` console binary. No installer, no Tk,
   no CustomTkinter. Drop it in a folder, add to PATH, done.

Linux and macOS get the same zip structure when their bundles come
online (currently Windows-only).

### Build pipeline changes

- Add `audiobookmaker_cli.spec` next to `audiobookmaker.spec` and
  `audiobookmaker_launcher.spec`. Three frozen binaries, three specs,
  same `src/` source tree.
- Extend `.github/workflows/build-release.yml` to build, hash, and
  upload the CLI zip as a release asset alongside the installer. Same
  SHA-256-in-release-notes guarantee per `docs/CONVENTIONS.md` so
  power users can verify the download. The CLI does not auto-update
  itself — there's no UI to surface a banner — so the SHA is for
  manual verification only.
- Skip auto-update wiring for the CLI build. CLI users update by
  re-downloading. Document that explicitly in the CLI's `--help`.

### Documentation upkeep

- This file (`docs/CLI.md`) is the canonical reference. Every
  subcommand gets a section once implemented.
- `README.md` gets a short "Command-line use" section pointing here
  and to the release asset.
- The CLI's `--help` text and this doc are generated from the same
  argparse definitions — write a tiny `scripts/render_cli_help.py`
  that walks the parser tree and emits the per-subcommand reference,
  invoked by a pre-commit hook so they cannot drift.

## Versioning

The CLI ships under the same version string as the GUI
(`src/auto_updater.py::APP_VERSION`). One version, two binaries,
guaranteed to agree on engine behaviour. The release-cut skill in
`.claude/skills/release-cut/` already enforces the
APP_VERSION / setup.iss sync gate; extending it to also bump the CLI
spec is one line.

## What this audit does not change

- The existing `scripts/*.py` files keep working. They are still the
  right place for one-off diagnostics and ML pipeline stages. The CLI
  just wraps the user-facing ones with a nicer surface and a stable
  contract.
- The GUI does not call the CLI. Both call the same backend functions.
- No business logic moves. The CLI is a thin shell over
  `synthesis_orchestrator`, `engine_registry`, `voice_pack/pack.py`,
  `app_config`, `auto_updater`, `system_checks`, and
  `duration_estimate`.

## Implementation plan (ordered, smallest first)

Each step is independently shippable.

1. **Scaffold `src/cli/`** with `convert` + `voices list` only.
   Wire `python -m src.cli convert book.pdf` to
   `synthesis_orchestrator.run_inprocess_synthesis()`. No PyInstaller
   work yet — dev-only via `python -m`. Smallest possible useful CLI.
2. **Add `sample`, `preview`, `voices test`, `estimate`, `doctor`.**
   All read-only or short-running. Establishes the subcommand
   pattern and exit-code conventions.
3. **Add `packs` and `engines` subcommands.** Now CLI users have
   feature parity with the GUI's settings panel.
4. **Add `config show / set / reset`.** Closes the persistence gap.
5. **Build pipeline.** Add `audiobookmaker_cli.spec` and the CI job
   to produce the standalone zip artifact.
6. **Auto-doc.** Write `scripts/render_cli_help.py` and a pre-commit
   hook so this file's reference section regenerates from argparse.
7. **Deprecate `generate_audiobook_parallel.py` and
   `dev_qwen_tts.py`.** Delete after one release where the CLI's
   `convert` covers the parallel case.

Each step is small enough to land as a single PR. None of them touch
the GUI's behaviour.
