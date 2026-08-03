#!/usr/bin/env python3
"""Transcribe every chunk and report words the audio never says.

Duration cannot detect a truncated chunk, and the evidence is
uncomfortable: of four confirmed truncations found
in one corpus, three sat at or ABOVE their file's median speech rate.

    chunk   rate / median   lost
    ----------------------------------------------------
    en  4       1.033       "Tokens, total: 323,826"
    en 12       0.928       "and only for the Haiku tier"
    fi 18       0.892       "ja vain Haiku-tasolla"
    fi  1       0.855       a whole closing sentence

A chunk that drops its final clause does not necessarily get shorter:
the model speaks the surviving words a little slower and the ratio lands
in the normal band. One of them read FASTER than average while missing
its most important number. No duration threshold can see that, which is
why verify_narration.py is a screen and this reads the words instead.

What this does NOT do is decide for you. Three attempts at classifying
truncation by length — raw characters, all words, prose words — each
reported complete chunks as truncated, because the two sides do not
measure the same thing. The output points at pairs worth reading; the
reading is yours.

Run with the chatterbox venv, which is where faster-whisper lives:

    ./.venv-chatterbox/Scripts/python.exe \\
        .claude/skills/narrate-texts/scripts/verify_words.py \\
        --work <work-dir> --text <source.txt> --language fi
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

WORD = re.compile(r"[A-Za-zÄÖÅäöå][A-Za-zÄÖÅäöå-]{3,}")

# Number words are excluded outright. The normalizer spells digits out and
# the transcriber writes them back as digits, so every one reads as "never
# heard" — dozens of false alarms in any text about measurements, burying
# the real findings.
NUMBER_STEMS = (
    "nolla", "yksi", "yhde", "yhte", "kaksi", "kahde", "kahte", "kolme",
    "nelj", "viisi", "viide", "viite", "kuusi", "kuude", "seitsem",
    "kahdeks", "yhdeks", "kymmen", "toista", "sata", "sadan", "sataa",
    "tuhat", "tuhan", "miljoon", "pilkku", "prosentti", "sekunti",
    "minuutti", "miinus", "kertaa", "dollari", "euroa", "pikseli",
    "zero", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "teen", "twenty",
    "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand", "million", "billion", "percent", "point",
    "minus", "dollar", "cent", "pixel", "second",
)


def is_number_word(w: str) -> bool:
    return any(w.startswith(s) or s in w for s in NUMBER_STEMS)


class _Chapter:
    def __init__(self, title, content):
        self.title, self.content = title, content


def _load_runner(repo):
    spec = importlib.util.spec_from_file_location(
        "gca", f"{repo}/scripts/generate_chatterbox_audiobook.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _chunk_at(mod, raw, language, size):
    out = mod._prepare_chapter_chunks(_Chapter("Text", raw), size, 100000, language)
    chunks = out[0] if isinstance(out, tuple) else out
    return [c if isinstance(c, str) else getattr(c, "text", str(c)) for c in chunks]


def _detect(mod, raw, language, work):
    """Match the chunk size to the WAVs on disk.

    Files converted before the switch to 200-char chunks still have 300,
    and chunking at the wrong size misaligns every comparison silently.
    """
    on_disk = len(glob.glob(f"{work}/.chunks/ch01_chunk*.wav"))
    for size in (200, 300):
        chunks = _chunk_at(mod, raw, language, size)
        if len(chunks) == on_disk:
            return chunks, size
    return None, on_disk


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--language", required=True)
    ap.add_argument("--chunk-chars", type=int)
    # Derived from this file's location, not hardcoded: the script is
    # committed and has to work in anyone's clone.
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[4]))
    args = ap.parse_args()

    mod = _load_runner(args.repo)
    raw = open(args.text, encoding="utf-8").read()

    if args.chunk_chars:
        chunks = _chunk_at(mod, raw, args.language, args.chunk_chars)
    else:
        chunks, size = _detect(mod, raw, args.language, args.work)
        if chunks is None:
            print(f"!! {size} WAVs match no known chunk size — skipping")
            return 2

    from faster_whisper import WhisperModel
    model = WhisperModel("large-v3", device="cuda", compute_type="float16")

    findings = 0
    for i, text in enumerate(chunks):
        wav = f"{args.work}/.chunks/ch01_chunk{i:04d}.wav"
        # vad_filter MUST stay off. With it on, the transcriber silently
        # discards short or quiet chunks and returns nothing, which reads
        # here as a total loss — two perfectly good title chunks were
        # reported as truncated that way.
        segs, _ = model.transcribe(
            wav, language=args.language, beam_size=5,
            vad_filter=False, condition_on_previous_text=False,
        )
        heard = " ".join(s.text.strip() for s in segs)

        # Compare against the transcript with every non-letter stripped.
        # The transcriber re-spaces and re-hyphenates freely ("backend" ->
        # "back end", "re-arm" -> "rearm") and none of that is a defect.
        flat = re.sub(r"[^a-zäöå]", "", heard.lower())
        missing = [w for w in (x.lower().strip("-") for x in WORD.findall(text))
                   if not is_number_word(w)
                   and re.sub(r"[^a-zäöå]", "", w) not in flat]
        if not missing:
            continue

        # Truncation needs BOTH signals: a missing word among the chunk's
        # final words, AND a transcript materially shorter than the text.
        # The tail test alone is not enough — a spelling difference that
        # happens to fall near the end looks identical to a lost clause,
        # and on its own it flagged "judgement" (spelt "judgment" by the
        # transcriber) as a defect in a chunk that was entirely complete.
        tail = [w.lower().strip("-") for w in WORD.findall(text)][-8:]
        tail_loss = any(w in tail for w in missing)
        # No length test. Three versions of one were tried — raw
        # characters, all words, prose words — and each reported complete
        # chunks as truncated, because the two sides are not measuring
        # the same thing. The normalizer spells digits out and the
        # transcriber writes them back ("neljä pilkku kahdeksan" vs
        # "4,8"), and Finnish compounds merge and split across the
        # boundary ("M tulostehinnalla" vs "M-turoste-hinnolla"). Both
        # move the count further than a lost clause does.
        #
        # What remains honest is the position: a word near the END of a
        # chunk that was not heard is where truncation shows up, so it is
        # worth a look. It is a pointer, not a verdict, and the label says
        # so — a checker that cries wolf is worse than none, and one that
        # claims certainty it does not have is worse still.
        kind = "TAIL?" if tail_loss else "differs"
        findings += 1
        # Newlines are collapsed so each record stays exactly three lines.
        # A title chunk carries a blank line after the heading, and left
        # alone it pushes HEARD out of alignment for anything reading this
        # output back.
        flat_text = " ".join(text.split())
        print(f"[{kind}] chunk{i:04d} missing {missing}")
        print(f"    TEXT : {flat_text}")
        print(f"    HEARD: {' '.join(heard.split())}")
        print()

    print(f"{findings} chunk(s) flagged of {len(chunks)}.")
    print("TAIL?   = a word near the END was not heard. Truncation shows up "
          "here, but so does an ordinary transcriber miss. READ the pair.")
    print("differs = a word elsewhere was not heard; usually spelling.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
