"""One-time conversion: Murre OpenNMT-py 2 checkpoint → CTranslate2 model.

Murre (https://github.com/mikahama/murre) is a puhekieli → kirjakieli
neural normalizer trained with OpenNMT-py 2.x. Its distributed
checkpoint pickles ``torchtext.legacy.data.Field`` objects in its
vocabulary, which means loading it requires:

* PyTorch built against an old C++ ABI (torch < 2.0)
* torchtext < 0.15 (the last release that still shipped ``Field``)
* Python ≤ 3.10 (the last release with prebuilt wheels for that combo)

Modern Windows + Python 3.11 cannot satisfy that chain — the wheels
do not exist. This script is therefore a **one-time conversion** you
run inside Docker or WSL with a Python 3.9 environment. It produces a
CTranslate2 model directory which the inference wrapper at
``src/murre_normalize.py`` can load with only modern dependencies
(``ctranslate2`` has Windows wheels for every Python version we care
about, no torchtext, no torch needed at runtime).

After conversion you commit nothing — the CTranslate2 model lives at
``.local/murre_models_ct2/`` which is gitignored. Distribute it the
same way you distribute the original ``.pt``: out of band, optional.

──────────────────────────────────────────────────────────────────────
Setup option A — WSL (recommended on Windows 11)
──────────────────────────────────────────────────────────────────────

    wsl -d Ubuntu
    sudo apt-get update && sudo apt-get install -y python3.9 python3.9-venv
    python3.9 -m venv ~/.venvs/murre-convert
    source ~/.venvs/murre-convert/bin/activate
    pip install "torch==1.13.1" "torchtext==0.14.1" "ctranslate2>=4.0,<5"
    cd /mnt/d/koodaamista/AudiobookMaker
    python scripts/convert_murre_model.py

──────────────────────────────────────────────────────────────────────
Setup option B — Docker (no WSL)
──────────────────────────────────────────────────────────────────────

    docker run --rm -it -v "%cd%":/work -w /work python:3.9-slim bash
    pip install "torch==1.13.1" "torchtext==0.14.1" "ctranslate2>=4.0,<5"
    python scripts/convert_murre_model.py

──────────────────────────────────────────────────────────────────────

The converter writes ``model.bin``, ``config.json``, and the source/
target vocabularies into the output directory. The vocabularies are
also copied out as plain text (one token per line) so the inference
wrapper can do the simple whitespace tokenization Murre expects
without re-importing torchtext at runtime.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / ".local" / "murre_models" / "murre_norm_default.pt"
DEFAULT_OUTPUT = REPO_ROOT / ".local" / "murre_models_ct2"


def convert(input_pt: Path, output_dir: Path, *, force: bool = False) -> None:
    """Convert one OpenNMT-py 2.x checkpoint to a CTranslate2 model dir.

    Raises:
        FileNotFoundError: if ``input_pt`` is missing.
        ImportError: if ``ctranslate2`` is not installed in the current
            environment (must be the WSL/Docker conversion env).
        RuntimeError: if torch.load can't unpickle the checkpoint
            (usually means torchtext < 0.15 is not installed).
    """
    if not input_pt.exists():
        raise FileNotFoundError(
            f"Murre checkpoint not found at {input_pt}. Download it first "
            "from https://github.com/mikahama/murre/raw/master/murre/models/"
            "murre_norm_default.pt and place it at the expected path."
        )

    try:
        from ctranslate2.converters import OpenNMTPyConverter
    except ImportError as exc:
        raise ImportError(
            "ctranslate2 is not installed in the current environment. "
            "Run this script from the conversion venv described in the "
            "module docstring (Python 3.9 + torch 1.13 + torchtext 0.14 + "
            "ctranslate2)."
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[convert] loading {input_pt} ...")
    converter = OpenNMTPyConverter(str(input_pt))
    print(f"[convert] writing CTranslate2 model to {output_dir} ...")
    converter.convert(str(output_dir), force=force)

    # Extract the source/target vocabularies as plain text so the
    # runtime wrapper does not need torchtext to tokenize. The
    # OpenNMT-py 2 checkpoint stores vocab under checkpoint["vocab"]
    # as a list of (field_name, Vocab) pairs. We re-load the .pt with
    # torch (not via the ct2 converter) and extract the itos lists.
    try:
        import torch  # noqa: F401 — only needed inside the convert env
    except ImportError as exc:
        raise ImportError(
            "torch is required to extract vocab files (load the .pt). "
            "Install torch==1.13.1 in the conversion environment."
        ) from exc

    print("[convert] extracting vocab token lists ...")
    checkpoint = torch.load(str(input_pt), map_location="cpu")
    vocab = checkpoint.get("vocab")
    if vocab is None:
        print("[convert] WARNING: checkpoint has no 'vocab' field; skipping "
              "vocab extraction. Inference will still work but may not "
              "round-trip unknown tokens cleanly.")
        return

    # vocab is typically [("src", Vocab), ("tgt", Vocab)] or a dict.
    fields = dict(vocab) if isinstance(vocab, list) else vocab
    for name, field_vocab in fields.items():
        # field_vocab is either a torchtext Vocab (has .itos) or a Field
        # (whose .vocab is the Vocab). Handle both.
        itos = getattr(field_vocab, "itos", None) or getattr(
            getattr(field_vocab, "vocab", None), "itos", None
        )
        if itos is None:
            print(f"[convert] WARNING: could not extract itos for field "
                  f"{name!r}; skipping.")
            continue
        out_path = output_dir / f"vocab.{name}.txt"
        out_path.write_text("\n".join(itos), encoding="utf-8")
        print(f"[convert]   wrote {out_path} ({len(itos)} tokens)")

    # Drop a small marker file so the runtime wrapper has a single
    # "is the conversion complete" check.
    marker = output_dir / "murre_ct2.json"
    marker.write_text(
        json.dumps(
            {
                "source_checkpoint": input_pt.name,
                "format": "ctranslate2",
                "tokenizer": "whitespace+chunk3",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[convert] done. Marker written to {marker}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a Murre OpenNMT-py 2.x checkpoint to CTranslate2.",
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                        help=f"path to .pt checkpoint (default: {DEFAULT_INPUT})")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"output dir (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--force", action="store_true",
                        help="overwrite existing output dir")
    args = parser.parse_args()

    try:
        convert(args.input, args.output, force=args.force)
    except (FileNotFoundError, ImportError, RuntimeError) as exc:
        print(f"[convert] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
