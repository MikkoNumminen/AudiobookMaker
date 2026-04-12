#!/usr/bin/env python
"""dev_chatterbox_fi.py — Finnish-NLP/Chatterbox-Finnish smoke test.

Developer-only tool for evaluating Chatterbox-Finnish on this Mac. Not
part of the shipped AudiobookMaker app. Sits next to dev_qwen_tts.py.

What this does:
    1. Loads ChatterboxMultilingualTTS from ResembleAI/chatterbox via
       from_pretrained() — the multilingual model natively supports
       Finnish (language_id='fi') out of the box; no weight swap needed
       for a smoke test.
    2. Optionally (--finnish-finetune) overrides engine.t3 with the
       Finnish-NLP/Chatterbox-Finnish checkpoint, which claims MOS 4.34
       and WER 2.76% on Finnish. The finetune is T3-only on top of the
       multilingual base (not the English base), so shapes match.
    3. Synthesizes one Finnish sentence from turodokumentti.pdf (or a
       hardcoded fallback) using the bundled reference_finnish.wav as
       the voice clone prompt.
    4. Saves the result to dev_chatterbox_fi_out.wav and prints the
       wall-clock synthesis time so we can decide whether this is a
       viable CPU/MPS path for real audiobooks.

Usage:
    .venv-chatterbox/bin/python dev_chatterbox_fi.py
    .venv-chatterbox/bin/python dev_chatterbox_fi.py --device cpu
    .venv-chatterbox/bin/python dev_chatterbox_fi.py --device mps
    .venv-chatterbox/bin/python dev_chatterbox_fi.py --text "Oma testilauseeni."
    .venv-chatterbox/bin/python dev_chatterbox_fi.py --finnish-finetune

Status: EXPERIMENTAL. Chatterbox upstream targets CUDA/CPU; MPS works
for most ops but some (perth watermarker, parts of s3gen) may silently
fall back to CPU. Start with --device cpu for the first run.

Finnish model card: https://huggingface.co/Finnish-NLP/Chatterbox-Finnish
Base model:         https://huggingface.co/ResembleAI/chatterbox
Upstream package:   pip install chatterbox-tts

INSTALLATION:
    # Reuse the existing .venv-chatterbox (Python 3.11, already has torch).
    .venv-chatterbox/bin/pip install chatterbox-tts safetensors
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

FINNISH_REPO = "Finnish-NLP/Chatterbox-Finnish"
FINNISH_T3_FILE = "models/best_finnish_multilingual_cp986.safetensors"
FINNISH_REF_WAV = "samples/reference_finnish.wav"

# Fallback sentence if no --text is provided and no PDF is readable.
# Intentionally covers Finnish gemination, long vowels, and a foreign
# loanword — the exact traps that expose a bad Finnish TTS.
DEFAULT_SENTENCE = (
    "Tervetuloa kokeilemaan suomenkielistä Chatterbox-puhesynteesiä. "
    "Tämä lause sisältää pitkiä vokaaleja ja kaksoiskonsonantteja."
)

# Finnish "Golden Settings" from the model card.
FI_REPETITION_PENALTY = 1.5
FI_TEMPERATURE = 0.8
FI_EXAGGERATION = 0.5
FI_CFG_WEIGHT = 0.3


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Smoke test Chatterbox-Finnish on this machine.",
    )
    p.add_argument(
        "--device",
        choices=("cpu", "mps", "cuda"),
        default="cpu",
        help="Torch device. Default cpu — safest starting point on Mac.",
    )
    p.add_argument(
        "--text",
        default=None,
        help="Sentence to synthesize. Default: a Finnish gemination probe.",
    )
    p.add_argument(
        "--pdf",
        default=None,
        help="Optional PDF — first ~200 chars of first page will be used "
             "if --text is not given.",
    )
    p.add_argument(
        "--output",
        default="dev_chatterbox_fi_out.mp3",
        help="Output audio path. Extension decides format: .mp3 (via pydub "
             "+ ffmpeg, matches the main app) or .wav (raw torchaudio).",
    )
    p.add_argument(
        "--ref-audio",
        default=None,
        help="Override reference clip. Default: samples/reference_finnish.wav "
             "from the HF repo.",
    )
    p.add_argument(
        "--finnish-finetune",
        action="store_true",
        help="Override the multilingual T3 weights with the Finnish-NLP "
             "finetune (best_finnish_multilingual_cp986.safetensors, ~2 GB, "
             "already cached from earlier runs).",
    )
    p.add_argument(
        "--chunks",
        type=int,
        default=1,
        help="How many ~500-char chunks of prose to synthesize and "
             "concatenate. Each chunk costs ~150s wall-clock on CPU Mac "
             "and yields ~25s of audio. Default 1 (smoke test). Use 3-5 "
             "for a listenable sample.",
    )
    p.add_argument(
        "--chunk-chars",
        type=int,
        default=300,
        help="Target characters per chunk. Upstream community consensus "
             "(resemble-ai/chatterbox issues #60, #424, PR #343) says "
             "anything over ~300 chars causes hallucinations and prosody "
             "drift. Finnish compounds make this worse. 300 is the "
             "fluency-optimal setting.",
    )
    p.add_argument(
        "--inter-chunk-ms",
        type=int,
        default=100,
        help="Silence inserted between chunks in the final MP3. Edge-TTS "
             "used 200 ms; Chatterbox needs 100 ms or less because 200 ms "
             "reads as a sentence-final pause mid-paragraph.",
    )
    p.add_argument(
        "--tail-trim-db",
        type=float,
        default=-30.0,
        help="Trailing-silence trim threshold in dB. Edge-TTS uses -45 dB "
             "because it emits clean silence. Chatterbox emits 200-800 ms "
             "of breath/hum NOISE at chunk end (see upstream issues #48, "
             "#271, #388), which is ABOVE -45 dB and so survives the old "
             "trimmer. -30 dB catches the breath noise.",
    )
    p.add_argument(
        "--cfg-weight",
        type=float,
        default=FI_CFG_WEIGHT,
        help="Classifier-free-guidance weight. Finnish golden = 0.3. "
             "For cross-language accent transfer (e.g. English reference "
             "→ Finnish output), raise toward 0.8-1.0 to let more of "
             "the reference voice's accent bleed through. Upstream README "
             "says cfg_weight=0.0 MITIGATES accent bleed-through.",
    )
    p.add_argument(
        "--exaggeration",
        type=float,
        default=FI_EXAGGERATION,
        help="Emotion/prosody exaggeration. Finnish golden = 0.5. "
             "Raise toward 0.7-0.9 to amplify reference voice character.",
    )
    p.add_argument(
        "--no-normalize",
        dest="normalize",
        action="store_false",
        default=True,
        help="Bypass the Finnish text normalizer (keeps raw digits). "
             "Debug-only — Chatterbox reads digits in English without this.",
    )
    p.add_argument(
        "--temperature",
        type=float,
        default=FI_TEMPERATURE,
        help="Sampling temperature. Finnish golden = 0.8.",
    )
    return p.parse_args()


_SENTENCE_START_RE = re.compile(r"[.!?…]\s+([A-ZÅÄÖ])")


# --- Finnish text normalization ---------------------------------------------
#
# Chatterbox-TTS has NO built-in number-to-word normalization, so Finnish
# years like "1500" get pronounced digit-by-digit in English ("one five zero
# zero"). Edge-TTS Noora handles it correctly server-side; we replicate the
# same behavior locally via num2words(lang='fi'). See
# docs/tts_text_normalization_cases.md for the pattern inventory extracted
# from turodokumentti.pdf.
#
# Passes run in a fixed order because earlier patterns must consume their
# digits before the generic "bare integer" fallback rewrites them. For
# example, "1500-luvulla" MUST be handled by pass C before pass G sees a
# loose 1500.

# Pass A: bibliographic citations — parens containing a 4-digit year and a
# Capitalized publisher-ish token. Conservative: requires BOTH.
_FI_CITE_RE = re.compile(
    r"\s*\(([^()]*?\b[A-ZÅÄÖ][\wäöåÄÖÅ]+[^()]*?\b\d{4}[a-z]?\b[^()]*?)\)"
)

# Pass B: elided-hyphen Finnish compounds (e.g. "keski-ja" → "keski- ja").
_FI_ELIDED_HYPHEN_RE = re.compile(
    r"(\w+)-(ja|tai|eli|sekä)\b", re.IGNORECASE
)

# Pass C: century/era expressions — digit + "-luku" declension suffix.
_FI_CENTURY_SUFFIXES = (
    "luvulla",
    "luvulta",
    "luvulle",
    "luvuilla",
    "luvusta",
    "luvut",
    "luvun",
    "luku",
)
_FI_CENTURY_RE = re.compile(
    r"(\d+)-(" + "|".join(_FI_CENTURY_SUFFIXES) + r")\b"
)

# Pass D: numeric ranges like "1500-1800" or "1100–1300".
_FI_RANGE_RE = re.compile(r"(\d{3,4})\s*[-–]\s*(\d{3,4})\b")

# Pass E: "s. 42" page abbreviation.
_FI_PAGE_RE = re.compile(r"\bs\.\s*(\d+)")

# Pass F: decimals (comma or dot separator).
_FI_DECIMAL_RE = re.compile(r"(\d+)[.,](\d+)")

# Pass G: any remaining bare integer.
_FI_INT_RE = re.compile(r"\d+")

# Pass H: split glued Finnish compound-number morphemes.
#
# num2words 0.5.14 emits Finnish compound numbers WITHOUT spaces between
# hundreds/tens/units morphemes — e.g. 1889 -> "tuhat
# kahdeksansataakahdeksankymmentäyhdeksän". Chatterbox-TTS then tokenizes
# the glued word as one giant token and mispronounces it. We insert a
# space after "sataa" (hundred, partitive form emitted by num2words for
# 200-900) and after "kymmentä" (ten, partitive) when another morpheme
# is glued on. Standalone teens like "viisitoista" (15) and "yksitoista"
# (11) are unaffected because they do not contain these morphemes.
_FI_MORPHEME_BOUNDARY_RE = re.compile(r"(sataa|kymmentä)(?=[a-zäöå])")


def _fi_split_number_compounds(text: str) -> str:
    """Insert spaces at morpheme boundaries in Finnish compound numbers.

    See :data:`_FI_MORPHEME_BOUNDARY_RE` for the rationale. Operates on
    already-normalized text (post num2words expansion).
    """
    return _FI_MORPHEME_BOUNDARY_RE.sub(r"\1 ", text)


def normalize_finnish_text(text: str, drop_citations: bool = True) -> str:
    """Expand Finnish-specific patterns so Chatterbox-TTS reads them correctly.

    Rewrites numbers, century expressions, numeric ranges, page abbreviations,
    and elided-hyphen compounds into plain word-form Finnish. Uses num2words
    (lazy import) for the actual digit → word conversion; if the package is
    not installed the function degrades gracefully and returns the input
    unchanged.
    """
    if not text:
        return text
    try:
        from num2words import num2words  # type: ignore
    except ImportError:
        return text

    def _w(n: int) -> str:
        try:
            return num2words(n, lang="fi")
        except (NotImplementedError, OverflowError, ValueError):
            return str(n)

    # Pass A — drop bibliographic citations.
    if drop_citations:
        text = _FI_CITE_RE.sub("", text)

    # Pass B — elided-hyphen compounds (just insert a space).
    text = _FI_ELIDED_HYPHEN_RE.sub(r"\1- \2", text)

    # Pass C — century expressions.
    def _century_sub(m: re.Match) -> str:
        return f"{_w(int(m.group(1)))} {m.group(2)}"

    text = _FI_CENTURY_RE.sub(_century_sub, text)

    # Pass D — numeric ranges (must run before decimals/bare ints).
    def _range_sub(m: re.Match) -> str:
        return f"{_w(int(m.group(1)))} {_w(int(m.group(2)))}"

    text = _FI_RANGE_RE.sub(_range_sub, text)

    # Pass E — "s. 42" page abbreviation.
    def _page_sub(m: re.Match) -> str:
        return f"sivu {_w(int(m.group(1)))}"

    text = _FI_PAGE_RE.sub(_page_sub, text)

    # Pass F — decimals.
    def _decimal_sub(m: re.Match) -> str:
        whole = int(m.group(1))
        frac_str = m.group(2)
        # num2words Finnish handles floats natively.
        try:
            return num2words(float(f"{whole}.{frac_str}"), lang="fi")
        except (NotImplementedError, ValueError):
            return f"{_w(whole)} pilkku {' '.join(_w(int(d)) for d in frac_str)}"

    text = _FI_DECIMAL_RE.sub(_decimal_sub, text)

    # Pass G — any remaining bare integers.
    def _int_sub(m: re.Match) -> str:
        return _w(int(m.group(0)))

    text = _FI_INT_RE.sub(_int_sub, text)

    # Pass H — split glued compound-number morphemes (post num2words).
    text = _fi_split_number_compounds(text)

    # Collapse whitespace introduced by deletions/substitutions.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([.,;:!?])", r"\1", text)
    return text


def _trim_to_sentence_start(content: str) -> str:
    """Slice content so it begins at a capital letter starting a fresh sentence.

    pdf_parser.py sometimes eats the first N chars of a chapter body into
    the chapter title (e.g. "1500. Nämä jaot..." → title, body starts
    "listen kehityslinjojen..."). That produces a chunk 1 that begins
    mid-word, which sounds broken from the very first syllable.

    If the first char is already uppercase Finnish, return content as-is.
    Otherwise find the first `[.!?…]\\s+[A-ZÅÄÖ]` boundary and slice from
    the capital letter.
    """
    if not content:
        return content
    if content[0].isupper() or content[0] in "«\"'(":
        return content
    m = _SENTENCE_START_RE.search(content)
    if m:
        return content[m.start(1):]
    return content


def pick_text(args: argparse.Namespace) -> list[str]:
    """Return a list of chunks to synthesize (one per generate() call).

    - args.text → run through the Finnish sentence-aware chunker with
      `max_chars=args.chunk_chars`, then limit to `args.chunks` pieces.
      This is critical because Chatterbox's decoder emits early EOS on
      multi-sentence chunks — feeding it one sentence at a time is the
      only reliable way to synthesize a paragraph on either CPU or GPU.
    - args.pdf  → first real prose chapter, sliced into N chunks of
      ~args.chunk_chars each, where N = args.chunks.
    - otherwise → single-chunk run of DEFAULT_SENTENCE.
    """
    if args.text:
        raw = args.text
        if getattr(args, "normalize", True):
            normalized = normalize_finnish_text(raw)
            if normalized != raw:
                print(f"  normalizer rewrote {abs(len(normalized) - len(raw))} "
                      f"chars of --text input")
            raw = normalized
        try:
            from src.tts_engine import split_text_into_chunks  # type: ignore
        except ImportError:
            return [raw]
        all_chunks = split_text_into_chunks(raw, max_chars=args.chunk_chars)
        if not all_chunks:
            return [raw]
        # In --text mode always synthesize every sentence the chunker
        # produced — the --chunks flag is a --pdf smoke-test limit and
        # applying it here would silently drop sentences after the
        # first one (the default of --chunks is 1). If you want to
        # limit --text output, pass a shorter string.
        if len(all_chunks) != 1:
            print(f"  --text split into {len(all_chunks)} sentence-chunks "
                  f"(chunk_chars={args.chunk_chars})")
        return all_chunks
    if args.pdf:
        # Reuse the main app's PDF parser so we get the same text cleanup
        # (letterspace fixup, hyphenation repair, chapter detection). A
        # naive doc[0].get_text() grabs the title page, which on books like
        # Turo's is "H E I K K I  P I H L A J A M Ä K I" — letterspaced
        # garbage that any TTS will fail on.
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from src.pdf_parser import parse_pdf  # type: ignore
            from src.tts_engine import split_text_into_chunks  # type: ignore
        except ImportError as exc:
            print(f"Could not import src.pdf_parser/tts_engine ({exc}); "
                  "falling back to default sentence.")
            return [DEFAULT_SENTENCE]
        book = parse_pdf(args.pdf)
        if not book.chapters:
            print("PDF has no detected chapters; using default sentence.")
            return [DEFAULT_SENTENCE]
        # Skip: empty/stub chapters, title pages with letterspacing
        # ("H E I K K I"), and table-of-contents entries (dot-dominated
        # lines like "Johdanto............1"). Real prose chapters have
        # roughly 0.9% dots (just sentence punctuation).
        for ch in book.chapters:
            content = ch.content.strip()
            if len(content) < 3000:
                continue
            dot_ratio = content.count(".") / len(content)
            if dot_ratio > 0.05:
                continue  # ToC entry
            words = content[:500].split()
            if not words:
                continue
            single = sum(1 for w in words if len(w) == 1 and w.isalpha())
            if single / len(words) > 0.2:
                continue  # letterspaced title page
            print(f"  using chapter {ch.index}: {ch.title[:60]!r}")
            # Trim the chapter preamble so we start at a real sentence.
            # pdf_parser.py can eat the first 50-60 chars of body into
            # the chapter title, leaving content[0] mid-word.
            trimmed = _trim_to_sentence_start(content)
            if trimmed != content:
                print(f"  trimmed {len(content) - len(trimmed)} preamble chars")
            content = trimmed
            # Normalize Finnish digits/abbreviations so Chatterbox reads them
            # correctly (it has no built-in number-to-word normalizer).
            if getattr(args, "normalize", True):
                before_len = len(content)
                content = normalize_finnish_text(content)
                delta = len(content) - before_len
                print(f"  normalizer changed {abs(delta)} chars "
                      f"({'+' if delta >= 0 else ''}{delta})")
            # Use the main app's sentence-aware chunker — it respects
            # Finnish abbreviations, initials, and decimals so we don't
            # split mid-sentence.
            all_chunks = split_text_into_chunks(
                content, max_chars=args.chunk_chars
            )
            selected = all_chunks[: max(1, args.chunks)]
            print(f"  chapter has {len(content)} chars → "
                  f"{len(all_chunks)} chunks, using first {len(selected)}")
            return selected
        return [DEFAULT_SENTENCE]
    return [DEFAULT_SENTENCE]


def main() -> int:
    args = parse_args()

    print(f"Device: {args.device}")
    print("Importing torch + chatterbox (first import is slow)…")
    t0 = time.time()
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    from huggingface_hub import hf_hub_download
    from safetensors.torch import load_file
    import torchaudio as ta
    print(f"  imports ready in {time.time() - t0:.1f}s")

    if args.device == "mps" and not torch.backends.mps.is_available():
        print("MPS not available on this machine; falling back to cpu.")
        args.device = "cpu"
    if args.device == "cuda" and not torch.cuda.is_available():
        print("CUDA not available on this machine; falling back to cpu.")
        args.device = "cpu"

    print(f"Loading ChatterboxMultilingualTTS onto {args.device}…")
    t0 = time.time()
    # NOTE: pass device as a plain string, NOT torch.device(...). Upstream
    # mtl_tts.from_local() checks `device in ["cpu", "mps"]`, which is a
    # string comparison — a torch.device object silently falls through to
    # map_location=None and crashes when deserializing CUDA-saved s3gen.pt
    # on a CPU-only machine.
    engine = ChatterboxMultilingualTTS.from_pretrained(device=args.device)
    print(f"  multilingual base loaded in {time.time() - t0:.1f}s")

    # Reference WAV from the Finnish repo — always needed (Chatterbox is
    # zero-shot voice-clone only; there is no speakerless mode).
    if args.ref_audio:
        ref_wav_path = args.ref_audio
    else:
        print(f"Fetching reference Finnish voice from {FINNISH_REPO}…")
        t0 = time.time()
        ref_wav_path = hf_hub_download(FINNISH_REPO, FINNISH_REF_WAV)
        print(f"  fetched in {time.time() - t0:.1f}s")
    print(f"  ref wav: {ref_wav_path}")

    if args.finnish_finetune:
        print(f"Fetching Finnish T3 finetune from {FINNISH_REPO}…")
        t0 = time.time()
        fi_ckpt_path = hf_hub_download(FINNISH_REPO, FINNISH_T3_FILE)
        print(f"  fetched in {time.time() - t0:.1f}s")
        print(f"  T3 ckpt: {fi_ckpt_path}")
        print("Swapping Finnish T3 weights into the multilingual engine…")
        sd = load_file(fi_ckpt_path)
        sd = {k[3:] if k.startswith("t3.") else k: v for k, v in sd.items()}
        missing, unexpected = engine.t3.load_state_dict(sd, strict=False)
        if missing:
            print(f"  missing keys: {len(missing)}")
        if unexpected:
            print(f"  unexpected keys: {len(unexpected)}")
    else:
        print("Using stock multilingual T3 weights (pass --finnish-finetune "
              "to swap in the Finnish-NLP checkpoint).")

    chunks = pick_text(args)
    print(f"Chunks to synthesize: {len(chunks)}")
    for i, ch in enumerate(chunks):
        preview = ch[:60].replace("\n", " ")
        print(f"  [{i + 1}/{len(chunks)}] ({len(ch)} chars) {preview!r}…")

    # Synthesize each chunk, save to a temp WAV, then combine via the main
    # app's combine_audio_files (silence trimming + inter-chunk pauses).
    import tempfile
    try:
        from src.tts_engine import combine_audio_files  # type: ignore
    except ImportError:
        combine_audio_files = None  # falls back to pydub concat below

    total_synth_s = 0.0
    total_audio_s = 0.0
    chunk_paths: list[str] = []
    tmp_dir_ctx = tempfile.TemporaryDirectory()
    tmp_dir = tmp_dir_ctx.name

    for i, chunk_text in enumerate(chunks):
        print(f"\n[{i + 1}/{len(chunks)}] Generating…")
        # WORKAROUND for upstream chatterbox-tts bug (v0.1.7): every call
        # to engine.generate() creates a new AlignmentStreamAnalyzer that
        # leaks state across chunks in THREE ways:
        #   1. It registers a new PyTorch forward hook on the attention
        #      layers WITHOUT saving the handle for removal. After call
        #      #1 the hooks accumulate and chunks 2+ collapse to ~0.4s
        #      of audio with immediate token_repetition forced-EOS.
        #      See chatterbox/models/t3/inference/
        #          alignment_stream_analyzer.py:84
        #   2. It mutates tfmr.config.output_attentions = True and
        #      tfmr.config._attn_implementation = 'eager' and never
        #      restores them, so chunk 2+ inherit the mutated config.
        #      See chatterbox/models/t3/inference/
        #          alignment_stream_analyzer.py:86
        #   3. T3.inference() stores self.patched_model / self.compiled
        #      on the instance; the stale patched_model (and its old
        #      analyzer) lives on until GC'd, compounding the pollution.
        #      See chatterbox/models/t3/t3.py:273
        # Clear the hooks, reset the compiled flag, and restore the
        # attention config so each chunk starts from a clean state.
        try:
            for layer in engine.t3.tfmr.layers:
                layer.self_attn._forward_hooks.clear()
        except AttributeError:
            pass  # upstream may rename tfmr.layers someday
        # Also reset T3's compiled flag and restore the mutated attention
        # config so the next chunk builds a fresh analyzer with a clean
        # tfmr state.
        try:
            engine.t3.compiled = False
            engine.t3.tfmr.config._attn_implementation = "sdpa"
            engine.t3.tfmr.config.output_attentions = False
        except AttributeError:
            pass
        t0 = time.time()
        wav = engine.generate(
            chunk_text,
            language_id="fi",
            audio_prompt_path=ref_wav_path,
            repetition_penalty=FI_REPETITION_PENALTY,
            temperature=args.temperature,
            exaggeration=args.exaggeration,
            cfg_weight=args.cfg_weight,
        )
        dt = time.time() - t0
        audio_s = wav.shape[-1] / engine.sr
        total_synth_s += dt
        total_audio_s += audio_s
        rtf_i = dt / audio_s if audio_s > 0 else float("inf")
        print(f"  wall-clock: {dt:.1f}s  audio: {audio_s:.1f}s  rtf: {rtf_i:.2f}x")

        chunk_path = Path(tmp_dir) / f"chunk_{i:04d}.wav"
        ta.save(str(chunk_path), wav, engine.sr)
        chunk_paths.append(str(chunk_path))

    overall_rtf = total_synth_s / total_audio_s if total_audio_s > 0 else float("inf")
    print(f"\nTotals:")
    print(f"  synth wall-clock: {total_synth_s:.1f}s")
    print(f"  audio duration:   {total_audio_s:.1f}s")
    print(f"  overall rtf:      {overall_rtf:.2f}x")

    out_path = Path(args.output).resolve()
    print(f"\nCombining {len(chunk_paths)} chunks into {out_path.name}…")
    # Chatterbox needs different silence/gap handling than Edge-TTS:
    #   * Silero-VAD catches breath/hum NOISE at chunk end that dB
    #     thresholds miss (noise sits above -25 dB). See upstream
    #     issues #48, #271, #388, PR #164 (auto-editor).
    #   * 100 ms inter-chunk gap (not 200 ms) because 200 ms reads as
    #     sentence-final in the middle of a paragraph. See PR #343,
    #     devnen/Chatterbox-TTS-Server.
    # Falls back to pydub dB threshold if silero-vad is not installed.
    from pydub import AudioSegment
    from pydub.silence import detect_leading_silence
    try:
        import torch as _torch
        import torchaudio as _ta
        from silero_vad import load_silero_vad, get_speech_timestamps
        _vad_model = load_silero_vad()
        _use_vad = True
        print("  using Silero-VAD for tail trimming")
    except Exception as _vad_exc:
        _vad_model = None
        _use_vad = False
        print(f"  silero-vad not available ({_vad_exc}); "
              f"falling back to dB trim at {args.tail_trim_db} dB")

    def _trim_chatterbox(seg: AudioSegment) -> AudioSegment:
        if _use_vad:
            # Resample to 16 kHz for VAD (Silero wants 16 kHz mono).
            samples = _torch.tensor(seg.get_array_of_samples(),
                                    dtype=_torch.float32) / 32768.0
            if seg.channels > 1:
                samples = samples.view(-1, seg.channels).mean(dim=1)
            sr = seg.frame_rate
            wav16 = _ta.functional.resample(samples, sr, 16000)
            # Silero-VAD is English-trained and under-estimates the end of
            # Finnish sentences — quiet unstressed endings like -ssa, -een,
            # -alla drop below the default 0.5 threshold and get cut. Loosen:
            #   * threshold=0.3 (was 0.5) — accept quieter speech
            #   * min_silence_duration_ms=500 (was 100) — don't declare a
            #     silence inside a word-final vowel's tail
            #   * 200 ms pad at the tail (was 50) — extra safety for Finnish
            ts = get_speech_timestamps(wav16, _vad_model, sampling_rate=16000,
                                       threshold=0.3,
                                       min_silence_duration_ms=500)
            if not ts:
                return seg
            # Slice from first speech start to last speech end, mapped back
            # to the original sample rate, with 100 ms head pad and 200 ms
            # tail pad so Finnish sentence-final endings don't get clipped.
            first_start_ms = int(ts[0]["start"] * 1000 / 16000) - 100
            last_end_ms = int(ts[-1]["end"] * 1000 / 16000) + 200
            first_start_ms = max(0, first_start_ms)
            last_end_ms = min(len(seg), last_end_ms)
            if last_end_ms <= first_start_ms:
                return seg
            return seg[first_start_ms:last_end_ms]
        # pydub dB fallback
        lead = detect_leading_silence(seg, silence_threshold=args.tail_trim_db)
        trail = detect_leading_silence(seg.reverse(), silence_threshold=args.tail_trim_db)
        start = max(0, lead - 30)
        end = len(seg) - max(0, trail - 30)
        if end <= start:
            return seg
        return seg[start:end]

    combined = AudioSegment.empty()
    gap = AudioSegment.silent(duration=args.inter_chunk_ms)
    for i, p in enumerate(chunk_paths):
        seg = _trim_chatterbox(AudioSegment.from_file(p))
        combined += seg
        if i < len(chunk_paths) - 1:
            combined += gap
    fmt = out_path.suffix.lstrip(".").lower() or "mp3"
    combined.export(str(out_path), format=fmt)

    tmp_dir_ctx.cleanup()
    print(f"Saved {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
