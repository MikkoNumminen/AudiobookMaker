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
    "src.tts_edge",
    "src.tts_piper",
    "src.tts_engine",
    "src.tts_normalizer",
    "src.tts_normalizer_fi",
    "src.tts_normalizer_en",
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
excludes = [
    "torch",
    "torchaudio",
    "transformers",
    "chatterbox",
    "silero_vad",
    "safetensors",
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
