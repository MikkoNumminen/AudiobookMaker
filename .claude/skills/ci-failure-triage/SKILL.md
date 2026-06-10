---
name: ci-failure-triage
description: When CI fails on a release tag or PR, walk the failure modes in the right order against a recipe library built from the project's fix(ci) commit history. Use whenever the user says "CI is red", "the build failed", "tag failed CI", "release-build broke", or any time gh run list shows a recent failure on master or a release tag.
---

# CI failure triage

Walk a failing GitHub Actions run to a known fix in four steps. The
recipe table below encodes every repeated failure shape observed in this
repo. Match the log output to a recipe before writing any new code.

## Why this skill exists

The last 20 `fix(ci):` commits in this repo all fix one of six failure
shapes. Without a recipe table, each failure restarts investigation from
scratch: open the workflow file, re-read the ignore list, grep the spec,
compare ffmpeg pins. That takes 20–40 minutes. The table cuts it to
under ten.

Shapes that recur:

- Tk-thread hang from headless GHA Windows (multiple commits across
  weeks).
- pytest-timeout late-fire crashing the build after all tests pass.
- ffmpeg autobuild pin rotating off the BtbN release page.
- Spec runner-import drift when a new `src/` module is added.
- Version drift between `APP_VERSION` and `setup.iss`.
- `STATUS_STACK_BUFFER_OVERRUN` in voice-pack-analyze on Windows CI.

## Phase 1 — identify the failing run

```bash
gh run list --limit 5 --repo <owner>/<repo>
```

Pick the run marked `failure`. Note the run ID.

```bash
gh run view <run-id> --log-failed
```

Capture the first failing step name and the first error line. That pair
is the symptom you match against the recipe table.

### Phase 1.5 — local env sanity check (fast-path)

Before assuming the failure is CI-specific, run the CLI's `doctor` and
check whether your local env is even in a state where the tests *could*
pass:

```bash
audiobookmaker-cli doctor --json
```

The terminal line emitted is:

```json
{"kind": "summary", "status": "pass|fail", "required_missing": [...], "exit_code": 0|2}
```

If `status` is `fail` and `required_missing` is non-empty, your local
env is broken in a way that matters — fixing it locally may also be
the fix for CI (or at least lets you reproduce). If `status` is `pass`,
the failure is CI-environment-specific and the recipe table below is
where to look. This is faster than `pytest` for the
"is-anything-missing" question because it doesn't run the test suite —
it probes engine availability, ffmpeg, ocrmypdf, etc. in one shot.

## Phase 2 — match against the recipe table

| # | Symptom (step name + first error line) | Root cause | Fix |
|---|---|---|---|
| R1 | `Run tests` / `pytest-timeout` fires after all tests complete, `AssertionError in read_global_capture()` | pytest-timeout's thread method cannot cancel its timer cleanly on Windows; fires during Tcl notifier shutdown | Add `-p no:timeout` to the `python -m pytest` command in `build-release.yml`. Drop `--cov` if present (coverage finalisation widens the hang window). Commit `fix(ci): disable pytest-timeout — thread-method timer fires late on Windows`. |
| R2 | `Run tests` / build hangs indefinitely, then runner kills it | A GUI test file that imports `tkinter` at module level slipped past the `--ignore` list | Check `pytest.ini` and `build-release.yml` ignore list are consistent. Run `git diff HEAD~1 -- pytest.ini .github/workflows/build-release.yml` to see what changed. Add the offending file to both ignore lists. Commit `fix(ci): add <file> to CI ignore list — module-level tkinter import hangs headless runner`. |
| R3 | `Download ffmpeg` / `404` or asset not found on BtbN release page | The `autobuild-YYYY-MM-DD-HH-MM` pin in `build-release.yml` (and `build-launcher.yml`) was removed from the BtbN release page | Go to https://github.com/BtbN/FFmpeg-Builds/releases and find the current `autobuild-*` tag for `ffmpeg-n<ver>-latest-win64-gpl-shared-<ver>.zip`. Update the pin in both `build-release.yml` and `build-launcher.yml`. Commit `fix(ci): bump ffmpeg pin to <new-tag>`. |
| R4 | `Run tests` / `ModuleNotFoundError` or `ImportError` for a `src.*` module | Runner script imports a `src/` file not listed in `audiobookmaker.spec` `datas=` | Run `python scripts/check_spec_runner_imports.py` locally. It prints every `src/` import the runner scripts use that is absent from the spec. Add the missing entry to the `datas=` list in `audiobookmaker.spec`. Commit `fix(ci): add <module> to spec datas — runner import was missing`. |
| R5 | `Build installer` / Inno Setup fails with version mismatch, or auto-update rejects the tag | `APP_VERSION` in `src/auto_updater.py` and the version string in `installer/setup.iss` have drifted | Use the `release-cut` skill to re-align and re-tag. Do not patch by hand — `release-cut` updates both files atomically and re-tags. |
| R6 | `Run tests` / `STATUS_STACK_BUFFER_OVERRUN` crash in voice-pack-analyze subprocess | Voice-pack-analyze loads CUDA models on the CI runner which has no GPU; the native stack overflows | Ensure the analyze orchestrator (`scripts/voice_pack_analyze.py`) passes `--device cpu` when `CUDA_VISIBLE_DEVICES` is unset or empty. The fix is a one-line guard at the subprocess launch site. Commit `fix(ci): force CPU fallback in chunked analyzer when no GPU is available`. |
| R7 | Any download/install step / `404` on a pinned toolchain asset, or a floating dep resolves to a new version that breaks the build | CI toolchain pin drifted: BtbN ffmpeg autobuild tags get deleted; unpinned `pip install pyinstaller` / `Pillow` / floating `pygame` ranges resolve differently than `requirements.txt` | Re-pin to the validated versions, keeping `build-release.yml` and `build-launcher.yml` in sync with each other and with `requirements.txt`. Commit `fix(ci): pin CI toolchain — <what drifted>`. (Ref: 89f09fc) |
| R8 | `Publish release` / SHA-256 guard reports the line missing although release_notes.md clearly has it | PowerShell guard reads the file with `-Raw` (one string) and the regex anchors `^` to document start, not line start — the SHA line near the end never matches | Use the multiline flag: `(?im)^SHA-256:`. Verify it still does NOT match the `CLI: SHA-256:` line. Commit `fix(ci): make the installer SHA-256 release-notes guard regex multiline`. (Ref: 8e0b118) |
| R9 | CLI build / PyInstaller COLLECT aborts with `output directory ... is not empty` on Windows | Two specs collect into `dist/AudiobookMaker/` and `dist/audiobookmaker/` — the same folder on case-insensitive Windows | Give each COLLECT a distinct dist folder (e.g. `dist/audiobookmaker_cli/`) and point the packaging steps at it. Commit `fix(ci): give the CLI build a distinct dist folder to avoid Windows clash`. (Ref: 1b17d33) |

If none of the nine recipes match, go to Phase 4 before writing new code.

## Phase 3 — apply the fix

Apply only the recipe's stated change. Keep the commit message in the
`fix(ci): <verb> — <short reason>` form matching the project's history.
No other files in the commit unless the recipe explicitly says so.

If the failure was on a **release tag** (the run was triggered by a
`v*` push), the tag must be re-cut after the fix lands on master. Do
not patch the tag in place. Use the `release-cut` skill's "Tag re-do"
section — it handles bumping, tagging, pushing, and verifying the
SHA-256 sidecar.

Run the test suite locally before pushing:

```bash
python -m pytest tests/ --ignore=tests/test_record_voice_sample.py \
  --ignore=tests/test_single_instance.py \
  --ignore=tests/test_voice_recorder.py \
  -x -q --tb=short -p no:timeout
```

Then push and watch the run:

```bash
gh run watch --exit-status
```

## Phase 4 — failure shape not in the table

If the log output does not match any recipe:

1. Read the full failed step log: `gh run view <id> --log-failed`.
2. Grep the recent commit log for context:
   `git log --oneline --all | grep -i "fix(ci)"`.
3. Identify the exact line that changed between the last green run and
   this one: `git log --oneline <last-green-sha>..HEAD --` (limit to
   ~20 commits; don't spelunk into dependency repos).
4. Fix the root cause, commit with `fix(ci): ...`, and add a new row
   to the recipe table in this file so the next session does not repeat
   the investigation.

## Things NOT to do

- Do not re-run a failing CI run without a code change. GHA is
  deterministic on Windows-2022; the same input produces the same
  failure.
- Do not add `--continue-on-collection-errors` or similar flags to
  mask import failures. Find which file is failing and add it to the
  ignore list or fix the import.
- Do not patch the ignore list in `pytest.ini` alone. The CI workflow
  `build-release.yml` has its own `--ignore=` list; both must be
  consistent. Check both files every time.
- Do not re-tag a release by hand. Use the `release-cut` skill — a
  manually applied tag that misses the SHA-256 sidecar breaks auto-
  update for every existing user (P0).
- Do not raise the chunked-analyzer worker count above 1 on CI. The
  runner has no GPU; concurrent native model loads cause stack
  overflows.
- Do not skip the local test run before pushing the fix. CI is slow;
  a broken fix wastes another 10-minute cycle.
