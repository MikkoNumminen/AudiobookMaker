# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for AudiobookMaker
# Build with: pyinstaller audiobookmaker.spec

import os
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

block_cipher = None

# Collect all submodules for packages that need dynamic imports
hidden_imports = [
    'edge_tts',
    'pydub',
    'fitz',
    'tkinter',
    'tkinter.ttk',
    'tkinter.messagebox',
    'tkinter.filedialog',
    'asyncio',
    'asyncio.events',
    'asyncio.base_events',
    'aiohttp',
    'aiohttp.resolver',
    'aiohttp.connector',
    'aiohappyeyeballs',
    'certifi',
    'multidict',
    'yarl',
    'aiosignal',
    'frozenlist',
    # Piper offline TTS + its ONNX runtime backend
    'piper',
    'piper_phonemize',
    'onnxruntime',
    'numpy',
    # Finnish text normalizer
    'num2words',
    # Unified GUI extras
    'tkinter.scrolledtext',
    'customtkinter',
    'darkdetect',
]

hidden_imports += collect_submodules('edge_tts')
hidden_imports += collect_submodules('aiohttp')
hidden_imports += collect_submodules('piper')
hidden_imports += collect_submodules('onnxruntime')
hidden_imports += collect_submodules('piper_phonemize')
hidden_imports += collect_submodules('customtkinter')

# numpy is now REQUIRED at runtime (onnxruntime/piper need it), so it
# must NOT appear in excludes.
excludes = [
    'matplotlib',
    'scipy',
    'PIL',
    'cv2',
    'pandas',
    'IPython',
    'notebook',
    'sphinx',
    'docutils',
]

# Native shared libraries that onnxruntime ships with (e.g. DirectML and
# core DLLs on Windows). Piper also pulls in espeak-ng native libs, which
# collect_data_files('piper') picks up automatically below.
binaries = collect_dynamic_libs('onnxruntime')
binaries += collect_dynamic_libs('piper')
binaries += collect_dynamic_libs('piper_phonemize')

# Bundle ffmpeg.exe and ffplay.exe from dist/ffmpeg/ into the package root
# so pydub can find ffmpeg via PATH at runtime (see src/ffmpeg_path.py)
# and the Listen button can play audio via ffplay.
datas = [
    (os.path.join('dist', 'ffmpeg', 'ffmpeg.exe'), '.'),
    (os.path.join('dist', 'ffmpeg', 'ffplay.exe'), '.'),
]
# piper ships its phonemizer data (espeak-ng-data/) inside the package;
# PiperVoice.load() will fail at runtime without it.  collect_data_files
# walks the installed piper package and emits every non-Python file.
datas += collect_data_files('piper')
datas += collect_data_files('piper_phonemize')
# onnxruntime ships a few config/JSON files next to its native libs on
# some platforms; bundle them to be safe.
datas += collect_data_files('onnxruntime')
# edge_tts package data
datas += collect_data_files('edge_tts')
# customtkinter assets (themes, icons)
datas += collect_data_files('customtkinter')
# Finnish loanword lexicon used by the text normalizer
datas += [(os.path.join('data', 'fi_loanwords.yaml'), 'data')]
# Chatterbox runner script — invoked as a subprocess by the unified GUI
datas += [(os.path.join('scripts', 'generate_chatterbox_audiobook.py'), 'scripts')]

a = Analysis(
    [os.path.join('src', 'main.py')],
    pathex=[os.path.abspath('.')],
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
    name='AudiobookMaker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # No console window (windowed app)
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join('assets', 'icon.ico'),
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
    name='AudiobookMaker',
)

# ── Version metadata (Windows VERSIONINFO resource) ──────────────────────────
# PyInstaller reads this from the EXE block above via a version_file, but the
# simplest cross-compatible approach is to embed it directly in the spec using
# a VSVersionInfo object when building on Windows.  The block below is kept as
# a reference; to activate it replace `version_file=None` above with the path
# to a generated version file, or use the PyInstaller --version-file flag.
#
# App version: 1.0.0
