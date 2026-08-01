---
name: narrate-texts
description: Turn text files into MP3 audiobooks with Chatterbox (Grandmom for English, Isoäiti for Finnish) and prove the audio actually says what the text says. Use whenever the user hands over blog posts, articles, or any .txt to "make audio from", "narrate", "read aloud", "turn into audiobooks", or asks to re-narrate something after a normalizer fix. Bundles the batch runner and the verifier so neither gets rewritten from scratch, and routes the run to a Fable subagent to keep the token cost off the main thread. CRITICAL — a conversion is not done until a transcript check has passed; duration alone cannot see a dropped clause or a mispronounced word.
---

# narrate-texts

Convert text to narrated MP3, then prove it.

The conversion is the easy half. The half that goes wrong silently is
verification, because the failure mode is **audio that sounds perfect and
is missing words**. Chatterbox can emit end-of-speech early; the MP3
assembles from the stub without complaint.

## Delegate the run

The convert-and-verify loop is mechanical: run a script, read a table,
transcribe outliers. Hand it to a **Fable** subagent so the tokens do not
land on the main thread.

```
Agent(
  subagent_type: "general-purpose",
  model: "fable",
  description: "Narrate and verify texts",
  prompt: """
  Narrate text files to MP3 and verify the result. Work in
  D:/koodaamista/AudiobookMaker (adjust if the repo lives elsewhere).

  1. Check free VRAM: `nvidia-smi --query-gpu=memory.free --format=csv,noheader`
     Chatterbox needs ~4-6 GB. If less is free, STOP and report which
     PIDs hold it. Never kill a process yourself.

  2. Convert:
     python .claude/skills/narrate-texts/scripts/narrate_batch.py \
         --source <SOURCE_DIR>

     <SOURCE_DIR> holds <lang>/<slug>.txt subdirectories. Add
     `--languages en` or `--only slug-a,slug-b` to narrow it, `--force`
     to redo existing output. Never run two conversions at once.

  3. Screen on duration (cheap, catches gross failures):
     ./.venv-chatterbox/Scripts/python.exe \
         .claude/skills/narrate-texts/scripts/verify_narration.py \
         --root .local/audiobooks/blog --texts <SOURCE_DIR> \
         --chunk-chars 200

  4. Then get the verdict, per file — this is the step that matters:
     ./.venv-chatterbox/Scripts/python.exe \
         .claude/skills/narrate-texts/scripts/verify_words.py \
         --work .local/audiobooks/blog/work/<lang>/<slug> \
         --text <SOURCE_DIR>/<lang>/<slug>.txt --language <lang>

     A duration PASS proves nothing: three of four known truncations
     read at or above their file's median rate.

  5. Read every TEXT/HEARD pair it prints. [TRUNCATED] means content is
     missing from the end — a real defect. [differs] is usually the
     transcriber's spelling; judge each one.

  Report: per file, the duration and PASS/FAIL, plus every mismatch you
  found quoted verbatim. Do not report success on duration alone. If
  anything failed, say exactly which chunk and what was wrong.
  """
)
```

Do it inline instead only when it is a single short file, or when the
subagent has already failed and you need to see the raw output.

## The two scripts

| Script | Does | Run with |
|---|---|---|
| `.claude/skills/narrate-texts/scripts/narrate_batch.py` | sequential conversion, handles the CLI quirks, logs, prints a summary | normal `python` |
| `.claude/skills/narrate-texts/scripts/verify_narration.py` | per-chunk duration vs the file's own median — a cheap screen | `./.venv-chatterbox/Scripts/python.exe` |
| `.claude/skills/narrate-texts/scripts/verify_words.py` | transcribes EVERY chunk, reports words the audio never says — **the verdict** | `./.venv-chatterbox/Scripts/python.exe` |

All take `--help`. Do not rewrite any of them inline.

### Duration is a screen. Words are the verdict.

Of four confirmed truncations in one corpus, **three sat at or above
their file's median speech rate**:

| chunk | rate / median | what was lost |
|---|---|---|
| en 4 | **1.033** | "Tokens, total: 323,826" |
| en 12 | 0.928 | "and only for the Haiku tier" |
| fi 18 | 0.892 | "ja vain Haiku-tasolla" |
| fi 1 | 0.855 | an entire closing sentence |

A chunk that drops its final clause does not necessarily get shorter —
the model speaks the surviving words a little slower and the ratio lands
in the normal band. One read FASTER than average while missing its most
important number.

So `verify_narration.py` passing means nothing on its own. Always finish
with `verify_words.py`.

## Voices

| Language | Flag | Voice |
|---|---|---|
| English | `--language en` | Grandmom |
| Finnish | `--language fi` | Isoäiti |

Same engine (`chatterbox_grandmom`) and the same voice id for both — only
`--language` differs. They are one persona on two pipelines: Finnish runs
a T3 finetune, English the multilingual base plus a reference clip.

## Things that will bite you

**`--output foo.mp3` is a work directory.** The MP3 lands at
`foo/01_Text.mp3`, the CLI prints `Done:` with an empty path, and exits
0. A script trusting `--output` reports success and leaves no file.
`narrate_batch.py` already handles this.

**Never average `.chunk_stats.jsonl`.** It appends one record per
synthesis *attempt* — a 64-chunk chapter can hold 108 records. Mixing
discarded takes with final ones under-reports bad chunks. It produced two
false "verified clean" calls on a file that had nine truncations. Read
the WAV files; `verify_narration.py` does.

**Judge a chunk against its neighbours, not a constant.** The runner's
absolute floor has to stay safe for the fastest text in the project, so
it is far too loose for any given chapter — chunks at 0.058-0.062 s/char
cleared it while sitting well under neighbours at 0.070. The runner's
Pass R now sweeps per chapter automatically, but it goes quiet when a
chapter is more than half broken, so verify anyway.

**English fails quietly.** Finnish truncates, which shortens the chunk
and shows up in a duration check. English drops or mangles content at
full length: `0 to −90px` was narrated "0 to 90 pecs" (sign gone, meaning
flipped) and `gap.` as "gapage". Neither changed the duration. **A clean
duration report on English is not evidence.** Transcribe it.

**Identical retries mean a bad input.** If re-rolls reproduce the same
defect, stop blaming sampling and read the text — look for a character
the model cannot pronounce. See `docs/tts_symbol_handling.md`.

**Only one conversion at a time.** ~4-6 GB VRAM each; two thrash the page
file and can freeze the machine.

**200-char chunks, not the 300 default.** Longer chunks early-stop
measurably more often. `narrate_batch.py` defaults to 200.

**Changing the text invalidates the whole cache.** The chunk cache is
keyed by INDEX, so shifted boundaries splice audio from the old text into
the new one. Always re-convert fresh after an edit; `--keep-cache` exists
but is unsafe unless the text is byte-identical.

## When verification fails

1. Read the flagged chunk's `TEXT` and `HEARD` side by side.
2. If words are missing at the end → truncation. Look for an
   unpronounceable character; check the run log for
   `[normalizer …] dropped … unpronounceable symbol(s)`.
3. If a word came out wrong → a normalizer bug, not a synthesis bug.
   Reproduce it with `normalize_text("…", "en")` before touching audio.
4. Fix the normalizer, add a regression test, then re-narrate. Editing
   the source text to dodge the bug fixes one file and leaves the bug.
5. Re-verify with `--transcribe`. Every time.

## Output layout

```
.local/audiobooks/blog/<lang>/<slug>.mp3      final files
.local/audiobooks/blog/work/<lang>/<slug>/    work dir + chunk cache
.local/audiobooks/blog/narrate.log            full run log
```

Source texts stay where the user keeps them; nothing is copied into the
repo. Third-party source material never leaves `.local/`.
