"""
stress_test_chatterbox_longrun.py
---------------------------------
Tier 1 validation for the long-run hardening in
``scripts/generate_chatterbox_audiobook.py``.

Rationale
=========
The "sentence endings get swallowed after ~4 hours" bug only shows up
deep into a long synthesis run because its root cause is stateful drift
that accumulates across thousands of ``engine.generate()`` calls —
``AlignmentStreamAnalyzer`` hook residue and CUDA allocator
fragmentation. A small regression test proves nothing; a 5-hour
end-to-end book takes 5 hours to fail.

This script compresses the "many calls in one process" dimension without
the "produce a real book" dimension. It loops ``engine.generate()`` on
a single short Finnish sentence N times in one process, applies the
same ``_clear_chatterbox_state`` hygiene the real synth loop applies,
and logs the same per-call stats. If the fix is working, every metric
stays flat; if something regresses, the stats file pinpoints which
metric drifts and when.

Usage
=====
    python scripts/stress_test_chatterbox_longrun.py
    python scripts/stress_test_chatterbox_longrun.py --n 500
    python scripts/stress_test_chatterbox_longrun.py \
        --n 500 --snapshot-at 1,100,250,500 \
        --text "Tämä on pitkän ajon stressitestin vakiolause." \
        --out-dir dist/stress_test

Output
======
Per run, under ``--out-dir`` (default ``dist/stress_test/<timestamp>``):
  * ``stress_test_stats.jsonl`` — one JSON record per call
  * ``snapshots/chunk_NNNN.wav`` — audio at configured snapshot indices
  * ``summary.txt`` — trend analysis + pass/fail verdict

The verdict is PASS iff:
  * ``hook_count`` never exceeds ``HOOK_COUNT_PASS_THRESHOLD`` after the
    first call (the first call legitimately sees 30 residual hooks from
    the base model's construction; we reset them on the second call and
    thereafter every chunk must be at 0)
  * ``reserved_mb`` in the last quarter is not more than
    ``RESERVED_MB_GROWTH_FRACTION`` above the first quarter
  * No call's ``audio_s`` is less than
    ``AUDIO_SHORTENING_FAIL_FRACTION`` of the baseline median for
    identical input

Safe to re-run; each invocation writes a fresh timestamped directory.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from statistics import median

# Make scripts/ importable as a sibling of src/.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src.ffmpeg_path import setup_ffmpeg_path  # noqa: E402

setup_ffmpeg_path()

import generate_chatterbox_audiobook as gca  # type: ignore  # noqa: E402

# Constants from the real synth script — keep in sync.
FI_REPETITION_PENALTY = gca.FI_REPETITION_PENALTY
FI_TEMPERATURE = gca.FI_TEMPERATURE
FI_EXAGGERATION = gca.FI_EXAGGERATION
FI_CFG_WEIGHT = gca.FI_CFG_WEIGHT

# Pass/fail thresholds. Chosen to catch the actual drift modes we care
# about while leaving room for normal run-to-run jitter.
HOOK_COUNT_PASS_THRESHOLD = 3        # > 3 after call #2 is evidence of a leak
RESERVED_MB_GROWTH_FRACTION = 0.15   # last-quarter mean > 1.15 * first-quarter → fail
AUDIO_SHORTENING_FAIL_FRACTION = 0.75  # any call < 0.75 × median → fail

DEFAULT_TEXT = (
    "Tämä on pitkän ajon stressitestin vakiolause, "
    "jolla varmistetaan ettei lauseita lyhennetä tuhannen kutsun jälkeen."
)
DEFAULT_N = 500
DEFAULT_SNAPSHOTS = (1, 100, 250, 500)


def _parse_snapshot_arg(raw: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in raw.split(",") if x.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--snapshot-at expects comma-separated ints, got {raw!r}"
        ) from exc


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=DEFAULT_N,
                    help=f"Number of generate() calls (default {DEFAULT_N})")
    ap.add_argument("--text", default=DEFAULT_TEXT,
                    help="Finnish sentence to synthesize repeatedly")
    ap.add_argument("--language", default="fi",
                    help="Language code passed to Chatterbox (default fi)")
    ap.add_argument("--device", default="auto",
                    help="cpu / cuda / auto (default auto)")
    ap.add_argument("--ref-audio", default=None,
                    help="Override reference WAV path (default: Grandmom)")
    ap.add_argument("--snapshot-at", type=_parse_snapshot_arg,
                    default=DEFAULT_SNAPSHOTS,
                    help="Comma-separated chunk indices to save as WAV "
                         "(1-based). Default: 1,100,250,500")
    ap.add_argument("--out-dir", default=None,
                    help="Output directory (default: dist/stress_test/<ts>)")
    return ap.parse_args()


def _default_out_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return REPO_ROOT / "dist" / "stress_test" / stamp


def _trend(values: list[float]) -> tuple[float, float, float]:
    """Return (first_quarter_mean, last_quarter_mean, delta)."""
    if not values:
        return 0.0, 0.0, 0.0
    q = max(1, len(values) // 4)
    first = sum(values[:q]) / q
    last = sum(values[-q:]) / q
    return first, last, last - first


def _summarize(records: list[dict],
               snapshot_indices: tuple[int, ...],
               out_dir: Path) -> tuple[bool, str]:
    """Compute PASS/FAIL verdict and write a readable summary."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("Chatterbox long-run stress test — summary")
    lines.append("=" * 72)
    lines.append(f"Total calls: {len(records)}")
    if not records:
        return False, "No records collected."

    # Per-metric trends.
    metrics = ("audio_s", "s_per_char", "synth_s", "rtf",
               "hook_count", "allocated_mb", "reserved_mb")
    lines.append("")
    lines.append("Trend (first-quarter mean -> last-quarter mean (delta)):")
    for key in metrics:
        vals = [r[key] for r in records if r.get(key) is not None]
        if not vals:
            continue
        first, last, delta = _trend(vals)
        arrow = "UP" if delta > 0 else ("DOWN" if delta < 0 else "=")
        lines.append(
            f"  {key:16s}  {first:10.3f}  ->  {last:10.3f}  "
            f"({delta:+10.3f}) {arrow}"
        )

    # Failure checks.
    failures: list[str] = []

    # 1. Hook count. Call #1 legitimately has residual hooks from model
    #    construction; we care about calls #2+.
    post_first = records[1:]
    bad_hooks = [r for r in post_first
                 if (r.get("hook_count") or 0) > HOOK_COUNT_PASS_THRESHOLD]
    if bad_hooks:
        first_bad = bad_hooks[0]
        failures.append(
            f"hook_count exceeded {HOOK_COUNT_PASS_THRESHOLD} "
            f"at call #{first_bad['global_chunk_idx']} "
            f"(value={first_bad['hook_count']}) — likely leak"
        )

    # 2. Reserved GPU memory growth.
    reserved = [r["reserved_mb"] for r in records
                if r.get("reserved_mb") is not None]
    if reserved:
        first_q, last_q, _ = _trend(reserved)
        if first_q > 0 and last_q > first_q * (1 + RESERVED_MB_GROWTH_FRACTION):
            failures.append(
                f"reserved_mb grew {last_q - first_q:.0f} MiB "
                f"({first_q:.0f} -> {last_q:.0f}, "
                f">{int(RESERVED_MB_GROWTH_FRACTION * 100)}%) — "
                f"allocator fragmentation"
            )

    # 3. Audio shortening: since input is constant, audio_s should be
    #    stable. Any outlier below 0.75 × median is the exact symptom
    #    we're hunting.
    audio_s_vals = [r["audio_s"] for r in records
                    if r.get("audio_s") is not None]
    if audio_s_vals:
        base = median(audio_s_vals)
        short = [r for r in records
                 if (r.get("audio_s") or 0) < base * AUDIO_SHORTENING_FAIL_FRACTION]
        if short:
            first_short = short[0]
            failures.append(
                f"audio_s dropped below "
                f"{int(AUDIO_SHORTENING_FAIL_FRACTION * 100)}% of median "
                f"({base:.2f}s) at call #{first_short['global_chunk_idx']} "
                f"(value={first_short['audio_s']:.2f}s) — "
                f"THIS IS THE SWALLOWING SYMPTOM"
            )

    passed = not failures
    lines.append("")
    if passed:
        lines.append("VERDICT: PASS — no drift detected across "
                     f"{len(records)} calls")
    else:
        lines.append("VERDICT: FAIL")
        for f in failures:
            lines.append(f"  - {f}")

    lines.append("")
    lines.append(f"Snapshots saved at calls: "
                 f"{', '.join(str(i) for i in snapshot_indices)}")
    lines.append(f"  -> {out_dir / 'snapshots'}")
    lines.append(f"Per-call stats: {out_dir / 'stress_test_stats.jsonl'}")

    summary = "\n".join(lines)
    return passed, summary


def main() -> int:
    args = parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir = out_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    stats_path = out_dir / "stress_test_stats.jsonl"
    summary_path = out_dir / "summary.txt"

    device = gca._resolve_device(args.device)
    print(f"[stress] device={device} n={args.n} text={args.text!r}",
          flush=True)
    print(f"[stress] output: {out_dir}", flush=True)

    engine, ref_wav_path = gca._load_engine(
        device=device,
        ref_override=args.ref_audio,
        language=args.language,
    )
    print(f"[stress] ref wav: {ref_wav_path}", flush=True)

    snapshot_set = set(args.snapshot_at)
    records: list[dict] = []

    wall_start = time.time()
    for i in range(1, args.n + 1):
        gca._clear_chatterbox_state(engine)
        t0 = time.time()
        wav = engine.generate(
            args.text,
            language_id=args.language,
            audio_prompt_path=ref_wav_path,
            repetition_penalty=FI_REPETITION_PENALTY,
            temperature=FI_TEMPERATURE,
            exaggeration=FI_EXAGGERATION,
            cfg_weight=FI_CFG_WEIGHT,
        )
        dt = time.time() - t0
        audio_s = wav.shape[-1] / engine.sr

        record = {
            "ts": time.time(),
            "global_chunk_idx": i,
            "input_chars": len(args.text),
            "audio_s": round(audio_s, 3),
            "synth_s": round(dt, 3),
            "rtf": round(dt / audio_s, 3) if audio_s > 0 else None,
            "s_per_char": round(audio_s / max(1, len(args.text)), 4),
            "hook_count": gca._chatterbox_hook_count(engine),
            **gca._gpu_mem_stats_mb(),
        }
        records.append(record)
        gca._append_chunk_stats(stats_path, record)

        if i in snapshot_set:
            import torchaudio as ta
            snap_path = snapshots_dir / f"chunk_{i:04d}.wav"
            ta.save(str(snap_path), wav, engine.sr)
            print(f"[stress] snapshot saved: {snap_path}", flush=True)

        # Progress print every 25 calls so a long run is observable.
        if i == 1 or i % 25 == 0 or i == args.n:
            elapsed = time.time() - wall_start
            eta_s = (elapsed / i) * (args.n - i) if i else 0.0
            print(
                f"[stress] {i}/{args.n}  audio_s={audio_s:.2f}  "
                f"synth_s={dt:.2f}  rtf={record['rtf']}  "
                f"hooks={record['hook_count']}  "
                f"reserved_mb={record.get('reserved_mb', 'n/a')}  "
                f"elapsed={elapsed/60:.1f}min  eta={eta_s/60:.1f}min",
                flush=True,
            )

    passed, summary = _summarize(records, args.snapshot_at, out_dir)
    summary_path.write_text(summary, encoding="utf-8")
    print("\n" + summary, flush=True)
    print(f"\n[stress] summary written to {summary_path}", flush=True)

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
