# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for AudiobookMaker
# Build with: pyinstaller audiobookmaker.spec

import os
from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

block_cipher = None

# collect_all() returns (datas, binaries, hiddenimports) for a package
# and grabs EVERYTHING — source .py files, native libs, data files, and
# submodule names. Use this for packages where piecewise collection has
# repeatedly missed critical pieces (e.g. onnxruntime.capi is needed for
# InferenceSession but wasn't being bundled).
_all_onnx = collect_all('onnxruntime')
_all_piper = collect_all('piper')
_all_pathvalidate = collect_all('pathvalidate')  # required by piper-tts

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
    # (Full submodule trees come from collect_all() below.)
    'piper',
    'onnxruntime',
    'numpy',
    'pathvalidate',
    # Finnish text normalizer
    'num2words',
    # Unified GUI extras
    'tkinter.scrolledtext',
    'customtkinter',
    'darkdetect',
]

hidden_imports += collect_submodules('edge_tts')
hidden_imports += collect_submodules('aiohttp')
hidden_imports += collect_submodules('customtkinter')
# Piper + onnxruntime + pathvalidate: use collect_all() to grab source
# .py files too. collect_submodules alone only adds names.
hidden_imports += _all_onnx[2]
hidden_imports += _all_piper[2]
hidden_imports += _all_pathvalidate[2]

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

binaries = _all_onnx[1] + _all_piper[1] + _all_pathvalidate[1]
# collect_all('piper') already collects espeakbridge.pyd under piper/.
# Do NOT also add it via collect_dynamic_libs — that bundles a second copy
# at the top level, and Python refuses to load the same native module twice
# ("cannot load module more than once per process").
hidden_imports += ['piper.espeakbridge', 'piper.voice', 'piper.config',
                   'piper.phonemize_espeak', 'piper.phoneme_ids', 'piper.const']

# Bundle ffmpeg.exe, ffprobe.exe, and ffplay.exe from dist/ffmpeg/ into the
# package root so pydub can find them at runtime (see src/ffmpeg_path.py).
# ffprobe is required by pydub to read audio file metadata (mediainfo_json).
# ffplay is used by the Listen button.
datas = [
    (os.path.join('dist', 'ffmpeg', 'ffmpeg.exe'), '.'),
    (os.path.join('dist', 'ffmpeg', 'ffprobe.exe'), '.'),
    (os.path.join('dist', 'ffmpeg', 'ffplay.exe'), '.'),
]
# Pull piper/onnxruntime/pathvalidate data (includes espeak-ng-data/,
# onnxruntime config files, etc.) from collect_all().
datas += _all_onnx[0]
datas += _all_piper[0]
datas += _all_pathvalidate[0]
# edge_tts package data
datas += collect_data_files('edge_tts')
# customtkinter assets (themes, icons)
datas += collect_data_files('customtkinter')
# Finnish loanword lexicon used by the text normalizer
datas += [(os.path.join('data', 'fi_loanwords.yaml'), 'data')]
# Chatterbox runner script — invoked as a subprocess by the unified GUI
datas += [(os.path.join('scripts', 'generate_chatterbox_audiobook.py'), 'scripts')]
# Bundle src modules needed by the Chatterbox subprocess script.
# The script is run by the chatterbox venv's Python (not the bundled
# interpreter) and does `from src.tts_engine import ...`. We bundle
# the .py files so the script can sys.path.insert(_internal) and import them.
datas += [
    (os.path.join('src', '__init__.py'), 'src'),
    (os.path.join('src', 'tts_engine.py'), 'src'),
    (os.path.join('src', 'tts_normalizer.py'), 'src'),
    (os.path.join('src', 'tts_normalizer_fi.py'), 'src'),
    (os.path.join('src', 'tts_normalizer_en.py'), 'src'),
    (os.path.join('src', '_en_pass_o_dates.py'), 'src'),
    (os.path.join('src', '_en_pass_p_telephone.py'), 'src'),
    (os.path.join('src', '_en_pass_r_urls.py'), 'src'),
    (os.path.join('src', '_en_pass_s_acronyms.py'), 'src'),
    (os.path.join('src', 'tts_chunking.py'), 'src'),
    (os.path.join('src', 'tts_audio.py'), 'src'),
    (os.path.join('src', 'pdf_parser.py'), 'src'),
    (os.path.join('src', 'fi_loanwords.py'), 'src'),
]
# Goat icon for the window title bar and taskbar
datas += [(os.path.join('assets', 'icon.ico'), 'assets')]
datas += [(os.path.join('assets', 'icon.png'), 'assets')]
# Grandmom voice reference WAV — used by the Chatterbox subprocess when
# synthesizing English via the multilingual base model + voice cloning.
# See memory/project_english_grandmom.md for the recipe.
datas += [(os.path.join('assets', 'voices', 'grandmom_reference.wav'),
           os.path.join('assets', 'voices'))]
# Pre-baked Grandmom English voice sample played by the Test-voice button
# on the Chatterbox engine, where on-demand synthesis is too slow to give
# the user instant feedback.
datas += [(os.path.join('assets', 'voices', 'grandmom_en_sample.mp3'),
           os.path.join('assets', 'voices'))]

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

# Splash shown by the PyInstaller bootloader the moment the .exe starts —
# covers the 3-5 s unpack + Python import delay so the user sees the goat
# icon immediately rather than wondering if the app crashed. Closed from
# src/main.py once the Tk window is visible via pyi_splash.close().
splash = Splash(
    os.path.join('assets', 'icon.png'),
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,          # No status text — the icon alone is cleaner.
    text_size=12,
    text_color='black',
)

exe = EXE(
    pyz,
    splash,
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
    splash.binaries,
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
