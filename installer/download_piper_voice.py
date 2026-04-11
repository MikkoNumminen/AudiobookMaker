"""Download the Finnish Harri Piper voice into the installed app.

Invoked by the Launcher Inno Setup wizard when the ``engine_piper``
component is selected. Idempotent — re-running skips files that already
exist.

The URLs point at Rhasspy's CDN hosted on HuggingFace, which is where
``piper-tts`` fetches voices by default. We prefetch them so the first
synthesis does not pause for a 60 MB download.

Standalone usage::

    python installer/download_piper_voice.py --target "C:\\AudiobookMaker\\piper_voices"
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

VOICE_ID = "fi_FI-harri-medium"
BASE_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "fi/fi_FI/harri/medium/"
)

FILES = [
    f"{VOICE_ID}.onnx",
    f"{VOICE_ID}.onnx.json",
]


def download(url: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        print(f"  skip (exists): {dst.name}", flush=True)
        return
    print(f"  fetching {url}", flush=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()
    except urllib.error.URLError as exc:
        print(f"[error] download failed: {exc}", flush=True, file=sys.stderr)
        raise SystemExit(2)
    dst.write_bytes(data)
    print(f"  wrote {dst} ({len(data) / 1024 / 1024:.1f} MB)", flush=True)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Download the Finnish Piper Harri voice."
    )
    p.add_argument(
        "--target",
        type=Path,
        default=Path.home() / ".audiobookmaker" / "piper_voices" / VOICE_ID,
        help="Directory to place the .onnx and .onnx.json files into.",
    )
    args = p.parse_args()

    print(f"[step 1/1] Downloading Piper Harri voice to {args.target}",
          flush=True)
    for name in FILES:
        download(BASE_URL + name, args.target / name)
    print("[done] Piper voice installed.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
