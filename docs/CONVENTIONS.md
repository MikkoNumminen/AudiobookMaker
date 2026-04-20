# Conventions

Code style, commit format, branch hygiene, and the TODO.md protocol.
Read this once; it shouldn't change often.

## Python style

- Python 3.11+. Use modern syntax: `X | None`, `list[str]`, `dict[K, V]`,
  `match` statements where they help.
- PEP 8, 4-space indent, ~88-char soft line length (Black-ish, but no
  formatter is enforced — match the surrounding style).
- Type hints on every public function and method, every dataclass field,
  every public class attribute. Internal helpers should be hinted too if
  the types aren't obvious.
- Use `from __future__ import annotations` at the top of files where it
  buys you cleaner forward references.
- Docstrings: short, imperative ("Return …", "Raise … if …"). One line is
  often enough. No multi-paragraph essays.
- Comments: only when the *why* is non-obvious — a hidden constraint, a
  workaround, a subtle invariant. Don't restate the code.
- Naming: `snake_case` for functions and variables, `PascalCase` for
  classes, `_leading_underscore` for module-private. Test classes are
  `class TestX:` (pytest finds them).

## Imports

- Group: stdlib, third-party, `src.*`. Blank line between groups.
- Avoid wildcard imports.
- Heavy optional dependencies (`torch`, `piper`, `voxcpm`, `edge_tts`)
  go inside the function or method that uses them — keeps app startup
  fast and lets `check_status()` report missing-dep errors cleanly.
- `from src.X import Y` form preferred over `import src.X as X`.

## Error handling

- Validate at boundaries (user input, external APIs, file I/O). Trust
  internal code.
- Catch specific exceptions, not `except Exception:` — exceptions are
  load-bearing; broad catches hide regressions.
- For user-facing errors, route through `_STRINGS` so the message is
  bilingual. Never hardcode Finnish or English literals in
  `messagebox.showerror(...)` or `_fail(...)`.
- For developer-facing log lines, plain English in `_append_log(...)`
  is fine.

## Tests

- Every new feature ships with tests. Bug fixes ship with a regression
  test that fails on the old code.
- One file per `src/` module: `tests/test_<module>.py`.
- Test naming: `def test_<thing>_<expected_behavior>(...)`. Class
  groupings (`class TestSomething:`) for related tests are encouraged.
- Use `pytest` fixtures for shared setup. Common fixtures live in
  `tests/conftest.py` (`clean_registry`, etc.). Don't duplicate.
- Mock at the module boundary, not inside the unit under test —
  patch `src.tts_engine.normalize_finnish_text`, not `tts_engine`'s
  internal implementation details.
- GUI tests share one Tk root via `tests/test_gui_e2e.py::_shared_app`.
  Don't create new `Tk()` instances — Tkinter crashes the interpreter.
- Run before every commit: `python -m pytest tests/ -x -q --tb=short`
  (the pre-commit hook does this automatically).

## Commits

- One logical change per commit. If the diff has two unrelated edits,
  split them.
- Conventional Commits format:
  ```
  <type>(<scope>): <subject>
  ```
  Types in use: `feat`, `fix`, `refactor`, `test`, `docs`, `ci`,
  `chore`, `security`, `i18n`. Scope is the affected module or area
  (`gui`, `updater`, `normalizer`, `engine-installer`, `ci`, etc.).
- Subject in present-tense imperative, no trailing period, no capital:
  ```
  fix(updater): require SHA-256 in release notes before installing
  ```
- Body (optional) explains *why* the change was needed, not what — the
  diff already shows what.
- **No co-authors, no AI/Claude/assistant attribution, no tool
  signatures.** Anywhere. Not in commit messages, not in PR bodies, not
  in code comments. The repo's contributors list stays clean.
- **No `--no-verify`** unless you have an explicit reason to bypass
  the pre-commit test run, and even then write down why in the commit
  body.

## Branches

- Branch off `master`. Branch name: `kebab-case`, descriptive
  (`audit-fixes`, `dev-docs`, `fix-edge-timeout`).
- Worktrees go under `.claude/worktrees/<name>` so multiple developers
  (or agents) can work in parallel without stepping on each other's
  checkouts.
- Merge to master via fast-forward when possible; merge commit
  (`--no-ff`) when the branch tells a story worth preserving.
- Delete the remote branch after merge:
  `git push origin --delete <branch>`.

## Pull requests (when used)

- Title follows the same Conventional Commits format as commits.
- Body: bullet-list summary of what changed, "Test plan" section with
  a checklist of what was verified.
- Link the TODO.md item if any.

## Use English GUI label names in user-facing prose

Whenever you write narrative prose for a human reader — chat replies,
release notes, README copy, commit messages, PR bodies, GitHub issues —
refer to GUI elements by their **English** label names. Do not paste the
Finnish in-app strings into prose, even though the running app shows
them in Finnish by default.

| Finnish in-app | Use this in prose |
|---|---|
| Kieli | Language |
| Moottori | Engine |
| Ääni | Voice |
| Muunna | Convert |
| Esikuuntele | Preview |
| Tee näyte | Make sample |
| Avaa kansio | Open folder |
| Asetukset | Settings |
| Asenna moottoreita | Install engines |
| Tallenna | Save |
| Tuloste | Output |
| Nopeus | Speed |
| Ref. ääni | Reference audio |
| Suomi | Finnish |
| Kirja | Book |
| Teksti | Text |

The Finnish strings still live in `_STRINGS["fi"]` inside `gui_unified.py`
— that is the actual UI text, leave it alone. The English equivalents
live in `_STRINGS["en"]` already. Code identifiers like `_lang_cb`,
`_engine_cb`, `_voice_cb` are symbol names and stay as-is.

Why: release notes and PR descriptions need to be readable for
contributors who don't speak Finnish, and inline Finnish UI strings
look unprofessional in English prose.

## TODO.md protocol

[TODO.md](../TODO.md) is the shared task list across all parallel
developers and agents. The file's own header lists the mandatory
rules — re-read it before starting any task. Highlights:

- **Move your task to "In Progress" with your name tag *before* you
  touch any files.** `[mikko, master]`, `[Claude 1, audit-fixes]`,
  etc. This is how parallel workers avoid collisions.
- **Don't pick up an item that already has an owner tag.** Pick
  something else or wait.
- **Don't remove your tag mid-task.** It stays in In Progress until
  the work is committed and pushed.
- **Every item has a size estimate** — 🟢 small, 🟡 medium, 🔴 large.
- **Items destined for an LLM use a model marker** — ⚡ Sonnet for
  mechanical tasks, 🧠 Opus for ones needing judgement.
- TODO.md is mixed Finnish/English — match the language the existing
  section uses.
- Remove items as soon as they're done. No "Recently Completed"
  archive — `git log` is the history.

## Docs

- Keep `DEVELOPMENT.md` short. It points to other docs; it doesn't
  duplicate them.
- `docs/ARCHITECTURE.md` owns the module map and Mermaid diagrams.
- `docs/CONVENTIONS.md` (this file) owns style and process.
- `docs/tts_text_normalization_cases.md` owns the normalizer test
  inventory.
- When you change a boundary the docs describe (a new engine, a new
  mixin, a renamed module), update the doc in the same commit. Drift
  is what kills doc usefulness.

## When to ask first

- Anything that touches the auto-update flow (`auto_updater.py`,
  `installer/setup.iss`, `cleanup.py`) — these run on every user's
  machine on every release. Mistakes are expensive.
- Anything that affects `master` directly without a branch.
- Anything that would create a `CLAUDE.md`, `.claude/` directory in
  the repo, or a co-authored commit. Don't.

## Keep `cleanup.py` current ("the trash collector")

`src/cleanup.py` runs silently on every app launch in frozen mode. It
detects old installs, rescues user MP3s from them, removes orphan
shortcuts, and rmtrees the leftovers. Whenever you change one of these
things, ask yourself whether `cleanup.py` needs to learn about it —
**and if the answer is yes, update it in the same commit.**

Triggers that require a `cleanup.py` update:

| Change | What to update |
|--------|----------------|
| New install location (e.g. Inno Setup default path changes, you ship a portable bundle, you add a WinGet manifest) | `_candidate_install_dirs()` |
| New output file type or location (today: MP3 at `{app}` root, legacy `{app}/audiobooks/*.mp3`) | `_rescue_user_mp3s()` — add the new glob so users never lose generated files |
| New Start-Menu / Desktop / Taskbar shortcut path | `_candidate_shortcut_dirs()` |
| User-writable file the user might have under the install dir that *isn't* an MP3 (e.g. custom voice presets, logs they care about) | `_rescue_user_mp3s()` or a new rescue helper; don't silently nuke user data |
| New app that shares the "AudiobookMaker" name space | scanner's `_is_audiobook_install()` identity check |

Checklist before merging any change that touches install paths, output
paths, or shortcut creation:

- [ ] Will `find_old_installs()` still find it?
- [ ] Will `remove_old_install()` preserve the user's generated files?
- [ ] Are the tests in `tests/test_cleanup.py` updated for the new case?

Why this rule exists: a botched cleanup is a data-loss incident that
users discover only *after* their audiobooks are gone. Much cheaper to
audit the checklist on every path-touching PR than to write an apology
later.

## Finnish text normalizer — lexicon vs. new pass

The Finnish normalizer pipeline ([src/tts_normalizer_fi.py](../src/tts_normalizer_fi.py))
is a sequence of ~20 lettered passes (A–N). Each pass owns one phenomenon
and has load-bearing ordering constraints with its neighbours; the
dispatcher docstring enumerates them.

When a mispronunciation report lands ("please fix `patriotismi`", "page
ranges read wrong", "acronym pronounced as a word"), classify the fix
**before** you touch code:

- **Lexicon / data extension** — the phenomenon is already covered by an
  existing pass, but the pass's whitelist is missing the specific word
  or stem. Fix is a YAML edit in `data/fi_*.yaml` plus a regression
  test. Examples: new `-ismi` stem, new `-tio` loanword, new
  abbreviation expansion for Pass K. No code change, no ordering
  change, no invariants-docstring edit.
- **Bug in an existing pass** — the pattern/regex is wrong, over-fires,
  or under-fires. Fix is in the pass function plus a regression test.
  No ordering change.
- **Genuinely new phenomenon** — nothing in the pipeline addresses it.
  Only then add a new pass, wire it into the dispatcher at the correct
  slot, and add a new invariants bullet to the docstring explaining
  what must run before and after it.

Don't reach for "new pass" when "new lexicon entry" or "fix existing
pass" is the right shape. New passes add ordering surface area that
later changes have to reason about forever. See
[docs/tts_text_normalization_cases.md](tts_text_normalization_cases.md)
for the canonical test inventory.

## cuDNN duplicate DLL in `.venv-chatterbox` (ctranslate2 vs. torch)

If you run anything in the Chatterbox venv that touches
faster-whisper (voice-pack analysis, diarization, transcription, some
of the `scripts/voice_pack_*.py` tools) and the process dies with:

```
Could not load symbol cudnnGetLibConfig. Error code 127.
```

...the cause is two copies of `cudnn64_9.dll` sitting in the same
venv. `ctranslate2` ships a small single-file build of the DLL, and
`torch` ships the full modular cuDNN 9 suite (the top-level
`cudnn64_9.dll` plus seven siblings like `cudnn_ops`, `cudnn_graph`,
`cudnn_engines_precompiled`, and so on). Whichever copy Windows' DLL
loader binds first wins, and the other package's code breaks because
its expected symbols aren't there.

**Fix — the runtime guard renames the ctranslate2 copy aside
automatically.** cuDNN 9 has a stable ABI, so ctranslate2 is happy
using torch's DLL instead of its own. The guard at
`src/voice_pack/_cudnn_compat.py` runs on every voice-pack entry
point; when it sees both copies on disk it renames
`ctranslate2/cudnn64_9.dll` to `cudnn64_9.dll.disabled` and prints one
info line. If a `.disabled` sidecar already exists (pip just put the
duplicate back), it silently deletes the fresh duplicate. No developer
action is required after:

- `pip install --upgrade ctranslate2`
- `pip install --force-reinstall ctranslate2`
- A fresh `.venv-chatterbox` setup
- Any `pip install` that pulls `ctranslate2` as a transitive dependency

**Manual fallback for edge cases.** If the auto-fix can't proceed —
file locked by another running process, read-only filesystem,
permission denied — the guard falls back to a stderr warning with the
exact command to run. The commands are:

Bash (Git Bash / WSL):
```bash
mv .venv-chatterbox/Lib/site-packages/ctranslate2/cudnn64_9.dll \
   .venv-chatterbox/Lib/site-packages/ctranslate2/cudnn64_9.dll.disabled
```

PowerShell:
```powershell
Rename-Item `
  -Path .venv-chatterbox\Lib\site-packages\ctranslate2\cudnn64_9.dll `
  -NewName cudnn64_9.dll.disabled
```

Close any process holding the DLL open (old Python REPLs, running
voice-pack scripts) before re-running, or just let the guard retry on
the next invocation once the lock clears.

**Frozen `.exe` bundles are not affected.** The PyInstaller spec
excludes the ctranslate2 copy of `cudnn64_9.dll` from the shipped
bundle, so end users of the installer never see this crash. This is a
dev-venv-only gotcha.

## Auto-update is critical — it must work for every release

Auto-update is the lifeline to existing users. If it breaks, every user
on a previous version has to manually find the GitHub release page,
download the installer, dismiss SmartScreen, and run it themselves.
Most won't. **Treat a broken auto-update path as a P0 — same severity
as a data-loss bug.** v3.7.0 shipped without a SHA-256 hash in the
release notes and locked every existing user out of one-click updates;
that must never happen again.

Mandatory guarantees for every release:

1. **The release notes contain a `SHA-256: <64-hex>` line.** Enforced
   by [.github/workflows/build-release.yml](../.github/workflows/build-release.yml)
   — the build computes the hash from the freshly-built `.exe`, writes
   it into `release_notes.md`, the "Guard — release notes must contain
   SHA-256" step refuses to publish without it, and a post-publish
   verification step re-fetches the live release and re-checks. Do not
   bypass any of these steps. Do not edit release notes after publish
   in a way that strips the SHA line.
2. **A sidecar `AudiobookMaker-Setup-<v>.exe.sha256` asset is uploaded.**
   Provides a second source of truth for [src/auto_updater.py](../src/auto_updater.py)
   to recover from. If the body line ever goes missing, the in-code
   sidecar fallback at `_fetch_sidecar_sha256` self-heals every
   already-installed client the moment a sidecar exists.
3. **The auto-updater error message stays actionable.** When the
   download is blocked, the message must point at the working escape
   (the "Lataa selaimella" / "Download in browser" button next to it).
   See `src/auto_updater.py:download_update`.

Before changing anything in the release pipeline, the auto-updater, or
the release-notes template, ask yourself:

- [ ] Will `check_for_update` still find a SHA-256 (body line OR
      sidecar) for the freshly-built release?
- [ ] Does the CI guard step still match the expected SHA pattern?
- [ ] Will the post-publish verification step still pass?
- [ ] If something fails, does the user see a message that tells them
      what to do, not just what went wrong?

If you have a reason to ship a release that violates any of those
points, the bar is **a written incident-style explanation** in the PR
description naming the affected user cohort and the migration path.
"It was a small change" is not a reason. The user has a small support
team — them — and broken auto-update means every user becomes a
support ticket.
