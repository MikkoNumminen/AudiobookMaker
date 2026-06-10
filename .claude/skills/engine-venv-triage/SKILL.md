---
name: engine-venv-triage
description: Diagnose end-user Chatterbox engine failures (Convert fails, Repair loops, "Could not import module 'LlamaModel'", "Torch not compiled with CUDA enabled", install smoke passes but synthesis fails) using the provenance-first ladder built from the v3.16.0–v3.17.3 field saga. Use whenever a user/tester screenshot shows an engine error dialog, the user says "chatterbox does not work for X", "repair didn't help", "the engine keeps failing", or an engine works after install but Convert errors.
---

# Engine-venv triage

End-user engine failures in the frozen Windows app almost never have
enough information in the *dialog* — the diagnosis lives in the **log
panel** and in **provenance**. This ladder was built from a multi-day
field saga (PRs #107–#113, releases v3.16.0 → v3.17.3) where every
plausible-looking first guess was wrong. Follow the ladder; do not
guess ahead of it.

## Step 0 — what to ask the reporter for

One screenshot that includes **the log panel** (Piilota loki area), not
just the dialog. The log now prints provenance lines at every Convert:

```
Runner: <path to generate_chatterbox_audiobook.py that will run>
Venv:   <path to the venv python that will run it>
[runner] build <RUNNER_BUILD> @ <path that actually ran>
```

Plus the app version in the title bar.

## Step 1 — provenance checks (cheapest, highest yield)

| Observation | Meaning | Fix |
|---|---|---|
| `[runner] build` line **missing** from the log | The runner script on disk predates v3.17.3 → the user's app files are **mixed versions** (a silent auto-update half-applied). Proven possible in the field: a v3.17.2 exe ran a pre-v3.17.2 script. | Have the user download the installer from GitHub releases and run it once (full overwrite). Auto-update will NOT fix it — it thinks the version is current. |
| `Venv:` path differs from `C:\AudiobookMaker\.venv-chatterbox` | Synthesis is using a stray venv while Install/Repair manage the default one — repairs will "succeed" forever without effect. (Resolution order prefers the managed venv since v3.17.3.) | Delete/rename the stray venv, or update the app. |
| Repair says "Asennus epäonnistui" with mismatched-versions text (EN+FI "mismatched versions / eri versioista") | The smoke test ran the old script with `--selftest` and it rejected the flag — same mixed-version state as above, detected mechanically. | Same fix: manual installer run from GitHub. |

## Step 2 — read the REAL error, not the masked one

`Could not import module 'LlamaModel'. Are this object's requirements
defined correctly?` is transformers' `_LazyModule` **masking** the real
exception. Since v3.17.2 the runner prints the full chained traceback
below the `[error]` line — the true cause is the **"direct cause"**
block. Never diagnose from the masked one-liner alone.

| Real error in the chain | Cause | Fix |
|---|---|---|
| `Torch not compiled with CUDA enabled` | CPU torch wheel in the venv. Historic cause: pre-v3.17.1 Repair ran `pip --force-reinstall` *with deps*, clobbering cu124 torch with PyPI's CPU wheel; the pinned `torch==X` reinstall then no-ops ("already satisfied" — PEP 440 treats `2.6.0+cpu` == `2.6.0`). | v3.17.1+ Repair detects a non-CUDA build and force-reinstalls cu124 in place. On older versions: Uninstall engine → Install fresh. |
| `ModuleNotFoundError` for a transformers/chatterbox dep | Genuinely broken venv (interrupted install). | Repair — the corruption signatures escalate to a clean rebuild when needed (`_CORRUPTION_SMOKE_SIGNATURES`, `src/engine_installer.py`). |
| `Found no NVIDIA driver` / `CUDA error:` | Environmental (driver/hardware) — a rebuild can NOT fix it; deliberately excluded from the rebuild signatures. | Driver install / hardware question, not an app fix. |

## Step 3 — the smoke-vs-Convert paradox

If "install/repair succeeds but Convert fails" ever reappears: since
v3.17.3 the smoke test runs the **actual runner script**
(`--selftest`) in the **same isolated env** as synthesis, so smoke and
Convert cannot verify different things. If the paradox shows up anyway,
the two code paths have diverged again — diff what
`engine_installer._smoke_test` runs vs what `ChatterboxRunner.start`
runs (argv, env, cwd) and close the gap. That divergence, not the venv,
is the bug.

## Step 4 — environment isolation invariants (do not regress)

- The venv python is spawned with `isolated_python_env()`
  (`src/launcher_bridge.py`): `PYTHONPATH`/`PYTHONHOME`/`PYTHONSTARTUP`
  stripped, `PYTHONNOUSERSITE=1`.
- The runner **appends** (never prepends) its repo root to `sys.path`
  — in a frozen install that root is the app's `_internal` bundle and
  must not shadow venv packages. A source-guard test enforces this.
- Repair's force-reinstall uses `--no-deps` (re-pins the listed chain
  without re-resolving torch).

## Known history (why the rules above exist)

- v3.16.0 Repair clobbered CUDA torch → CPU (`--force-reinstall` w/o
  `--no-deps`), fixed in v3.17.1 (PR #111).
- Convert-vs-smoke false-green + stale-script delivery failure
  diagnosed via missing unmask traceback; provenance stamps + selftest
  smoke shipped in v3.17.3 (PR #113). The published installer was
  verified byte-identical to the tag — the staleness was on the user's
  disk, i.e. a half-applied silent update.
- "LlamaModel" root-cause note: the transformers pin (5.2.0 for
  chatterbox-tts 0.1.7) is correct; suspect environment/delivery before
  suspecting the pin.
