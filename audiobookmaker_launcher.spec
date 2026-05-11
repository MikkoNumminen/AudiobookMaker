# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the simple Launcher entry point.
#
# This is a SECOND installer target distinct from ``audiobookmaker.spec``:
#   * audiobookmaker.spec  freezes src/main.py -> src.gui.run()
#     (advanced mode — full engine/voice/rate settings window)
#   * audiobookmaker_launcher.spec  freezes src/launcher.py
#     (simple mode — "pick PDF, click button, get MP3")
#
# Both specs share the same underlying ``src.tts_*`` engines. The launcher
# build excludes torch and chatterbox-tts entirely — the Chatterbox engine
# is wired through a subprocess that talks to a separate ``.venv-chatterbox``
# installed post-install by ``installer/post_install_chatterbox.py``. See
# ``installer/launcher.iss`` for the Inno Setup wizard that ties it together.
#
# Build with:
#   pyinstaller audiobookmaker_launcher.spec
#
# Output:
#   dist/AudiobookMakerLauncher/AudiobookMakerLauncher.exe  (windowed)
#   dist/AudiobookMakerLauncher/*.dll, etc.                 (bundled deps)
#
# Smoke test (post-build) on a Windows runner:
#   dist\AudiobookMakerLauncher\AudiobookMakerLauncher.exe --self-test

import glob
import os
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

block_cipher = None

# Hidden imports — same set as the advanced-mode spec minus anything
# launcher.py does not touch directly.
hidden_imports = [
    "edge_tts",
    "pydub",
    "fitz",
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    "tkinter.filedialog",
    "tkinter.scrolledtext",
    "asyncio",
    "aiohttp",
    "aiohttp.resolver",
    "aiohttp.connector",
    "certifi",
    "piper",
    "onnxruntime",
    "numpy",
    # num2words is used by src/tts_engine.normalize_finnish_text for
    # pre-TTS Finnish number expansion.
    "num2words",
    "num2words.lang_EU",
    "num2words.lang_FI",
    # Launcher + bridge live under src/.
    "src.launcher",
    "src.launcher_bridge",
    "src.tts_base",
    "src.engine_registry",
    "src.tts_edge",
    "src.tts_piper",
    "src.tts_chatterbox_bridge",
    "src.tts_engine",
    "src.tts_normalizer",
    "src.tts_normalizer_fi",
    "src.tts_normalizer_en",
    "src._en_pass_o_dates",
    "src._en_pass_p_telephone",
    "src._en_pass_r_urls",
    "src._en_pass_s_acronyms",
    "src._yaml_data",
    "src.tts_chunking",
    "src.tts_audio",
    "src.pdf_parser",
    "src.app_config",
    "src.ffmpeg_path",
]

hidden_imports += collect_submodules("edge_tts")
hidden_imports += collect_submodules("aiohttp")
hidden_imports += collect_submodules("piper")
hidden_imports += collect_submodules("onnxruntime")
hidden_imports += collect_submodules("num2words")

# Things we deliberately do NOT want pulled into the frozen launcher.
# Torch + chatterbox-tts + transformers live ONLY in the .venv-chatterbox
# venv that post_install_chatterbox.py creates at install time. Keeping
# them out here is what keeps the launcher .exe under ~150 MB.
#
# Defense-in-depth: most of these aren't installed by the launcher's CI
# build env, so they wouldn't reach the bundle today. The exclude list
# documents the contract — "this launcher never carries an ML stack" —
# so a future contributor who pip-installs torch on the build machine
# can't silently double the installer.
excludes = [
    "torch",
    "torchaudio",
    "torchvision",
    "transformers",
    "chatterbox",
    "chatterbox_tts",
    "voxcpm",
    "pyannote",
    "pyannote.audio",
    "pyannote.core",
    "pyannote.metrics",
    "silero_vad",
    "safetensors",
    "accelerate",
    "bitsandbytes",
    "peft",
    "ctranslate2",
    "faster_whisper",
    "whisper",
    "speechbrain",
    "soundfile",
    "librosa",
    "sklearn",
    "scikit_learn",
    "huggingface_hub",
    # Standard heavy deps the main spec already excludes.
    "matplotlib",
    "scipy",
    "PIL",
    "cv2",
    "pandas",
    "IPython",
    "notebook",
    "sphinx",
    "docutils",
    # Test machinery — never reached at runtime.
    "pytest",
    "pytest_asyncio",
    "pytest_cov",
    "pytest_timeout",
    "_pytest",
    "coverage",
    # System-Python pollution from dev tooling (pip_audit / cyclonedx /
    # rich / markdown_it / etc.). Not reachable from the launcher.
    "cyclonedx",
    "cyclonedx_python_lib",
    "pip_audit",
    "pip_api",
    "pip_requirements_parser",
    "CacheControl",
    "msgpack",
    "rich",
    "markdown_it",
    "mdurl",
    "Pygments",
    "boolean",
    "docopt",
    "license_expression",
    "tabulate",
    "imageio_ffmpeg",
    "psutil",
    # Jupyter / notebook stack (transitive risk via dev tooling).
    "jupyter",
    "jupyter_client",
    "jupyter_core",
    "jupyterlab",
    "ipykernel",
    "ipywidgets",
    "tornado",
    "zmq",
]

binaries = collect_dynamic_libs("onnxruntime")

datas = []
# Bundle ffmpeg the same way the advanced-mode spec does. pydub finds it
# via src/ffmpeg_path.py's setup_ffmpeg_path() helper at runtime.
ffmpeg_src = os.path.join("dist", "ffmpeg", "ffmpeg.exe")
if os.path.exists(ffmpeg_src):
    datas.append((ffmpeg_src, "."))

# piper ships its phonemizer data; same as the advanced-mode spec.
datas += collect_data_files("piper")
datas += collect_data_files("onnxruntime")

# YAML lexicons used by the text normalizers (abbreviations, acronyms,
# governors, unit tables, loanword respellings). Non-developers curate
# these tables; the Python modules load them lazily from data/.
for _yaml in glob.glob(os.path.join("data", "*.yaml")):
    datas.append((_yaml, "data"))

# Bundle the Finnish quickstart doc so the launcher's "Ohje" link can
# open it locally even if the user is offline.
for doc in ("docs/turo_ohjeet_fi.md", "docs/audiobook_quality_rubric.md"):
    if os.path.exists(doc):
        datas.append((doc, "docs"))

a = Analysis(
    [os.path.join("src", "launcher.py")],
    pathex=[os.path.abspath(".")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ── Bundle trimming: drop subtrees we don't use ──────────────────────────
# piper ships a 4.6 MB Arabic diacritization model and training tools;
# onnxruntime ships transformer/quantization toolchains. The launcher uses
# only piper.voice.PiperVoice + onnxruntime.InferenceSession at runtime.
def _drop_path(item, *needles):
    """True if any needle matches the item's dest path (case-insensitive).

    Handles both path-style entries (``a.datas`` / ``a.binaries``) and
    module-name entries (``a.pure``) by checking the raw form and the
    dot-to-slash normalized form against path-style needles.
    """
    raw = item[0].lower().replace("\\", "/")
    as_path = raw.replace(".", "/")
    return any(n.lower() in raw or n.lower() in as_path for n in needles)


_unused_path_needles = (
    "piper/tashkeel/",          # Arabic diacritizer ONNX + scaler
    "piper/train/",             # training utilities
    "piper/phonemize_chinese",  # CN phonemizer (we ship en/fi voices)
    "piper/http_server",        # CLI HTTP server entry point
    "piper/__main__",           # python -m piper CLI dispatcher
    "piper/download_voices",    # voice-pack downloader
    "onnxruntime/transformers/",
    "onnxruntime/quantization/",
    "onnxruntime/tools/",
    "onnxruntime/backend/",
    "onnxruntime/datasets/",
)

a.datas = [d for d in a.datas if not _drop_path(d, *_unused_path_needles)]
a.pure = [p for p in a.pure if not _drop_path(p, *_unused_path_needles)]
a.binaries = [b for b in a.binaries if not _drop_path(b, *_unused_path_needles)]

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AudiobookMakerLauncher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # windowed app — no console on launch
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join("assets", "icon.ico"),
    version_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AudiobookMakerLauncher",
)

# Launcher version: 0.1.0 (pre-1.0, separate from main-app v1.0.x).
