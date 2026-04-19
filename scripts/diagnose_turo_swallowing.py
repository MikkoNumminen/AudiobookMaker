"""
diagnose_turo_swallowing.py
---------------------------
Read-only diagnostic for a long Finnish audiobook MP3.  Reads the file
once, walks it in fixed time windows (5 minutes by default), and for
each window reports:

  * speech-segment count, mean/median/p10/p90 duration, total speech ms
  * silence-gap mean/median/p10/p90 between consecutive speech segments
  * mean RMS (dB) of the non-silent portions
  * total window duration (ms), plus the window's start time

Writes a CSV next to the input audio and prints a terminal summary that
flags any metric trending monotonically from start to end.

Usage:
    python scripts/diagnose_turo_swallowing.py [path/to/audio.mp3]

Safe to run repeatedly — does not modify the input file.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from statistics import mean, median

import numpy as np

# Add repo root to sys.path so we can import src.ffmpeg_path.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.ffmpeg_path import setup_ffmpeg_path  # noqa: E402

setup_ffmpeg_path()

from pydub import AudioSegment  # noqa: E402
from pydub.silence import detect_nonsilent  # noqa: E402


DEFAULT_INPUT = REPO_ROOT / "TURO_00_full.mp3.mpeg"
DEFAULT_OUTPUT = REPO_ROOT / "out" / "diagnostic_turo.csv"

WINDOW_MS = 5 * 60 * 1000          # 5-minute windows
SILENCE_THRESH_DB = -40            # below this is silence
MIN_SILENCE_MS = 300               # gaps shorter than this are not "seams"


def pct(values, p):
    if not values:
        return float("nan")
    return float(np.percentile(values, p))


def window_stats(segment: AudioSegment, window_start_ms: int):
    """Compute the required metrics for one window's AudioSegment."""
    duration_ms = len(segment)

    # detect_nonsilent returns list of [start_ms, end_ms] pairs relative
    # to the slice.  seek_step=10 keeps runtime reasonable.
    nonsilent = detect_nonsilent(
        segment,
        min_silence_len=MIN_SILENCE_MS,
        silence_thresh=SILENCE_THRESH_DB,
        seek_step=10,
    )

    seg_durations = [end - start for start, end in nonsilent]
    gaps = []
    for i in range(1, len(nonsilent)):
        gap = nonsilent[i][0] - nonsilent[i - 1][1]
        if gap > 0:
            gaps.append(gap)

    total_speech = sum(seg_durations)
    total_silence = duration_ms - total_speech

    # Mean RMS dB over just the non-silent slices.  pydub's dBFS is
    # already log-scale, so a plain arithmetic mean over segments is
    # fine for trend detection.
    dbfs_values = []
    for start, end in nonsilent:
        piece = segment[start:end]
        if len(piece) > 0 and piece.rms > 0:
            dbfs_values.append(piece.dBFS)

    return {
        "window_start_ms": window_start_ms,
        "window_start_min": round(window_start_ms / 60000, 2),
        "window_duration_ms": duration_ms,
        "num_segments": len(seg_durations),
        "seg_mean_ms": round(mean(seg_durations), 1) if seg_durations else float("nan"),
        "seg_median_ms": round(median(seg_durations), 1) if seg_durations else float("nan"),
        "seg_p10_ms": round(pct(seg_durations, 10), 1),
        "seg_p90_ms": round(pct(seg_durations, 90), 1),
        "total_speech_ms": total_speech,
        "total_silence_ms": total_silence,
        "gap_mean_ms": round(mean(gaps), 1) if gaps else float("nan"),
        "gap_median_ms": round(median(gaps), 1) if gaps else float("nan"),
        "gap_p10_ms": round(pct(gaps, 10), 1),
        "gap_p90_ms": round(pct(gaps, 90), 1),
        "mean_speech_dbfs": round(mean(dbfs_values), 2) if dbfs_values else float("nan"),
    }


def detect_monotonic_trend(rows, key):
    """Crude: compare first-quarter mean to last-quarter mean."""
    vals = [r[key] for r in rows if not (isinstance(r[key], float) and np.isnan(r[key]))]
    if len(vals) < 4:
        return None
    q = len(vals) // 4
    first = mean(vals[:q]) if q else mean(vals)
    last = mean(vals[-q:]) if q else mean(vals)
    return first, last, last - first


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", nargs="?", default=str(DEFAULT_INPUT))
    ap.add_argument("-o", "--output", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--window-ms", type=int, default=WINDOW_MS)
    args = ap.parse_args()

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(2)

    print(f"Loading {input_path} ({os.path.getsize(input_path)/1e6:.1f} MB)...")
    audio = AudioSegment.from_file(str(input_path))
    total_ms = len(audio)
    print(f"  duration: {total_ms/1000:.1f}s ({total_ms/3600000:.2f}h), "
          f"channels={audio.channels}, fr={audio.frame_rate}, "
          f"sample_width={audio.sample_width}")

    window_ms = args.window_ms
    rows = []
    n_windows = (total_ms + window_ms - 1) // window_ms
    for i in range(n_windows):
        start = i * window_ms
        end = min(start + window_ms, total_ms)
        slice_ = audio[start:end]
        print(f"  window {i+1}/{n_windows}  "
              f"[{start/60000:6.2f}..{end/60000:6.2f} min]  "
              f"analyzing...", flush=True)
        rows.append(window_stats(slice_, start))

    # Write CSV.
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {out_path}  ({len(rows)} rows)")

    # Trend summary.
    print("\n=== Trend (first-quarter mean  ->  last-quarter mean  (delta)) ===")
    watch = [
        "num_segments",
        "seg_mean_ms",
        "seg_median_ms",
        "seg_p90_ms",
        "seg_p10_ms",
        "total_speech_ms",
        "total_silence_ms",
        "gap_mean_ms",
        "gap_median_ms",
        "gap_p90_ms",
        "mean_speech_dbfs",
    ]
    for key in watch:
        t = detect_monotonic_trend(rows, key)
        if t is None:
            print(f"  {key:22s}  (insufficient data)")
            continue
        first, last, delta = t
        arrow = "DOWN" if delta < 0 else ("UP" if delta > 0 else "=")
        print(f"  {key:22s}  {first:10.2f}  ->  {last:10.2f}   ({delta:+10.2f}) {arrow}")

    # Highlight the first window where things look anomalous.
    # Heuristic: flag windows where seg_p90 or seg_mean drop > 25% vs
    # the first-half median of that metric.
    print("\n=== Anomaly scan (window where seg_mean or seg_p90 drops >25% vs early baseline) ===")
    half = len(rows) // 2 or 1
    for key in ("seg_mean_ms", "seg_p90_ms", "total_speech_ms"):
        baseline = median([r[key] for r in rows[:half]
                           if not (isinstance(r[key], float) and np.isnan(r[key]))])
        if baseline <= 0:
            continue
        first_bad = None
        for r in rows:
            v = r[key]
            if isinstance(v, float) and np.isnan(v):
                continue
            if v < baseline * 0.75:
                first_bad = r
                break
        if first_bad:
            print(f"  {key:20s}  baseline={baseline:.1f}  "
                  f"first drop at t={first_bad['window_start_min']} min  "
                  f"(value={first_bad[key]})")
        else:
            print(f"  {key:20s}  baseline={baseline:.1f}  no drop >25% detected")


if __name__ == "__main__":
    main()
