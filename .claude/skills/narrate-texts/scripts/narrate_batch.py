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
import hashlib
import json
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


_runner_module = None


def _load_runner():
    """Import the Chatterbox runner once and keep it.

    Re-executing the module for every file in a batch is pure waste —
    the chunker is the only thing needed and it does not change between
    files.
    """
    global _runner_module
    if _runner_module is None:
        import importlib.util
        path = _repo_root() / "scripts" / "generate_chatterbox_audiobook.py"
        spec = importlib.util.spec_from_file_location("gca", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _runner_module = mod
    return _runner_module


_META_NAME = ".cache_meta.json"


def _chunk_fingerprint(src: Path, lang: str, chunk_chars: int) -> dict:
    """What the cached audio was built from: size, language, exact chunks."""
    gca = _load_runner()

    class _Chapter:
        def __init__(self, title, content):
            self.title, self.content = title, content

    out = gca._prepare_chapter_chunks(
        _Chapter("Text", src.read_text(encoding="utf-8")),
        chunk_chars, 100000, lang)
    chunks = out[0] if isinstance(out, tuple) else out
    chunks = [c if isinstance(c, str) else getattr(c, "text", str(c))
              for c in chunks]
    joined = json.dumps(chunks, ensure_ascii=False).encode("utf-8")
    return {
        "chunk_chars": chunk_chars,
        "language": lang,
        "chunk_count": len(chunks),
        "chunks_sha256": hashlib.sha256(joined).hexdigest(),
    }


def _cache_is_reusable(work_stem: Path, src: Path, lang: str,
                       chunk_chars: int) -> bool:
    """True when the cached chunks were built from this exact text.

    The chunk cache is keyed by INDEX. If the text or the chunk size has
    changed since it was written, chunk N now holds different words than
    the file it is about to be spliced into — and the runner reuses it
    anyway, because its health check only asks whether the audio length
    is plausible for the character count.

    This has bitten for real. Five delivered files were re-rendered with
    `--keep-cache` at 200 chars over a cache written at 300; the first
    six chunks kept audio from the LARGER old chunks while the remaining
    four were synthesized fresh, so the output repeated whole sentences
    and ran up to 38% long.

    The first version of this guard compared the number of WAVs on disk
    to the number of chunks expected. That cannot tell a stale cache from
    a deliberate one: deleting a few chunk WAVs is exactly how a single
    mispronounced chunk gets re-rolled, and it makes the counts differ
    too. Every targeted re-roll therefore triggered a full rebuild — safe,
    but it turned a two-minute job into twenty.

    So the fingerprint is recorded instead: chunk size, language, and a
    hash of the chunk texts themselves. A missing WAV is then just a cache
    miss and gets re-synthesized, while changed text invalidates the lot.
    """
    if not (work_stem / ".chunks").exists():
        return True

    meta_path = work_stem / _META_NAME
    try:
        want = _chunk_fingerprint(src, lang, chunk_chars)
    except Exception as exc:  # noqa: BLE001
        # Never silent: a guard that fails quietly and rebuilds anyway
        # looks exactly like a guard that decided the cache was stale,
        # and the operator loses the distinction.
        print(f"warning: could not fingerprint the text ({exc}); "
              f"rebuilding fresh to be safe", flush=True)
        return False

    if not meta_path.exists():
        # Written by an older run. Fall back to the count check, which is
        # the best that can be said without a recorded fingerprint.
        cached = list(work_stem.glob(".chunks/ch01_chunk*.wav"))
        return not cached or len(cached) == want["chunk_count"]

    try:
        have = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False

    return have == want


def _write_cache_meta(work_stem: Path, src: Path, lang: str,
                      chunk_chars: int) -> None:
    """Record what this cache was built from, for the next run to check."""
    try:
        (work_stem / _META_NAME).write_text(
            json.dumps(_chunk_fingerprint(src, lang, chunk_chars), indent=2),
            encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not write {_META_NAME} ({exc}); the next run "
              f"will fall back to counting chunks", flush=True)


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

    if not fresh and not _cache_is_reusable(work_stem, src, lang, chunk_chars):
        _log(log_path,
             f"--- CACHE MISMATCH {lang}/{stem}: chunk count differs from the "
             f"cache; rebuilding fresh rather than splicing stale audio ---")
        fresh = True

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
    # Record what this cache was built from, so the next run can tell a
    # deliberately-deleted chunk from a cache built at another size.
    _write_cache_meta(work_stem, src, lang, chunk_chars)
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
