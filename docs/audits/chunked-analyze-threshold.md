# Voice-pack analyze: long-source crash and chunked workaround

> File paths and constants below were last verified against master at
> commit `bc3bc23` on 2026-05-11 (after PR #8
> `feat(voice-pack): chunked analyze for long sources + hardening`
> merged). If `git ls-files src/voice_pack/` no longer shows
> `chunked.py`, `ffmpeg_slice.py`, and `reconcile.py`, this record is
> out of date.

## TL;DR

Running `scripts/voice_pack_analyze.py` against an audio source longer
than ~6 minutes on a Windows + 12 GB CUDA card crashes the Python
child with native exit code `0xC0000409`
(STATUS_STACK_BUFFER_OVERRUN, surfaced as `127` through bash) before
the script can write any artefacts. There is no Python traceback
because the crash kills the process before exception machinery can
run.

The fix in this repo is structural: anything longer than the safe
threshold goes through a chunked orchestrator that slices the source
into ≤ 5-min pieces, analyses each one in its own subprocess, and
reconciles the results via voice-embedding clustering. The thin
wrapper :func:`src.voice_pack_subproc.run_analyze_auto` does the
routing automatically so the GUI clone-voice flow handles long inputs
without user intervention.

## Empirical reproducer table

These numbers are from the same workstation (Windows 11, RTX 3060
12 GB, faster-whisper-large-v3, pyannote 3.1):

| Source duration | Outcome             | Wall time            |
|-----------------|---------------------|----------------------|
| 60 s            | OK                  | ~21 s                |
| 5 min           | OK, 29 chunks       | ~40 s                |
| 6 min           | borderline (varies) | ~45 s on success     |
| 10 min          | crash 0xC0000409    | n/a (no artefacts)   |
| 15 min          | crash 0xC0000409    | n/a                  |
| 54 min          | crash 0xC0000409    | n/a                  |

Crashes happen consistently above ~6 min. Below 5 min everything
behaves. The crash is a Windows fast-fail / GS check from native
code — the prime suspects are CTranslate2 (faster-whisper backend)
and pyannote / torchaudio. We have not yet narrowed it to one of the
two; the chunked workaround fixes it for both at once and is the
right primary fix because:

* Long audiobooks need chunking anyway for VRAM headroom and
  parallelism.
* A two-line config change in the workaround is much cheaper than
  upstream patching a native crash.
* Even if the upstream is patched, the chunked path is still useful
  for parallel analyse on a multi-GPU box.

## Where the chunked path lives

* `src/voice_pack/chunked.py` — pure planning (slice geometry,
  silence-snap, overlap math). No I/O.
* `src/voice_pack/ffmpeg_slice.py` — thin ffprobe / ffmpeg wrapper
  with injectable runner for tests.
* `src/voice_pack/reconcile.py` — pure cross-chunk speaker
  reconciliation (cosine-similarity union-find with hard/ambiguous
  thresholds).
* `src/voice_pack_chunked_subproc.py` — orchestrator. Probes
  duration, slices, runs N per-chunk analyses (with retry-on-crash
  via CPU fallback), embeds local speakers, reconciles, merges, and
  writes the canonical artefact set.
* `src/voice_pack_subproc.py::run_analyze_auto` — the routing
  wrapper. Short inputs (≤ 300 s by default) take the original
  single-shot path; long inputs go through the chunked orchestrator.
* `scripts/voice_pack_analyze.py` — gains `--chunked {auto,always,never}`,
  `--chunk-seconds N`, `--workers N` flags. Default `--chunked auto`
  matches the GUI behaviour.

## Threshold tuning

`DEFAULT_CHUNK_SECONDS = 300.0` (5 minutes). This is conservative —
a 6-minute slice probably works most of the time on this hardware.
The choice trades a slightly larger chunk count for a wide safety
margin: every chunk must succeed first-try ideally, since each retry
on the CPU fallback is roughly 5x slower than GPU.

If/when the upstream native crash is patched, raising the threshold
or letting the auto-router skip chunking entirely costs nothing —
the chunked path is purely additive.

## CPU fallback retry

Each chunk subprocess that crashes (return-code != 0, no artefacts
written) is automatically retried once with `--asr-device cpu`. CPU
mode escapes the GPU-side native crash at the cost of being much
slower; for a 5-min chunk that's acceptable since the failure is
already exceptional. If the CPU retry also fails the chunk is
skipped, logged in `report.md`, and the rest of the run continues —
we'd rather give the operator a partial result with a known gap
than fail the entire 1-hour analyse over one bad chunk.

## Reconciliation thresholds

* `DEFAULT_HARD_MERGE_THRESHOLD = 0.75` — cosine similarity at or
  above which two cross-chunk local speakers are merged into one
  global speaker.
* `DEFAULT_AMBIGUOUS_THRESHOLD = 0.65` — pairs in the half-open
  band ``[0.65, 0.75)`` are flagged for human review in `report.md`
  but **not** merged automatically. Below 0.65 we treat the pair as
  definitely-different.

These were picked against the Chatterbox voice encoder's known
distribution: same-speaker pairs typically score 0.85-0.95,
cross-speaker 0.30-0.55. The grey band catches edge cases (a reader
modulating between performances) without triggering false merges on
similar-timbre but distinct readers.
