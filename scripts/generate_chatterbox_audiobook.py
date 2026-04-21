#!/usr/bin/env python
"""generate_chatterbox_audiobook.py — full PDF -> MP3 audiobook via Chatterbox-TTS.

Synthesizes a complete Finnish audiobook from a PDF using the
ChatterboxMultilingualTTS base model with the Finnish-NLP T3 finetune
swapped in. Applies the proven v7 fix stack from dev_chatterbox_fi.py:

  1. Three state-leak workarounds around upstream chatterbox-tts v0.1.7
     (forward-hook clearing, compiled-flag reset, tfmr config restore).
  2. Silero-VAD "loose Finnish" tail-trim (threshold≈0.25,
     min_silence_duration_ms=500, +100 ms head pad, +500 ms tail pad).
  3. Finnish text normalization via src.tts_engine.normalize_finnish_text.
  4. Sentence-start preamble trimming (pdf_parser sometimes eats a few
     chars of the body into the chapter title).
  5. 300-char chunk sizing (upstream-consensus fluency sweet spot).
  6. 7 kHz low-pass + loudness normalize to -20 dBFS post-processing.

Hardware expectations:
  * Windows 11 with NVIDIA RTX 3080 Ti (16 GB VRAM), CUDA.
  * Also works on macOS CPU for development, but ~60x slower.
  * No MPS path — Chatterbox has silent fallbacks on MPS that hurt
    quality; we default to CPU on Mac and CUDA on Windows.

Resume semantics:
  * Per-chunk WAV cache at out/audiobook/{pdf_stem}/.chunks/
    ch{ci:02d}_chunk{chi:04d}.wav. Re-running the script skips any
    chunk whose WAV already exists. Ctrl-C is safe between chunks.
  * .progress.json in the output dir tracks completed chapters, total
    chunks done, wall-clock elapsed, and estimated remaining time.
  * Pass --no-resume to wipe the cache dir and start from scratch.

Output layout:
  out/audiobook/{pdf_stem}/
    .chunks/ch{ci:02d}_chunk{chi:04d}.wav   (intermediate)
    .progress.json                          (resume state)
    {idx:02d}_{safe_title}.mp3              (one per chapter)
    00_full.mp3                             (concatenated book)

Usage:
  # dev machine (macOS):
  .venv-chatterbox/bin/python scripts/generate_chatterbox_audiobook.py --pdf book.pdf
  # production (Windows):
  .venv-chatterbox\\Scripts\\python.exe scripts\\generate_chatterbox_audiobook.py --pdf book.pdf

For the GPU cloud alternative (rental RTX or A100), see
scripts/chatterbox_cloud_runbook.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
import warnings
from pathlib import Path

# --- Silence cosmetic upstream warnings --------------------------------------
# These are not actionable for the end user; suppressing them keeps the
# AudiobookMaker log panel free of yellow WARNING noise. Narrow message
# filters so any NEW upstream warnings still surface for investigation.
#
# Upstream noise comes from THREE distinct channels and each needs its own
# muzzle:
#   1. Python `warnings` module       -> warnings.filterwarnings(...)
#   2. Python `logging` module        -> logger.setLevel(...) per namespace
#   3. Raw stderr prints (transformers) -> TRANSFORMERS_VERBOSITY env var
# All three must be set BEFORE the offending libraries import.
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
# Belt-and-braces: also push it via PYTHONWARNINGS so subprocesses inherit.
os.environ.setdefault(
    "PYTHONWARNINGS",
    "ignore::FutureWarning,ignore::UserWarning",
)

warnings.filterwarnings("ignore", message=r".*LoRACompatibleLinear.*", category=FutureWarning)
warnings.filterwarnings("ignore", message=r".*torch\.backends\.cuda\.sdp_kernel.*", category=FutureWarning)
warnings.filterwarnings("ignore", message=r".*output_attentions.*", category=UserWarning)
warnings.filterwarnings("ignore", message=r".*Reference mel length.*", category=UserWarning)
warnings.filterwarnings("ignore", message=r".*HF_TOKEN.*unauthenticated requests.*", category=UserWarning)

# Module-level catch-alls for the three noisiest upstreams. Module regex is
# anchored at the start so sibling packages aren't accidentally silenced.
warnings.filterwarnings("ignore", category=FutureWarning, module=r"diffusers(\..*)?")
warnings.filterwarnings("ignore", category=UserWarning, module=r"diffusers(\..*)?")
warnings.filterwarnings("ignore", category=FutureWarning, module=r"transformers\.generation(\..*)?")
warnings.filterwarnings("ignore", category=UserWarning, module=r"transformers\.generation(\..*)?")
warnings.filterwarnings("ignore", category=FutureWarning, module=r"torch\.backends(\..*)?")
warnings.filterwarnings("ignore", category=UserWarning, module=r"huggingface_hub(\..*)?")
# The FutureWarning for `torch.backends.cuda.sdp_kernel()` is raised from
# inside `contextlib.contextmanager`, so its apparent "module" is
# `contextlib`, not `torch`. Catch it by category+filename pattern.
warnings.filterwarnings("ignore", category=FutureWarning, module=r"contextlib")

# The chatterbox AlignmentStreamAnalyzer and the "Reference mel length"
# notice are emitted through the `logging` module, not `warnings`. Mute
# them at the logger level. We keep ERROR so real failures still surface.
logging.getLogger("chatterbox").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)


class _RefMelFilter(logging.Filter):
    """Drop the cosmetic "Reference mel length is not equal..." message.

    This comes from `logging.warning(...)` called on the root logger by
    upstream chatterbox preprocessing, so we can't just silence a child
    namespace — we attach a narrow filter to the root logger instead.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        msg = record.getMessage()
        if "Reference mel length" in msg:
            return False
        return True


logging.getLogger().addFilter(_RefMelFilter())
# -----------------------------------------------------------------------------

# Make `src.*` importable when the script is run from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Point pydub at our bundled ffmpeg/ffprobe. Without this the final MP3
# assembly step fails with FileNotFoundError when the Chatterbox venv
# doesn't have ffmpeg on PATH — common in both dev and frozen installs.
try:
    from src.ffmpeg_path import setup_ffmpeg_path
    setup_ffmpeg_path()
except Exception as _exc:
    print(f"[setup] ffmpeg path setup failed: {_exc}", flush=True)

FINNISH_REPO = "Finnish-NLP/Chatterbox-Finnish"
FINNISH_T3_FILE = "models/best_finnish_multilingual_cp986.safetensors"
FINNISH_REF_WAV = "samples/reference_finnish.wav"

# Finnish "Golden Settings" from the model card.
FI_REPETITION_PENALTY = 1.5
FI_TEMPERATURE = 0.5
FI_EXAGGERATION = 0.5
FI_CFG_WEIGHT = 0.3

# Early-stop guard: if synthesized audio is much shorter than expected for
# the input character count, the T3 sampler likely emitted EOS early
# (truncated sentence). Normal Finnish ratio sits around 0.06 s/char;
# anything below this threshold is a synthesis failure, not a short
# sentence. Retry up to MIN_AUDIO_MAX_RETRIES times with fresh stochasticity
# and keep the longest result.
MIN_AUDIO_S_PER_CHAR = 0.040
MIN_AUDIO_RETRY_CHAR_FLOOR = 50  # skip retry for very short chunks
MIN_AUDIO_MAX_RETRIES = 2        # 1 initial + up to 2 retries

# Post-processing targets.
LOWPASS_HZ = 7000
TARGET_DBFS = -20.0

# Chapter skip heuristics.
MIN_CHAPTER_CHARS = 3000
MAX_DOT_RATIO = 0.05
MAX_SINGLE_LETTER_RATIO = 0.2

# Inter-chapter gap in the full-book concat.
INTER_CHAPTER_SILENCE_MS = 500

# Silero-VAD: English-trained; quiet Finnish word endings can read as silence.
# Keep tail padding generous — better a slightly long pause than clipped speech.
VAD_SPEECH_THRESHOLD = 0.25
VAD_MIN_SILENCE_MS = 500
VAD_HEAD_PAD_MS = 100
VAD_TAIL_PAD_MS = 500

# dB fallback when silero-vad is missing: trailing must stay conservative.
VAD_FALLBACK_HEAD_DB = -42.0
VAD_FALLBACK_TRAIL_DB = -52.0
VAD_FALLBACK_HEAD_KEEP_MS = 40
VAD_FALLBACK_TRAIL_KEEP_MS = 100

SETUP_INSTRUCTIONS = """\
chatterbox-tts is not installed. To set it up:

  1. python -m venv .venv-chatterbox
  2. .venv-chatterbox/bin/pip install --upgrade pip
  3. .venv-chatterbox/bin/pip install torch torchaudio chatterbox-tts safetensors
  4. .venv-chatterbox/bin/pip install silero-vad pydub num2words huggingface_hub PyMuPDF
  5. On Windows install ffmpeg and add it to PATH (pydub needs it).
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate a full Finnish PDF->MP3 audiobook via "
                    "Chatterbox-TTS + Finnish-NLP finetune.",
    )
    p.add_argument("--pdf", default=None, help="Input PDF file.")
    p.add_argument(
        "--epub",
        default=None,
        help="Input EPUB file (alternative to --pdf).",
    )
    p.add_argument(
        "--text-file",
        default=None,
        help="Input plain text file (alternative to --pdf / --epub).",
    )
    p.add_argument(
        "--out",
        default="out/audiobook",
        help="Output directory root. Per-book subdir is created.",
    )
    p.add_argument(
        "--chapters",
        default=None,
        help="Comma-separated list of chapter indices to synthesize. "
             "Default: all chapters passing skip heuristics.",
    )
    p.add_argument(
        "--chunks-per-chapter",
        type=int,
        default=0,
        help="Cap chunks per chapter (0 = unlimited). Useful for smoke tests.",
    )
    p.add_argument(
        "--device",
        choices=("cuda", "cpu", "auto"),
        default="auto",
        help="Torch device. 'auto' picks cuda if available, else cpu.",
    )
    p.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=True,
        help="(default) Re-use cached chunk WAVs on restart.",
    )
    p.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="Wipe the .chunks cache before starting.",
    )
    p.add_argument(
        "--chunk-chars",
        type=int,
        default=300,
        help="Target characters per chunk (upstream-consensus sweet spot).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse the PDF, count chunks per chapter, estimate total synth "
             "time from --rtf, print the plan and exit. Does NOT import torch.",
    )
    p.add_argument(
        "--rtf",
        type=float,
        default=0.17,
        help="Real-time factor used for --dry-run estimates. Observed ~0.17 "
             "on RTX 3080 Ti with the Finnish finetune.",
    )
    p.add_argument(
        "--ref-audio",
        default=None,
        help="Override reference voice clone WAV. Default: fetch from the "
             "Finnish-NLP/Chatterbox-Finnish repo on HuggingFace.",
    )
    p.add_argument(
        "--language",
        default="fi",
        choices=["fi", "en"],
        help="Synthesis language. 'fi' uses the Finnish T3 finetune (Grandmom "
             "in Finnish). 'en' uses the base multilingual model with a "
             "voice-clone reference (Grandmom in native English).",
    )
    p.add_argument(
        "--voice-pack",
        default=None,
        help="Optional directory containing a trained LoRA adapter (either "
             "peft-native with adapter_config.json or a packaged pack with "
             "adapter.pt).",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Helpers that don't need torch
# ---------------------------------------------------------------------------


def _safe_title(title: str) -> str:
    cleaned = "".join(
        c if c.isalnum() or c in " -_" else "_" for c in title.strip()
    )
    cleaned = cleaned.strip().replace("  ", " ")
    return cleaned[:80] or "chapter"


def _trim_to_sentence_start(content: str) -> str:
    """Slice content so it starts at a fresh sentence boundary.

    Mirrors dev_chatterbox_fi._trim_to_sentence_start: if the first char
    is already a capital/opening quote, return as-is; otherwise find the
    first ``[.!?...]\\s+[A-ZAAOAOU]`` boundary and slice from there.
    """
    import re
    if not content:
        return content
    if content[0].isupper() or content[0] in "\"'(\u00ab":
        return content
    m = re.search(r"[.!?\u2026]\s+([A-Z\u00c5\u00c4\u00d6])", content)
    if m:
        return content[m.start(1):]
    return content


def _chapter_is_prose(content: str) -> bool:
    """Apply the dev-script skip heuristics."""
    content = content.strip()
    if len(content) < MIN_CHAPTER_CHARS:
        return False
    dot_ratio = content.count(".") / len(content)
    if dot_ratio > MAX_DOT_RATIO:
        return False
    words = content[:500].split()
    if not words:
        return False
    single = sum(1 for w in words if len(w) == 1 and w.isalpha())
    if single / len(words) > MAX_SINGLE_LETTER_RATIO:
        return False
    return True


def _select_chapters(book, only: set[int] | None):
    """Return [(idx, chapter), ...] for chapters that will be synthesized.

    ``idx`` is the position in the filtered list (used for output filenames).
    """
    selected = []
    for ch in book.chapters:
        if not _chapter_is_prose(ch.content):
            continue
        if only is not None and ch.index not in only:
            continue
        selected.append(ch)
    return list(enumerate(selected, start=1))


def _prepare_chapter_chunks(chapter, chunk_chars: int, chunks_cap: int,
                            language: str = "fi"):
    """Normalize + chunk a chapter's content. Returns list[str].

    Routes through the language-aware dispatcher so English runs
    don't get Finnish-specific rewrites (Roman numerals expanded
    as Finnish ordinals, case-inflected numbers, etc.).
    """
    from src.tts_engine import split_text_into_chunks
    from src.tts_normalizer import normalize_text
    content = _trim_to_sentence_start(chapter.content.strip())
    content = normalize_text(content, language)
    chunks = split_text_into_chunks(content, max_chars=chunk_chars)
    if chunks_cap and chunks_cap > 0:
        chunks = chunks[:chunks_cap]
    return chunks


def _format_hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"


def _dry_run(args) -> int:
    """Parse the input book, print chunk plan, estimate synth time. No torch."""
    if args.epub:
        from src.epub_parser import parse_epub
        print(f"[dry-run] parsing {args.epub}", flush=True)
        book = parse_epub(args.epub)
    else:
        from src.pdf_parser import parse_pdf
        print(f"[dry-run] parsing {args.pdf}", flush=True)
        book = parse_pdf(args.pdf)
    only = None
    if args.chapters:
        only = {int(x) for x in args.chapters.split(",") if x.strip()}
    selected = _select_chapters(book, only)
    if not selected:
        print("[dry-run] no chapters passed the skip heuristics", flush=True)
        return 1

    total_chars = 0
    total_chunks = 0
    per_chunk_audio_s = 15.0  # rough: 300 chars -> ~15s Finnish audio
    print(f"[dry-run] {len(selected)} chapters pass filters", flush=True)
    for pos, ch in selected:
        chunks = _prepare_chapter_chunks(ch, args.chunk_chars,
                                         args.chunks_per_chapter,
                                         language=args.language)
        total_chars += len(ch.content)
        total_chunks += len(chunks)
        title_preview = ch.title[:60] if ch.title else f"chapter {ch.index}"
        print(
            f"  [{pos:02d}] idx={ch.index} {title_preview!r}: "
            f"{len(ch.content)} chars -> {len(chunks)} chunks",
            flush=True,
        )
    est_audio_s = total_chunks * per_chunk_audio_s
    est_synth_s = est_audio_s * args.rtf
    print(
        f"[dry-run] total: {total_chunks} chunks, "
        f"~{_format_hms(est_audio_s)} audio, "
        f"~{_format_hms(est_synth_s)} synth @ rtf={args.rtf}",
        flush=True,
    )
    return 0


# ---------------------------------------------------------------------------
# Main synthesis (lazy imports)
# ---------------------------------------------------------------------------


def _resolve_device(requested: str) -> str:
    import torch
    if requested == "auto":
        if torch.cuda.is_available():
            return "cuda"
        print("[warn] CUDA not available; falling back to cpu (slow).",
              flush=True)
        return "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        print("[warn] CUDA requested but not available; using cpu.",
              flush=True)
        return "cpu"
    return requested


def _clear_chatterbox_state(engine) -> None:
    """Reset Chatterbox state between chunks — prevent long-run drift.

    The upstream ``chatterbox-tts`` v0.1.7 ``AlignmentStreamAnalyzer`` leaks
    state in two ways that bite hard over a 4+ hour run:

    1. Forward hooks registered on transformer layers are never removed.
    2. Transformer config (``output_attentions``, ``_attn_implementation``)
       is mutated without restoration, and the "originals" are re-saved on
       every call as already-mutated values.

    See ``docs/upstream/chatterbox/BUG_REPORT.md`` for the full analysis.

    We mitigate the upstream bugs here AND add memory hygiene that the
    original workaround lacked. Without ``gc.collect()`` +
    ``torch.cuda.empty_cache()`` the CUDA allocator's cached blocks grow
    over thousands of chunks; combined with any residual analyzer state
    this manifests as monotonically worsening quality — sentence endings
    get swallowed more and more toward the end of a long book.

    Defense-in-depth, called before every ``engine.generate()``:

    * Clear stale forward hooks on all transformer layers.
    * Force ``compiled = False`` so the next call rebuilds ``patched_model``.
    * Drop the reference to the previous ``patched_model`` so Python can
      reclaim its analyzer (and the analyzer's closures over stale tensors).
    * Restore ``tfmr.config`` to the canonical Chatterbox-multilingual
      defaults.
    * ``gc.collect()`` to drop dead Python references promptly.
    * ``torch.cuda.empty_cache()`` to release the CUDA allocator's idle
      cached blocks so fragmentation does not accumulate.
    """
    import gc

    try:
        for layer in engine.t3.tfmr.layers:
            layer.self_attn._forward_hooks.clear()
    except AttributeError:
        pass
    try:
        engine.t3.compiled = False
        # Drop the previous patched_model so the old AlignmentStreamAnalyzer
        # (and its closures over CUDA tensors) can be freed before we build
        # the next one. Without this, the reference lingers until the next
        # generate() call overwrites it — which does still happen, but GC
        # timing is less predictable across thousands of calls.
        if hasattr(engine.t3, "patched_model"):
            engine.t3.patched_model = None
        engine.t3.tfmr.config._attn_implementation = "sdpa"
        engine.t3.tfmr.config.output_attentions = False
    except AttributeError:
        pass

    # Force Python GC so any just-dropped analyzer / patched_model is freed
    # before the next generate() allocates its replacement.
    gc.collect()

    # Release CUDA allocator idle blocks. On a long run this keeps the
    # working set stable instead of creeping upward with fragmentation.
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _chatterbox_hook_count(engine) -> int:
    """Return total forward-hook count across all transformer layers.

    Used for observability: if this grows beyond a small constant during
    a long run, ``_clear_chatterbox_state`` is not doing its job.
    """
    try:
        return sum(
            len(layer.self_attn._forward_hooks)
            for layer in engine.t3.tfmr.layers
        )
    except AttributeError:
        return -1


def _gpu_mem_stats_mb() -> dict[str, float]:
    """Return CUDA memory stats in MiB, or empty dict if CUDA unavailable.

    Included in per-chunk observability so we can see memory creep over
    a long run. ``allocated`` is live tensor memory; ``reserved`` is
    allocator-held memory (the number that grows on fragmentation).
    """
    try:
        import torch
        if not torch.cuda.is_available():
            return {}
        return {
            "allocated_mb": torch.cuda.memory_allocated() / (1024 * 1024),
            "reserved_mb": torch.cuda.memory_reserved() / (1024 * 1024),
        }
    except Exception:
        return {}


def _append_chunk_stats(stats_path: Path, record: dict) -> None:
    """Append a single chunk-stats record to the JSONL sidecar log.

    One line per chunk; survives Ctrl-C. Use this file to diagnose
    long-run drift — if any metric trends monotonically across chunks,
    we have a state leak.
    """
    try:
        with stats_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        # Observability is best-effort; never fail the synth loop on it.
        pass


def _bundled_grandmom_ref() -> str | None:
    """Return the path to the bundled Grandmom English reference WAV.

    Layout: <repo root or install root>/assets/voices/grandmom_reference.wav
    In the frozen app the file lives under the app's _internal/assets/voices/.
    """
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "assets" / "voices" / "grandmom_reference.wav",
        here.parent / "_internal" / "assets" / "voices" / "grandmom_reference.wav",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def _apply_lora_adapter(engine, voice_pack_dir: Path) -> None:
    """Apply a trained LoRA adapter to ``engine.t3.tfmr`` in place.

    Three on-disk layouts are supported:

    1. **peft-native** — ``voice_pack_dir/adapter_config.json`` plus a
       sibling ``adapter_model.safetensors`` (or ``.bin``). Loaded via
       :class:`peft.PeftModel.from_pretrained`, then merged.
    2. **packaged pack with sidecar config** — ``voice_pack_dir/adapter.pt``
       (safetensors bytes renamed, as produced by the voice-pack pipeline)
       alongside ``adapter_config.json``. peft's ``from_pretrained`` won't
       find its expected filenames, so the wrapper is reconstructed using
       ``LoraConfig.from_pretrained(voice_pack_dir)`` for accurate
       hyperparameters, then the state dict is loaded non-strictly.
    3. **packaged pack, legacy** — ``voice_pack_dir/adapter.pt`` only, no
       config. The wrapper falls back to the training defaults (r=32,
       alpha=32, dropout=0.0, target_modules q/k/v/o_proj, bias='none').

    After this call ``engine.t3.tfmr`` is an unwrapped ``nn.Module`` with
    the adapter deltas baked into the base weights, so the forward pass
    costs nothing extra.
    """

    import logging as _logging
    import torch

    log = _logging.getLogger(__name__)
    cfg_path = voice_pack_dir / "adapter_config.json"
    pt_path = voice_pack_dir / "adapter.pt"
    peft_st_path = voice_pack_dir / "adapter_model.safetensors"
    peft_bin_path = voice_pack_dir / "adapter_model.bin"
    has_peft_weights = peft_st_path.is_file() or peft_bin_path.is_file()

    if cfg_path.is_file() and has_peft_weights:
        from peft import PeftModel

        log.info(
            "[voice-pack] applying peft-native LoRA adapter from %s",
            voice_pack_dir,
        )
        engine.t3.tfmr = PeftModel.from_pretrained(
            engine.t3.tfmr, str(voice_pack_dir)
        )
        engine.t3.tfmr = engine.t3.tfmr.merge_and_unload()
        log.info("[voice-pack] adapter merged and unloaded")
        return

    if pt_path.is_file():
        from peft import LoraConfig, get_peft_model
        from peft.utils import set_peft_model_state_dict

        log.info(
            "[voice-pack] applying packaged LoRA adapter from %s",
            pt_path,
        )
        # Prefer a sidecar adapter_config.json so we use the exact LoRA
        # hyperparameters the adapter was trained with. Fall back to the
        # current voice_pack_train.py defaults only if the config is missing
        # (older packs predate the config preservation).
        if cfg_path.is_file():
            lora_cfg = LoraConfig.from_pretrained(str(voice_pack_dir))
            log.info(
                "[voice-pack] using sidecar adapter_config.json (r=%s, alpha=%s)",
                lora_cfg.r,
                lora_cfg.lora_alpha,
            )
        else:
            lora_cfg = LoraConfig(
                r=32,
                lora_alpha=32,
                lora_dropout=0.0,
                target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
                bias="none",
            )
            log.info("[voice-pack] using hardcoded LoRA defaults (no sidecar config)")
        engine.t3.tfmr = get_peft_model(engine.t3.tfmr, lora_cfg)

        state: dict
        try:
            from safetensors.torch import load_file

            state = load_file(str(pt_path))
        except Exception as exc:
            log.warning(
                "[voice-pack] safetensors load failed (%s); "
                "falling back to torch.load",
                exc,
            )
            state = torch.load(str(pt_path), weights_only=False)

        # ``set_peft_model_state_dict`` handles the key-name normalization
        # between the safetensors dump (which omits the ``.default``
        # adapter-name segment) and the live peft wrapper (which expects
        # it), so the plain ``load_state_dict`` fallback won't silently
        # leave the LoRA deltas at their zero initialization.
        incompat = set_peft_model_state_dict(engine.t3.tfmr, state)
        missing = getattr(incompat, "missing_keys", []) or []
        unexpected = getattr(incompat, "unexpected_keys", []) or []
        log.info(
            "[voice-pack] loaded adapter state (missing=%d, unexpected=%d)",
            len(missing),
            len(unexpected),
        )
        engine.t3.tfmr = engine.t3.tfmr.merge_and_unload()
        log.info("[voice-pack] adapter merged and unloaded")
        return

    raise FileNotFoundError(
        f"No LoRA adapter found in {voice_pack_dir}. Expected either "
        f"'adapter_config.json' (peft-native layout) or 'adapter.pt' "
        f"(packaged voice-pack layout)."
    )


def _load_engine(device: str, ref_override: str | None, language: str = "fi",
                 voice_pack_dir: Path | None = None):
    """Load Chatterbox. Returns (engine, ref_wav_path).

    Language routing (see memory/project_english_grandmom.md):
      - ``fi`` — loads the Finnish-NLP T3 finetune on top of the base
        multilingual model. Reference WAV is the Finnish-NLP sample
        (or --ref-audio override). This is Grandmom in Finnish.
      - ``en`` — base multilingual model only (English is trained in).
        Reference WAV is the bundled Grandmom clip so the voice-cloning
        pass carries Grandmom's timbre into native-English synthesis.
    """
    import torch  # noqa: F401  (imported to verify install before next steps)
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    from huggingface_hub import hf_hub_download
    from safetensors.torch import load_file

    print(f"[tts] loading ChatterboxMultilingualTTS on {device}...", flush=True)
    t0 = time.time()
    engine = ChatterboxMultilingualTTS.from_pretrained(device=device)
    print(f"[tts] base loaded in {time.time() - t0:.1f}s", flush=True)

    if language == "en":
        # Native English path — no T3 finetune, use the bundled Grandmom
        # reference clip for voice cloning.
        if ref_override:
            ref_wav_path = ref_override
        else:
            bundled = _bundled_grandmom_ref()
            if bundled is None:
                raise RuntimeError(
                    "Grandmom English reference WAV not found. "
                    "Expected at assets/voices/grandmom_reference.wav"
                )
            ref_wav_path = bundled
        print(f"[tts] English mode: base model + ref wav: {ref_wav_path}",
              flush=True)
        if voice_pack_dir is not None:
            _apply_lora_adapter(engine, voice_pack_dir)
        return engine, ref_wav_path

    # Finnish path (default) — keep existing finetune loading unchanged.
    if ref_override:
        ref_wav_path = ref_override
    else:
        print(f"[tts] fetching reference wav from {FINNISH_REPO}...",
              flush=True)
        ref_wav_path = hf_hub_download(FINNISH_REPO, FINNISH_REF_WAV)
    print(f"[tts] ref wav: {ref_wav_path}", flush=True)

    print(f"[tts] fetching Finnish T3 finetune from {FINNISH_REPO}...",
          flush=True)
    fi_ckpt_path = hf_hub_download(FINNISH_REPO, FINNISH_T3_FILE)
    sd = load_file(fi_ckpt_path)
    sd = {k[3:] if k.startswith("t3.") else k: v for k, v in sd.items()}
    missing, unexpected = engine.t3.load_state_dict(sd, strict=False)
    print(
        f"[tts] Finnish T3 loaded (missing={len(missing)}, "
        f"unexpected={len(unexpected)})",
        flush=True,
    )
    if voice_pack_dir is not None:
        _apply_lora_adapter(engine, voice_pack_dir)
    return engine, ref_wav_path


def _make_vad():
    """Return (vad_model, get_speech_timestamps) or (None, None)."""
    try:
        from silero_vad import load_silero_vad, get_speech_timestamps
        return load_silero_vad(), get_speech_timestamps
    except Exception as exc:
        print(f"[warn] silero-vad unavailable ({exc}); using dB fallback",
              flush=True)
        return None, None


def _vad_trim(seg, vad_model, get_speech_timestamps):
    """Silero-VAD loose-Finnish tail/head trim. seg: pydub AudioSegment."""
    import torch
    import torchaudio
    from pydub.silence import detect_leading_silence
    if vad_model is None:
        lead = detect_leading_silence(
            seg, silence_threshold=VAD_FALLBACK_HEAD_DB,
        )
        trail = detect_leading_silence(
            seg.reverse(), silence_threshold=VAD_FALLBACK_TRAIL_DB,
        )
        start = max(0, lead - VAD_FALLBACK_HEAD_KEEP_MS)
        end = len(seg) - max(0, trail - VAD_FALLBACK_TRAIL_KEEP_MS)
        return seg[start:end] if end > start else seg

    samples = torch.tensor(seg.get_array_of_samples(),
                           dtype=torch.float32) / 32768.0
    if seg.channels > 1:
        samples = samples.view(-1, seg.channels).mean(dim=1)
    wav16 = torchaudio.functional.resample(samples, seg.frame_rate, 16000)
    ts = get_speech_timestamps(
        wav16, vad_model, sampling_rate=16000,
        threshold=VAD_SPEECH_THRESHOLD,
        min_silence_duration_ms=VAD_MIN_SILENCE_MS,
    )
    if not ts:
        return seg
    first_start_ms = max(0, int(ts[0]["start"] * 1000 / 16000) - VAD_HEAD_PAD_MS)
    last_end_ms = min(
        len(seg),
        int(ts[-1]["end"] * 1000 / 16000) + VAD_TAIL_PAD_MS,
    )
    if last_end_ms <= first_start_ms:
        return seg
    return seg[first_start_ms:last_end_ms]


def _postprocess(seg):
    """7 kHz low-pass + loudness normalize to TARGET_DBFS."""
    seg = seg.low_pass_filter(LOWPASS_HZ)
    delta = TARGET_DBFS - seg.dBFS
    if seg.dBFS != float("-inf"):
        seg = seg.apply_gain(delta)
    return seg


def _write_progress(progress_path: Path, state: dict) -> None:
    tmp = progress_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(progress_path)


class _StopRequested(Exception):
    pass


def main() -> int:
    args = parse_args()

    # --dry-run path must not import torch/chatterbox.
    if args.dry_run:
        return _dry_run(args)

    # Lazy torch/chatterbox imports with friendly error.
    try:
        import torch  # noqa: F401
        import torchaudio  # noqa: F401
        import chatterbox  # noqa: F401
    except ImportError as exc:
        print(f"[error] {exc}", flush=True)
        print(SETUP_INSTRUCTIONS, flush=True)
        return 2

    from pydub import AudioSegment

    if not args.pdf and not args.text_file and not args.epub:
        print("[error] one of --pdf, --epub, or --text-file is required",
              flush=True)
        return 2

    # "source_path" is the unified reference written into progress.json,
    # regardless of input mode. Keeps downstream code simple.
    if args.text_file:
        # Plain text input — create a single-chapter book structure.
        text_path = Path(args.text_file).expanduser().resolve()
        if not text_path.is_file():
            print(f"[error] text file not found: {text_path}", flush=True)
            return 2
        from types import SimpleNamespace
        content = text_path.read_text(encoding="utf-8")
        chapter = SimpleNamespace(
            index=0, title="Text", content=content,
            page_start=0, page_end=0,
        )
        book = SimpleNamespace(
            chapters=[chapter],
            metadata=SimpleNamespace(title=text_path.stem),
        )
        input_stem = text_path.stem
        source_path = text_path
        print(f"[setup] text file: {text_path.name} ({len(content)} chars)", flush=True)
    elif args.epub:
        from src.epub_parser import parse_epub
        epub_path = Path(args.epub).expanduser().resolve()
        if not epub_path.is_file():
            print(f"[error] EPUB not found: {epub_path}", flush=True)
            return 2
        book = parse_epub(str(epub_path))
        input_stem = epub_path.stem
        source_path = epub_path
        print(f"[setup] parsing EPUB: {epub_path.name}", flush=True)
    else:
        from src.pdf_parser import parse_pdf
        pdf_path = Path(args.pdf).expanduser().resolve()
        if not pdf_path.is_file():
            print(f"[error] PDF not found: {pdf_path}", flush=True)
            return 2
        book = parse_pdf(str(pdf_path))
        input_stem = pdf_path.stem
        source_path = pdf_path
        print(f"[setup] parsing PDF: {pdf_path.name}", flush=True)

    out_root = Path(args.out).expanduser().resolve() / input_stem
    chunks_dir = out_root / ".chunks"
    progress_path = out_root / ".progress.json"
    # Per-chunk observability log — one JSON object per line. Never
    # cleared by --no-resume, so long-run drift across restarts is still
    # visible. See _append_chunk_stats.
    chunk_stats_path = out_root / ".chunk_stats.jsonl"

    if not args.resume and chunks_dir.exists():
        print(f"[setup] --no-resume: wiping {chunks_dir}", flush=True)
        for p in chunks_dir.glob("*"):
            try:
                p.unlink()
            except OSError:
                pass
    out_root.mkdir(parents=True, exist_ok=True)
    chunks_dir.mkdir(parents=True, exist_ok=True)

    print(f"[setup] out={out_root}", flush=True)
    only = None
    if args.chapters:
        only = {int(x) for x in args.chapters.split(",") if x.strip()}
    if args.text_file:
        # Text-file input: skip prose heuristics — the user explicitly
        # wants this text synthesized regardless of length.
        selected = list(enumerate(book.chapters, start=1))
    else:
        selected = _select_chapters(book, only)
    if not selected:
        print("[error] no chapters passed skip heuristics; nothing to do",
              flush=True)
        return 1
    print(f"[setup] {len(selected)} chapters selected", flush=True)

    # Pre-compute chunks for each selected chapter (deterministic, cheap).
    plan = []  # [(pos, chapter, chunks)]
    total_chunks = 0
    for pos, ch in selected:
        chunks = _prepare_chapter_chunks(ch, args.chunk_chars,
                                         args.chunks_per_chapter,
                                         language=args.language)
        if not chunks:
            continue
        plan.append((pos, ch, chunks))
        total_chunks += len(chunks)
    print(f"[setup] total chunks to synthesize: {total_chunks}", flush=True)

    device = _resolve_device(args.device)
    print(f"[setup] device={device}", flush=True)

    # Ctrl-C handling: finish the current chunk, then exit cleanly.
    stop_flag = {"stop": False}

    def _on_sigint(signum, frame):
        if stop_flag["stop"]:
            print("[signal] second Ctrl-C; exiting immediately", flush=True)
            sys.exit(130)
        stop_flag["stop"] = True
        print("[signal] Ctrl-C received; finishing current chunk then "
              "saving progress...", flush=True)

    signal.signal(signal.SIGINT, _on_sigint)

    voice_pack_dir = Path(args.voice_pack) if args.voice_pack else None
    engine, ref_wav_path = _load_engine(device, args.ref_audio,
                                         language=args.language,
                                         voice_pack_dir=voice_pack_dir)
    vad_model, get_speech_timestamps = _make_vad()

    wall_start = time.time()
    total_done = 0
    # Count already-cached chunks to keep RTF/ETA honest across restarts.
    cached_done = 0
    for pos, ch, chunks in plan:
        for chi in range(len(chunks)):
            cache_path = chunks_dir / f"ch{pos:02d}_chunk{chi:04d}.wav"
            if cache_path.exists():
                cached_done += 1
    total_done = cached_done
    print(f"[setup] cached chunks found: {cached_done}/{total_chunks}",
          flush=True)

    completed_chapters: list[dict] = []
    chapter_mp3_paths: list[Path] = []

    synth_wall_s = 0.0
    synth_audio_s = 0.0

    try:
        for ci_pos, (pos, ch, chunks) in enumerate(plan, start=1):
            safe = _safe_title(ch.title or f"chapter_{ch.index}")
            chapter_mp3 = out_root / f"{pos:02d}_{safe}.mp3"
            chapter_mp3_paths.append(chapter_mp3)

            print(
                f"[chapter {ci_pos}/{len(plan)}] idx={ch.index} "
                f"title={ch.title[:60]!r} chunks={len(chunks)}",
                flush=True,
            )

            for chi, chunk_text in enumerate(chunks):
                if stop_flag["stop"]:
                    raise _StopRequested()

                cache_path = chunks_dir / f"ch{pos:02d}_chunk{chi:04d}.wav"
                if cache_path.exists():
                    continue

                _clear_chatterbox_state(engine)
                t0 = time.time()
                wav = engine.generate(
                    chunk_text,
                    language_id=args.language,
                    audio_prompt_path=ref_wav_path,
                    repetition_penalty=FI_REPETITION_PENALTY,
                    temperature=FI_TEMPERATURE,
                    exaggeration=FI_EXAGGERATION,
                    cfg_weight=FI_CFG_WEIGHT,
                )
                dt = time.time() - t0
                audio_s = wav.shape[-1] / engine.sr

                # Early-stop guard: T3's alignment analyzer + EOS sampler
                # can truncate synthesis mid-sentence. Detect via
                # audio_s/char ratio; retry with fresh state to re-roll
                # the stochastic trajectory. Keep the longest result so
                # we never regress.
                chunk_chars = len(chunk_text)
                retries_used = 0
                if chunk_chars >= MIN_AUDIO_RETRY_CHAR_FLOOR:
                    best_wav, best_audio_s, best_dt = wav, audio_s, dt
                    for attempt in range(1, MIN_AUDIO_MAX_RETRIES + 1):
                        ratio = best_audio_s / chunk_chars
                        if ratio >= MIN_AUDIO_S_PER_CHAR:
                            break
                        print(
                            f"[retry {attempt}/{MIN_AUDIO_MAX_RETRIES}] "
                            f"ch{pos:02d} chunk{chi:04d}: "
                            f"audio_s={best_audio_s:.2f} "
                            f"s_per_char={ratio:.4f} < "
                            f"{MIN_AUDIO_S_PER_CHAR} (early-stop suspected)",
                            flush=True,
                        )
                        _clear_chatterbox_state(engine)
                        t0r = time.time()
                        wav_r = engine.generate(
                            chunk_text,
                            language_id=args.language,
                            audio_prompt_path=ref_wav_path,
                            repetition_penalty=FI_REPETITION_PENALTY,
                            temperature=FI_TEMPERATURE,
                            exaggeration=FI_EXAGGERATION,
                            cfg_weight=FI_CFG_WEIGHT,
                        )
                        dt_r = time.time() - t0r
                        audio_s_r = wav_r.shape[-1] / engine.sr
                        retries_used = attempt
                        # always charge the wall-clock
                        dt += dt_r
                        if audio_s_r > best_audio_s:
                            best_wav, best_audio_s, best_dt = wav_r, audio_s_r, dt_r
                    wav, audio_s = best_wav, best_audio_s

                synth_wall_s += dt
                synth_audio_s += audio_s
                total_done += 1

                # Per-chunk observability. If any metric trends monotonically
                # across a long run (e.g. shrinking audio_s_per_char, growing
                # gpu_reserved_mb, or hook_count creeping above a small
                # constant), we have a state leak and this log is the evidence.
                _append_chunk_stats(chunk_stats_path, {
                    "ts": time.time(),
                    "chapter_pos": pos,
                    "chapter_chi": chi,
                    "global_chunk_idx": total_done,
                    "input_chars": len(chunk_text),
                    "audio_s": round(audio_s, 3),
                    "synth_s": round(dt, 3),
                    "rtf": round(dt / audio_s, 3) if audio_s > 0 else None,
                    "s_per_char": round(audio_s / max(1, len(chunk_text)), 4),
                    "retries_used": retries_used,
                    "hook_count": _chatterbox_hook_count(engine),
                    **_gpu_mem_stats_mb(),
                })

                import torchaudio as ta
                ta.save(str(cache_path), wav, engine.sr)

                # ETA/RTF reporting based on THIS session's synthesis only
                # (cached chunks aren't timed).
                rtf = dt / audio_s if audio_s > 0 else float("inf")
                elapsed = time.time() - wall_start
                remaining_chunks = total_chunks - total_done
                if synth_wall_s > 0 and (total_done - cached_done) > 0:
                    avg = synth_wall_s / (total_done - cached_done)
                    eta = avg * remaining_chunks
                else:
                    eta = 0
                print(
                    f"[chapter {ci_pos}/{len(plan)}] "
                    f"chunk {chi + 1}/{len(chunks)} "
                    f"({total_done}/{total_chunks} total) - "
                    f"{_format_hms(elapsed)} elapsed, "
                    f"~{_format_hms(eta)} remaining, "
                    f"RTF {rtf:.2f}x",
                    flush=True,
                )

            # Concat + trim + postprocess this chapter.
            if stop_flag["stop"]:
                raise _StopRequested()
            print(f"[chapter {ci_pos}/{len(plan)}] assembling MP3...",
                  flush=True)
            combined = AudioSegment.empty()
            gap = AudioSegment.silent(duration=100)  # 100ms inter-chunk
            for chi in range(len(chunks)):
                cache_path = chunks_dir / f"ch{pos:02d}_chunk{chi:04d}.wav"
                seg = AudioSegment.from_file(str(cache_path))
                seg = _vad_trim(seg, vad_model, get_speech_timestamps)
                combined += seg
                if chi < len(chunks) - 1:
                    combined += gap
            combined = _postprocess(combined)
            combined.export(str(chapter_mp3), format="mp3", bitrate="128k")
            print(f"[chapter {ci_pos}/{len(plan)}] wrote {chapter_mp3.name} "
                  f"({len(combined) / 1000.0:.1f}s)", flush=True)

            completed_chapters.append({
                "pos": pos,
                "source_index": ch.index,
                "title": ch.title,
                "mp3": str(chapter_mp3.relative_to(out_root)),
                "chunks": len(chunks),
            })
            _write_progress(progress_path, {
                "source": str(source_path),
                "completed_chapters": completed_chapters,
                "total_chapters": len(plan),
                "total_chunks_done": total_done,
                "total_chunks": total_chunks,
                "elapsed_s": time.time() - wall_start,
                "eta_s": (
                    (synth_wall_s / max(1, (total_done - cached_done)))
                    * (total_chunks - total_done)
                ) if (total_done - cached_done) > 0 else 0,
            })

    except _StopRequested:
        print("[signal] saved partial progress; exit 0", flush=True)
        _write_progress(progress_path, {
            "source": str(source_path),
            "completed_chapters": completed_chapters,
            "total_chapters": len(plan),
            "total_chunks_done": total_done,
            "total_chunks": total_chunks,
            "elapsed_s": time.time() - wall_start,
            "interrupted": True,
        })
        return 0

    # Full-book concatenation.
    if len(chapter_mp3_paths) > 1:
        print(f"[full] concatenating {len(chapter_mp3_paths)} chapters", flush=True)
        full = AudioSegment.empty()
        gap = AudioSegment.silent(duration=INTER_CHAPTER_SILENCE_MS)
        for i, p in enumerate(chapter_mp3_paths):
            if not p.exists():
                print(f"[full] skipping missing {p.name}", flush=True)
                continue
            full += AudioSegment.from_file(str(p))
            if i < len(chapter_mp3_paths) - 1:
                full += gap
        full = _postprocess(full)
        full_path = out_root / "00_full.mp3"
        full.export(str(full_path), format="mp3", bitrate="128k")
        print(f"[full] wrote {full_path} ({len(full) / 1000.0:.1f}s)",
              flush=True)

    print(f"[done] {total_done}/{total_chunks} chunks, "
          f"{_format_hms(time.time() - wall_start)} wall-clock", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
