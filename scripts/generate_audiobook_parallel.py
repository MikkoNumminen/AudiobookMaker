"""Parallel audiobook generator.

Synthesizes chunks concurrently via asyncio.Semaphore to speed up the
edge-tts pipeline, then combines with the same silence-trimming logic as
the main `text_to_speech` function.

Usage:
    python scripts/generate_audiobook_parallel.py <pdf_path> <output_mp3> [concurrency]
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

import edge_tts

# Allow "import src..." from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pdf_parser import parse_pdf
from src.tts_engine import (
    TTSConfig,
    combine_audio_files,
    split_text_into_chunks,
)


async def _synth_one(
    idx: int,
    text: str,
    voice: str,
    rate: str,
    volume: str,
    out_dir: str,
    sem: asyncio.Semaphore,
    done_counter: list[int],
    total: int,
    start_time: float,
) -> str:
    async with sem:
        path = os.path.join(out_dir, f"chunk_{idx:04d}.mp3")
        comm = edge_tts.Communicate(text=text, voice=voice, rate=rate, volume=volume)
        await comm.save(path)
        done_counter[0] += 1
        done = done_counter[0]
        elapsed = time.time() - start_time
        rate_chunks = done / max(elapsed, 0.1)
        eta = (total - done) / max(rate_chunks, 0.01)
        print(
            f"  [{done}/{total}] chunk {idx} done  "
            f"(elapsed {elapsed:.0f}s, ETA {eta:.0f}s)",
            flush=True,
        )
        return path


async def _synth_all(
    chunks: list[str],
    config: TTSConfig,
    out_dir: str,
    concurrency: int,
) -> list[str]:
    voice = config.resolved_voice()
    sem = asyncio.Semaphore(concurrency)
    done = [0]
    start = time.time()
    tasks = [
        _synth_one(
            i, c, voice, config.rate, config.volume, out_dir, sem, done, len(chunks), start
        )
        for i, c in enumerate(chunks)
    ]
    return await asyncio.gather(*tasks)


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2]
    concurrency = int(sys.argv[3]) if len(sys.argv) > 3 else 8

    print(f"Parsing {pdf_path}...", flush=True)
    book = parse_pdf(pdf_path)
    print(
        f"  Title: {book.metadata.title}, "
        f"chapters: {len(book.chapters)}, chars: {book.total_chars}",
        flush=True,
    )

    chunks = split_text_into_chunks(book.full_text)
    print(f"  Chunks: {len(chunks)}", flush=True)

    config = TTSConfig(language="fi", voice="fi-FI-NooraNeural", rate="+0%")

    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="audiobook_") as tmp_dir:
        print(f"Synthesizing with concurrency={concurrency}...", flush=True)
        # Run the parallel synthesis
        paths = asyncio.run(_synth_all(chunks, config, tmp_dir, concurrency))

        # Sort by chunk index (asyncio.gather preserves order but be explicit)
        paths.sort()

        print(
            f"Synthesis done in {time.time() - t0:.0f}s.  "
            f"Combining with silence trimming...",
            flush=True,
        )
        combine_audio_files(paths, output_path)

    print(f"Done in {time.time() - t0:.0f}s!  Saved to {output_path}", flush=True)


if __name__ == "__main__":
    main()
