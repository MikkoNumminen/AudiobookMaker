# AudiobookMaker — command-line interface

The `audiobookmaker` command converts books to speech, manages voices and
engines, and exposes every conversion capability that the GUI provides —
without a window.

---

## Quick start

Install from the repo, verify it works, and convert your first book in three
commands.

```bash
pip install -e .
audiobookmaker doctor
audiobookmaker convert book.pdf
```

`doctor` checks that ffmpeg is on the PATH, that at least one TTS engine is
ready, and that you have free disk space. If everything is green you are
ready to convert.

The output MP3 lands in the configured output directory
(`~/.audiobookmaker/` by default). The path is printed on the last line of
the output.

---

## Installation

### From source (recommended for developers)

Clone the repo and install in editable mode:

```bash
git clone https://github.com/mikkopetteri/AudiobookMaker
cd AudiobookMaker
pip install -e .
```

The `audiobookmaker` console script is registered by `pyproject.toml`. After
installation it is on your PATH so you can run it from any directory.

### Standalone binary

A standalone Windows binary (`audiobookmaker.exe`) is planned as a future
release artifact — a self-contained zip with ffmpeg bundled so nothing else
needs to be installed. It is not available yet. Watch the
[releases page](https://github.com/mikkopetteri/AudiobookMaker/releases) for
the first `AudiobookMaker-CLI-*.zip` asset.

---

## Common workflows

### 1. Convert one book end-to-end with the default engine

```bash
audiobookmaker convert book.epub
```

AudiobookMaker reads the engine, language, and voice from
`~/.audiobookmaker/config.json`. On a fresh install the engine defaults to
`edge` (Edge-TTS, no GPU needed, no extra install). The output path is
printed when synthesis finishes.

Override any setting for a single run without changing your saved config:

```bash
audiobookmaker convert book.epub --engine piper --language fi --voice fi_FI-aho-medium
```

### 2. A/B test engines with `sample`

`sample` synthesizes only the first ~500 characters of a book — enough to
hear what an engine sounds like without waiting for a full run.

```bash
audiobookmaker sample book.pdf --engine edge
audiobookmaker sample book.pdf --engine piper
```

Each call writes a `*_sample.mp3` next to the full output path. Compare the
two files in any audio player.

### 3. Switch the default engine via `config set`

Set your preferred engine once so every subsequent `convert` call uses it
without extra flags:

```bash
audiobookmaker config set engine_id piper
audiobookmaker config set language fi
audiobookmaker config show
```

From this point `audiobookmaker convert book.epub` uses Piper without any
`--engine` flag.

To check what your active config is in a shell script:

```bash
audiobookmaker config show --quiet
# Output: one key=value line per field
```

### 4. Batch convert a folder

`audiobookmaker convert` takes one file at a time, but a shell loop covers
a whole folder:

```bash
# bash / zsh
for f in ~/books/*.epub; do
    audiobookmaker convert "$f" --quiet
done
```

```powershell
# PowerShell
Get-ChildItem ~/books -Filter *.epub | ForEach-Object {
    audiobookmaker convert $_.FullName --quiet
}
```

`--quiet` suppresses the per-chunk progress lines and prints only the final
output path, which makes the batch output readable.

### 5. Stream `--json` progress through jq

Every synthesis subcommand accepts `--json`, which emits one JSON object per
line (NDJSON). This lets you pipe progress events into any JSON-aware tool.

Show only the percentage done as synthesis runs:

```bash
audiobookmaker convert book.pdf --json | jq -r 'select(.kind=="chunk") | "\(.total_done)/\(.total_chunks)"'
```

Wait for completion and print the output path:

```bash
out=$(audiobookmaker convert book.pdf --json | jq -r 'select(.kind=="done") | .output_path')
echo "Written to: $out"
```

---

## Subcommand reference

Each subcommand has its own `--help`. The table below is a quick index;
the auto-generated detail follows.

| Subcommand | What it does |
|---|---|
| `convert INPUT` | Convert a PDF, EPUB, or TXT file to an MP3 audiobook |
| `sample INPUT` | Synthesize the first ~500 characters as a quick quality check |
| `preview TEXT` | Speak a short string through the system audio output (no file saved) |
| `voices list` | List all voices across installed engines, with optional filters |
| `engines list` | List all registered TTS engines and their availability |
| `engines install ID` | Download and install a TTS engine |
| `engines remove ID` | Remove an installed engine's assets |
| `engines check ID` | Report whether a specific engine is ready to use |
| `packs list` | List installed voice packs |
| `packs import DIR` | Validate and install a voice pack from a directory |
| `packs remove SLUG` | Delete an installed voice pack |
| `packs info SLUG` | Print metadata for an installed voice pack |
| `config show` | Print all persistent config fields (or one field) |
| `config set KEY VALUE` | Set one config field and save |
| `config reset` | Reset config (or one field) to defaults |
| `config path` | Print the path to the config file |
| `update check` | Query GitHub Releases for a newer version |
| `update apply` | Download, verify (SHA-256), and install the latest version |
| `doctor` | Run system health checks and report engine readiness |

<!-- BEGIN_GENERATED_REFERENCE -->
<!-- This block is auto-generated by scripts/render_cli_help.py.
     Do not edit by hand; edit the argparse parsers and re-run the
     renderer (or the pre-commit hook will re-run it for you). -->
### `audiobookmaker convert`

Convert a book file (PDF, EPUB, or TXT) to an MP3 audiobook.

| Flag | Description |
|------|-------------|
| `INPUT` | Path to a PDF, EPUB, or TXT file. |
| `--engine ID` | TTS engine to use (e.g. edge, piper, chatterbox_fi). Default from config; fallback: edge. Env: AUDIOBOOKMAKER_ENGINE. |
| `--language LANG` | Language code (e.g. fi, en). The Language picker in the GUI exposes fi + en; other codes route through to the engine, which will reject anything it doesn't speak. Default from config; fallback: auto-detect from locale. Env: AUDIOBOOKMAKER_LANGUAGE. |
| `--voice ID` | Voice id (engine-specific). Default: engine's default voice for the chosen language. Env: AUDIOBOOKMAKER_VOICE. |
| `--output PATH` | Output MP3 path. Default: <output_dir>/<book-stem>.mp3. Env: AUDIOBOOKMAKER_OUTPUT. |
| `--ref-audio PATH` | Reference audio file for voice-cloning engines. |
| `--voice-pack PATH` | Path to a voice pack directory (Chatterbox only). |
| `--chunk-chars N` | Characters per synthesis chunk (Chatterbox only; default 300). |
| `--dry-run` | Print what would happen without synthesizing. |
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker sample`

Convert the first ~500 characters of a book to MP3 as a quick quality check before running the full conversion.

| Flag | Description |
|------|-------------|
| `INPUT` | Path to a PDF, EPUB, or TXT file. |
| `--engine ID` | TTS engine to use (e.g. edge, piper, chatterbox_fi). Default from config; fallback: edge. Env: AUDIOBOOKMAKER_ENGINE. |
| `--language LANG` | Language code (e.g. fi, en). The Language picker in the GUI exposes fi + en; other codes route through to the engine, which will reject anything it doesn't speak. Default from config; fallback: auto-detect from locale. Env: AUDIOBOOKMAKER_LANGUAGE. |
| `--voice ID` | Voice id (engine-specific). Default: engine's default voice for the chosen language. Env: AUDIOBOOKMAKER_VOICE. |
| `--output PATH` | Output MP3 path. Default: <output_dir>/<book-stem>.mp3. Env: AUDIOBOOKMAKER_OUTPUT. |
| `--ref-audio PATH` | Reference audio file for voice-cloning engines. |
| `--voice-pack PATH` | Path to a voice pack directory (Chatterbox only). |
| `--chunk-chars N` | Characters per synthesis chunk (Chatterbox only; default 300). |
| `--dry-run` | Print what would happen without synthesizing. |
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker preview`

Synthesize a short text string and play it through the system audio output. Nothing is saved to disk.

| Flag | Description |
|------|-------------|
| `TEXT` | The text to speak. |
| `--engine ID` | TTS engine to use (e.g. edge, piper, chatterbox_fi). Default from config; fallback: edge. Env: AUDIOBOOKMAKER_ENGINE. |
| `--language LANG` | Language code (e.g. fi, en). The Language picker in the GUI exposes fi + en; other codes route through to the engine, which will reject anything it doesn't speak. Default from config; fallback: auto-detect from locale. Env: AUDIOBOOKMAKER_LANGUAGE. |
| `--voice ID` | Voice id (engine-specific). Default: engine's default voice for the chosen language. Env: AUDIOBOOKMAKER_VOICE. |
| `--output PATH` | Output MP3 path. Default: <output_dir>/<book-stem>.mp3. Env: AUDIOBOOKMAKER_OUTPUT. |
| `--no-play` | Synthesize only — do not play audio. Prints the tempfile path on stdout. The caller is responsible for deleting the file. |
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker voices list`

List voices across all engines, or filtered by engine / language.

| Flag | Description |
|------|-------------|
| `--engine ID` | Filter to voices of a specific engine (e.g. edge, piper, chatterbox_fi). |
| `--language LANG` | Filter to voices for a specific language (fi or en). |
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker engines list`

| Flag | Description |
|------|-------------|
| `--installed-only` | Only show engines that are currently available. |
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker engines install`

| Flag | Description |
|------|-------------|
| `ID` | Engine id (e.g. piper, chatterbox_fi). |
| `--yes` | Skip prompts. |
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker engines remove`

| Flag | Description |
|------|-------------|
| `ID` | Engine id to remove. |
| `--yes` | Skip confirmation. |

---

### `audiobookmaker engines check`

| Flag | Description |
|------|-------------|
| `ID` | Engine id to check. |
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker packs list`

| Flag | Description |
|------|-------------|
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker packs import`

| Flag | Description |
|------|-------------|
| `DIRECTORY` | Source pack directory. |
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker packs remove`

| Flag | Description |
|------|-------------|
| `SLUG` | Pack slug (folder name). |
| `--yes` | Skip confirmation prompt. |
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker packs info`

| Flag | Description |
|------|-------------|
| `SLUG` | Pack slug (folder name). |
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker config show`

| Flag | Description |
|------|-------------|
| `KEY` | Field name to show. Omit to show all fields. |
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker config set`

| Flag | Description |
|------|-------------|
| `KEY` | Field name. |
| `VALUE` | New value. |
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker config reset`

| Flag | Description |
|------|-------------|
| `KEY` | Field name to reset. Omit to reset entire config. |
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker config path`

| Flag | Description |
|------|-------------|
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker update check`

Query GitHub Releases and report whether this build is current.

| Flag | Description |
|------|-------------|
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker update apply`

Download the latest installer, verify its SHA-256, and run it.

| Flag | Description |
|------|-------------|
| `--yes` | Skip the confirmation prompt and apply immediately. |
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |

---

### `audiobookmaker doctor`

Run system health checks and report which engines are ready.

| Flag | Description |
|------|-------------|
| `--json` | Emit one JSON object per line (NDJSON format, ProgressEvent shape). |
| `--quiet` | Suppress progress; print only the final output path. |
<!-- END_GENERATED_REFERENCE -->

---

## Exit codes

Every subcommand follows this table.

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Bad input or validation failure |
| 2 | Missing dependency (engine not installed, ffmpeg absent, SHA-256 mismatch) |
| 3 | User cancelled (Ctrl-C or answered "N" at a prompt) |
| 4 | Runtime failure (network, GPU, synthesis error) |
| 5 | Unexpected internal error |

`engines check` is an exception: it exits 2 when the engine is not available,
not because a dependency is broken but because the check itself is the
question. Use it in scripts to guard conditional installs:

```bash
audiobookmaker engines check piper || audiobookmaker engines install piper --yes
```

---

## Configuration

AudiobookMaker stores user preferences in `~/.audiobookmaker/config.json`.
The GUI and the CLI read and write the same file, so changing a setting in
one surface affects the other.

### Reading and writing config

```bash
# Show everything
audiobookmaker config show

# Show one field
audiobookmaker config show engine_id

# Set the default engine
audiobookmaker config set engine_id piper

# Reset one field to its built-in default
audiobookmaker config reset engine_id

# Reset everything
audiobookmaker config reset

# Find the file
audiobookmaker config path
```

### Environment variable overrides

Each flag that participates in the precedence chain has a corresponding
`AUDIOBOOKMAKER_*` environment variable. Set these in your shell profile or
in a process supervisor to override config without editing the JSON file.

| Flag | Env var |
|------|---------|
| `--engine` | `AUDIOBOOKMAKER_ENGINE` |
| `--language` | `AUDIOBOOKMAKER_LANGUAGE` |
| `--voice` | `AUDIOBOOKMAKER_VOICE` |
| `--output` | `AUDIOBOOKMAKER_OUTPUT` |

### Precedence

Highest priority wins:

1. Command-line flag (e.g. `--engine chatterbox_fi`)
2. Environment variable (e.g. `AUDIOBOOKMAKER_ENGINE=chatterbox_fi`)
3. Persisted config (`~/.audiobookmaker/config.json`)
4. Built-in default (`edge` for engine, `fi` for language)

This means you can set a global default in config, override it per-session
with an env var, and override that further with a CLI flag for a single run.

### Script-friendly config reads

`config show --quiet` emits one `key=value` line per field with
shell-safe quoting. Values containing spaces or special characters are
wrapped in single quotes:

```bash
eval "$(audiobookmaker config show --quiet)"
echo "Current engine: $engine_id"
```

---

## JSON output

Pass `--json` to any synthesis or list subcommand to get NDJSON output
(one JSON object per line, flushed immediately). This is designed for
piping into `jq`, logging to a file, or consuming from another process.

The event shape matches the `ProgressEvent` dataclass from
[`src/launcher_bridge.py`](src/launcher_bridge.py):

```json
{"kind": "chunk", "raw_line": "chunk 12/340", "output_path": null, "total_done": 12, "total_chunks": 340, "chapter_idx": 0, "chapter_total": 1, "chunk_idx": 12, "chunk_total": 340, "elapsed_s": 14.2, "eta_s": 383.0, "rtf": 0.11, "returncode": null}
```

Event kinds:

| Kind | When |
|------|------|
| `log` | Informational message |
| `chunk` | One synthesis chunk completed |
| `chapter_start` | A chapter is starting |
| `chapter_done` | A chapter finished |
| `full_done` | All chapters merged into the final MP3 |
| `done` | Synthesis complete; `output_path` is set |
| `error` | A recoverable error; `raw_line` has the message |
| `exit` | Subprocess exited; `returncode` is set |

The `done` event carries the final output path:

```json
{"kind": "done", "output_path": "/home/user/.audiobookmaker/book.mp3", ...}
```

`--dry-run` with `--json` emits a single object describing what would
happen without synthesizing:

```json
{"dry_run": true, "kind": "convert", "input": "book.pdf", "engine": "edge", "language": "fi", "voice": null, "output": "/home/user/.audiobookmaker/book.mp3", "ref_audio": null, "voice_pack": null, "chunk_chars": null}
```

`voices list --json` and `engines list --json` emit one object per row
(same shape as the human-readable columns). `doctor --json` emits one
object per check.

---

## Out of scope / future work

The following capabilities exist in the repo but are not part of the
`audiobookmaker` CLI surface described in this document.

**Voice-cloning pipeline.** The scripts under `scripts/` —
`voice_pack_analyze.py`, `voice_pack_export.py`, `voice_pack_train.py`,
`voice_pack_package.py` — are developer tools for creating new voice packs
from audio sources. They run directly as `python scripts/voice_pack_*.py`
and are documented separately. They are not wrapped under the
`audiobookmaker` entry point.

**Standalone binary.** A PyInstaller-frozen `audiobookmaker.exe` for users
who do not have Python is in progress. The spec file
`audiobookmaker_cli.spec` is a parallel task and has not shipped in a
release yet.

**Auto-doc generator.** The `<!-- BEGIN_GENERATED_REFERENCE -->` block in
the Subcommand reference section above will be filled by
`scripts/render_cli_help.py` once that script lands. Until then the
section contains only the hand-written index table.

---

## Appendix — Architecture and design rationale

This section is for maintainers who want to understand how the CLI is
structured and why.

### Why a separate CLI layer

The GUI is the primary surface for end users. A dedicated CLI serves three
additional audiences:

- **Power users and sysadmins.** Batch-converting a shelf of books without
  clicking through dialogs, or running on headless servers.
- **Pipeline integrators.** Feeding AudiobookMaker into a larger automation
  flow — cron jobs, Makefiles, other scripts.
- **Developers and testers.** Reproducing bugs, comparing engines, or
  running stress tests against the backend directly.

The CLI does not own synthesis logic. It is a presentation layer over the
same backend modules the GUI calls: `synthesis_orchestrator`,
`engine_registry`, `voice_pack`, `app_config`, `auto_updater`, and
`system_checks`. If the GUI fixes a bug, the CLI picks it up automatically.

### Module layout

```
src/cli/
  __init__.py
  __main__.py       entry point; dispatches subcommands
  _common.py        exit codes, shared flags, ProgressEvent printer
  convert.py        wraps synthesis_orchestrator
  sample.py         wraps sample_helpers + convert.run()
  preview.py        wraps engine.synthesize + _audio_player
  voices.py         reads engine_registry, calls list_voices
  engines.py        wraps src/engine_installer.py
  packs.py          wraps src/voice_pack/
  config.py         wraps src/app_config.py
  update.py         wraps src/auto_updater.py
  doctor.py         wraps src/system_checks.py + ffmpeg_path
```

Each module exposes `add_parser(subparsers)` and stores `run` as the
`func` default so `__main__.py` only needs to call `args.func(args)`.

### Subprocess vs. in-process engines

Edge-TTS and Piper run in-process via `synthesis_orchestrator.run_inprocess_synthesis()`.

Chatterbox (`chatterbox_fi`) runs as a subprocess because it requires a
separate virtualenv with PyTorch and an NVIDIA GPU. `convert.py` detects
which path to take via `engine.uses_subprocess` and dispatches accordingly.
The `preview` subcommand does not support subprocess engines — use `sample`
instead for a quick Chatterbox listen.

### Deferred features

`--speed` (per-engine speed override) is deferred from v1. The underlying
`TTSEngine.synthesize()` contract does not accept a speed parameter. Wiring
it requires a base-class change (business logic outside the CLI layer) and
will be added once the engine interface grows a `speed` argument.

### Existing scripts

The scripts under `scripts/` that predate the CLI remain in place. They are
still the right tools for one-off diagnostics and ML pipeline stages. The
`audiobookmaker` entry point wraps the user-facing conversion surface only.

Two earlier scripts have been removed because the CLI's `convert` now
covers their use cases: `scripts/generate_audiobook_parallel.py` (an
Edge-TTS parallel runner) and the repo-root `dev_qwen_tts.py` (an
abandoned Qwen3-TTS feasibility experiment).

### Versioning

The CLI ships under the same version string as the GUI
(`APP_VERSION` in [`src/auto_updater.py`](src/auto_updater.py)). One
version number, one set of engine semantics.
