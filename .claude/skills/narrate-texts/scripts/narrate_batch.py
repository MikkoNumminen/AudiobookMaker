#!/usr/bin/env python3
"""Convert text files to MP3 with Chatterbox, sequentially, and log it.

Why this exists: every session that narrates a batch otherwise re-derives
the same shell loop, and gets the same two details wrong.

  1. `convert --output foo.mp3` on the Chatterbox path treats the argument
     as a WORK DIRECTORY. The MP3 lands at `foo/01_Text.mp3` and the CLI's
     final line prints `Done:` with an empty path — while still exiting 0.
     A script that trusts `--output` reports success and leaves no file.
  2. Only ONE conversion may run at a time. Chatterbox holds ~4-6 GB of
     VRAM; two at once thrash the page file and can freeze the machine.

Layout expected by --source:

    <source>/en/<slug>.txt      -> Grandmom
    <source>/fi/<slug>.txt      -> Isoäiti

Any language subdirectory is passed straight through to `--language`, so
this is not limited to the two the project ships.

Examples:
    python narrate_batch.py --source D:/texts
    python narrate_batch.py --source D:/texts --only posts-read-themselves
    python narrate_batch.py --file D:/texts/fi/post.txt --language fi
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_CHUNK_CHARS = 200   # 300 (the CLI default) truncates measurably more
CLI = "audiobookmaker-cli"


def _repo_root() -> Path:
    # scripts/ -> narrate-texts/ -> skills/ -> .claude/ -> repo
    return Path(__file__).resolve().parents[4]


def _log(log_path: Path, msg: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(msg + "\n")
    print(msg, flush=True)


def _duration_s(mp3: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(mp3)],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return 0.0


def _hms(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def convert_one(
    src: Path,
    lang: str,
    work_dir: Path,
    out_dir: Path,
    chunk_chars: int,
    log_path: Path,
    fresh: bool,
) -> tuple[bool, Path | None, float]:
    """Convert one file. Returns (ok, final_mp3, duration_seconds)."""
    stem = src.stem
    final = out_dir / f"{stem}.mp3"
    work_stem = work_dir / stem

    if fresh and work_stem.exists():
        shutil.rmtree(work_stem, ignore_errors=True)

    cmd = [
        CLI, "convert", str(src),
        "--engine", "chatterbox_grandmom",
        "--language", lang,
        "--chunk-chars", str(chunk_chars),
        "--output", str(work_stem) + ".mp3",
    ]
    if fresh:
        cmd += ["--overwrite", "fresh"]

    _log(log_path, f"--- START {lang}/{stem} ({src.stat().st_size} bytes) ---")
    t0 = time.time()
    with log_path.open("a", encoding="utf-8") as fh:
        rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode
    took = time.time() - t0

    # See docstring note 1 — the real file is NOT at --output.
    produced = work_stem / "01_Text.mp3"
    if rc != 0 or not produced.exists():
        _log(log_path, f"--- FAIL {lang}/{stem} rc={rc} (no {produced}) ---")
        return False, None, 0.0

    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(produced, final)
    dur = _duration_s(final)
    _log(log_path,
         f"--- OK {lang}/{stem} -> {final} ({_hms(dur)}, synth {_hms(took)}) ---")
    return True, final, dur


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src_group = ap.add_mutually_exclusive_group(required=True)
    src_group.add_argument("--source", help="Directory holding <lang>/ subdirs.")
    src_group.add_argument("--file", help="A single text file.")
    ap.add_argument("--language", help="Required with --file.")
    ap.add_argument("--out", help="Output root (default: <repo>/.local/audiobooks/blog).")
    ap.add_argument("--only", help="Comma-separated slugs (no .txt) to convert.")
    ap.add_argument("--languages",
                    help="Comma-separated language subdirs to include "
                         "(default: all). Use when a fix affects only one "
                         "language and re-running the others would waste "
                         "GPU hours for no change.")
    ap.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS)
    ap.add_argument("--force", action="store_true",
                    help="Re-convert even when the output MP3 already exists.")
    ap.add_argument("--keep-cache", action="store_true",
                    help="Reuse the chunk cache. UNSAFE if the text changed: "
                         "the cache is keyed by chunk INDEX, so shifted "
                         "boundaries splice in audio from the old text.")
    args = ap.parse_args()

    repo = _repo_root()
    out_root = Path(args.out) if args.out else repo / ".local/audiobooks/blog"
    work_root = out_root / "work"
    log_path = out_root / "narrate.log"

    jobs: list[tuple[Path, str]] = []
    if args.file:
        if not args.language:
            ap.error("--language is required with --file")
        jobs.append((Path(args.file), args.language))
    else:
        source = Path(args.source)
        if not source.is_dir():
            print(f"error: {source} is not a directory", file=sys.stderr)
            return 2
        wanted = {s.strip() for s in args.only.split(",")} if args.only else None
        langs = ({s.strip() for s in args.languages.split(",")}
                 if args.languages else None)
        for lang_dir in sorted(p for p in source.iterdir() if p.is_dir()):
            if langs is not None and lang_dir.name not in langs:
                continue
            for txt in sorted(lang_dir.glob("*.txt")):
                if wanted is not None and txt.stem not in wanted:
                    continue
                jobs.append((txt, lang_dir.name))

    if not jobs:
        print("error: nothing to convert", file=sys.stderr)
        return 2

    # Smallest first: a systemic failure surfaces in two minutes, not thirty.
    jobs.sort(key=lambda j: j[0].stat().st_size)

    _log(log_path, f"=== batch start: {len(jobs)} file(s) ===")
    results, failures = [], 0
    for src, lang in jobs:
        final = out_root / lang / f"{src.stem}.mp3"
        if final.exists() and not args.force:
            _log(log_path, f"--- SKIP {lang}/{src.stem} (exists) ---")
            continue
        # ONE at a time. Never parallelise this loop.
        ok, path, dur = convert_one(
            src, lang, work_root / lang, out_root / lang,
            args.chunk_chars, log_path, fresh=not args.keep_cache,
        )
        results.append((lang, src.stem, ok, dur))
        failures += 0 if ok else 1

    _log(log_path, "=== batch done ===")
    print("\nSUMMARY")
    for lang, stem, ok, dur in results:
        print(f"  {'OK  ' if ok else 'FAIL'} {lang}/{stem}  {_hms(dur)}")
    print(f"\n{len(results) - failures}/{len(results)} converted. "
          f"Log: {log_path}")
    print("NOT VERIFIED YET — run verify_narration.py before reporting done.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
