#!/usr/bin/env python3
"""Check a finished narration for chunks that lost speech.

The failure this catches: Chatterbox can emit end-of-speech early and drop
the rest of a chunk. The MP3 assembles from the stub without complaint, so
the file plays fine and is simply missing a clause.

Two rules this script exists to enforce, both learned the hard way:

  1. Read durations from the WAV files, NOT from `.chunk_stats.jsonl`.
     That file appends one record per synthesis ATTEMPT, so a 64-chunk
     chapter can hold 108 records. Averaging it mixes discarded takes with
     final ones and under-reports bad chunks. It produced two false
     "verified clean" calls on a file that had nine truncations.

  2. Compare each chunk against the file's own MEDIAN rate, not against
     the runner's absolute floor. The floor has to stay safe for the
     fastest text the project narrates, so it is far too loose for any
     particular chapter: chunks at 0.058-0.062 s/char cleared it while
     being plainly short next to neighbours running at 0.070.

Duration alone is a smoke alarm, never a verdict. `--transcribe` is what
turns a suspicion into a fact, and English needs it even when it looks
clean — English fails by swallowing content silently rather than by
producing a short chunk.

Examples:
    python verify_narration.py --work .local/audiobooks/blog/work/fi/my-post \\
                               --text D:/texts/fi/my-post.txt --language fi
    python verify_narration.py --root .local/audiobooks/blog --texts D:/texts
    ... add --transcribe to hear what the outliers actually say
"""
from __future__ import annotations

import argparse
import importlib.util
import statistics
import sys
from pathlib import Path

REL_FLOOR = 0.88   # flag below 88% of the file's median — matches runner Pass R


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _load_runner(repo: Path):
    """Import the runner so chunking here matches chunking there exactly."""
    path = repo / "scripts" / "generate_chatterbox_audiobook.py"
    spec = importlib.util.spec_from_file_location("gca", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Chapter:
    def __init__(self, title, content):
        self.title, self.content = title, content


def chunk_texts(mod, text: str, language: str, chunk_chars: int) -> list[str]:
    out = mod._prepare_chapter_chunks(
        _Chapter("Text", text), chunk_chars, 100000, language
    )
    chunks = out[0] if isinstance(out, tuple) else out
    return [c if isinstance(c, str) else getattr(c, "text", str(c))
            for c in chunks]


def wav_seconds(path: Path) -> float:
    import soundfile as sf
    info = sf.info(str(path))
    return info.frames / info.samplerate


def analyse(work: Path, texts: list[str]) -> tuple[float, list[dict]]:
    """Return (median_rate, rows). Rows carry per-chunk rate + flag."""
    rows = []
    for i, t in enumerate(texts):
        wav = work / ".chunks" / f"ch01_chunk{i:04d}.wav"
        if not wav.exists():
            continue
        secs = wav_seconds(wav)
        rows.append({"chunk": i, "chars": len(t), "secs": secs,
                     "rate": secs / max(1, len(t)), "text": t})
    if not rows:
        return 0.0, []
    median = statistics.median(r["rate"] for r in rows)
    for r in rows:
        r["short"] = r["rate"] < median * REL_FLOOR
    return median, rows


def transcribe(wav: Path, language: str) -> str:
    from faster_whisper import WhisperModel
    global _MODEL
    try:
        _MODEL
    except NameError:
        _MODEL = WhisperModel("large-v3", device="cuda", compute_type="float16")
    segs, _ = _MODEL.transcribe(str(wav), language=language, beam_size=5)
    return " ".join(s.text.strip() for s in segs)


def verify_one(mod, work: Path, text_file: Path, language: str,
               chunk_chars: int, do_transcribe: bool) -> bool:
    texts = chunk_texts(mod, text_file.read_text(encoding="utf-8"),
                        language, chunk_chars)
    median, rows = analyse(work, texts)
    if not rows:
        print(f"  !! no chunk WAVs under {work / '.chunks'}")
        return False

    short = [r for r in rows if r["short"]]
    print(f"  {len(rows)} chunks, median {median:.4f} s/char, "
          f"{len(short)} below {REL_FLOOR:.0%}")

    for r in short:
        print(f"    chunk{r['chunk']:04d} {r['rate']:.4f} s/char "
              f"(short ~{(1 - r['rate'] / median) * 100:.0f}%)")
        if do_transcribe:
            wav = work / ".chunks" / f"ch01_chunk{r['chunk']:04d}.wav"
            print(f"      TEXT : {r['text']}")
            print(f"      HEARD: {transcribe(wav, language)}")

    if do_transcribe and not short:
        # English hides its failures; sample the slowest few regardless.
        for r in sorted(rows, key=lambda x: x["rate"])[:2]:
            wav = work / ".chunks" / f"ch01_chunk{r['chunk']:04d}.wav"
            print(f"    spot-check chunk{r['chunk']:04d}")
            print(f"      TEXT : {r['text']}")
            print(f"      HEARD: {transcribe(wav, language)}")

    return not short


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", help="One work dir (contains .chunks/).")
    ap.add_argument("--text", help="Source text for --work.")
    ap.add_argument("--language", help="Language of --work.")
    ap.add_argument("--root", help="Output root; verifies every work dir under it.")
    ap.add_argument("--texts", help="Source tree with <lang>/ subdirs, for --root.")
    ap.add_argument("--chunk-chars", type=int, default=200,
                    help="MUST match what the conversion used, or the chunk "
                         "boundaries will not line up.")
    ap.add_argument("--transcribe", action="store_true",
                    help="Run Whisper on outliers. Needs .venv-chatterbox.")
    args = ap.parse_args()

    mod = _load_runner(_repo_root())
    jobs = []

    if args.work:
        if not (args.text and args.language):
            ap.error("--work needs --text and --language")
        jobs.append((Path(args.work), Path(args.text), args.language))
    elif args.root:
        if not args.texts:
            ap.error("--root needs --texts")
        work_root, text_root = Path(args.root) / "work", Path(args.texts)
        for lang_dir in sorted(p for p in work_root.iterdir() if p.is_dir()):
            for work in sorted(p for p in lang_dir.iterdir() if p.is_dir()):
                txt = text_root / lang_dir.name / f"{work.name}.txt"
                if txt.exists():
                    jobs.append((work, txt, lang_dir.name))
    else:
        ap.error("pass --work or --root")

    clean = True
    for work, txt, lang in jobs:
        print(f"{lang}/{work.name}")
        if not verify_one(mod, work, txt, lang, args.chunk_chars,
                          args.transcribe):
            clean = False

    print()
    if clean:
        print("PASS — no chunk is short for its text.")
        if not args.transcribe:
            print("Duration only. Re-run with --transcribe before calling it "
                  "verified; English drops content without shortening audio.")
    else:
        print("FAIL — transcribe the flagged chunks and compare to TEXT.")
        print("If re-rolls reproduce the same truncation, the input is at "
              "fault, not sampling: look for an unpronounceable character.")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
