# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the AudiobookMaker CLI.
# Build with: pyinstaller audiobookmaker_cli.spec
#
# Produces a console-mode `audiobookmaker.exe` plus bundled dependencies
# inside dist/audiobookmaker/. Drop that folder on PATH and run:
#   audiobookmaker convert book.pdf
#
# No GUI, no Tk, no CustomTkinter. Chatterbox synthesis runs via a separate
# .venv-chatterbox subprocess (same as the GUI) and is NOT bundled here.

# Version is read from src/auto_updater.py::APP_VERSION at build time.
# When cutting a release, the version-sync gate in build-release.yml
# verifies APP_VERSION matches the tag name. The release-cut workflow
# needs a follow-up to also bump pyproject.toml — tracked as future
# work.

import glob
import os
from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

block_cipher = None

# collect_all() grabs source .py, native libs, data files, and submodule
# names. Used for packages where piecewise collection has historically
# missed critical pieces (onnxruntime.capi, piper voice data).
_all_onnx = collect_all('onnxruntime')
_all_piper = collect_all('piper')
_all_pathvalidate = collect_all('pathvalidate')  # required by piper-tts

hidden_imports = [
    # Async HTTP (edge-tts dependency)
    'edge_tts',
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
    # Audio processing
    'pydub',
    # PDF parsing
    'fitz',
    # EPUB parsing
    'ebooklib',
    'bs4',
    'lxml',
    # Piper offline TTS + ONNX runtime backend
    'piper',
    'piper.espeakbridge',
    'piper.voice',
    'piper.config',
    'piper.phonemize_espeak',
    'piper.phoneme_ids',
    'piper.const',
    'onnxruntime',
    'numpy',
    'pathvalidate',
    # Finnish/English text normalizer
    'num2words',
    'num2words.lang_EU',
    'num2words.lang_FI',
    # PyYAML (YAML lexicon loader)
    'yaml',
    # ocrmypdf chain (PDF OCR fallback)
    'ocrmypdf',
    # CLI src modules
    'src.cli',
    'src.cli.__main__',
    'src.cli._common',
    'src.cli.convert',
    'src.cli.sample',
    'src.cli.preview',
    'src.cli.voices',
    'src.cli.engines',
    'src.cli.packs',
    'src.cli.config',
    'src.cli.update',
    'src.cli.doctor',
    # Engine and synthesis stack
    'src.engine_registry',
    'src.tts_edge',
    'src.tts_piper',
    'src.tts_chatterbox_bridge',
    'src.tts_base',
    'src.tts_engine',
    'src.tts_normalizer',
    'src.tts_normalizer_fi',
    'src.tts_normalizer_fi_legal',
    'src.tts_normalizer_en',
    'src._en_pass_o_dates',
    'src._en_pass_p_telephone',
    'src._en_pass_r_urls',
    'src._en_pass_s_acronyms',
    'src._yaml_data',
    'src.tts_chunking',
    'src.tts_audio',
    'src.pdf_parser',
    'src.ocr_path',
    'src.epub_parser',
    'src.docx_parser',
    'src.app_config',
    'src.auto_updater',
    'src.ffmpeg_path',
    'src.fi_loanwords',
    'src.engine_installer',
]

hidden_imports += collect_submodules('edge_tts')
hidden_imports += collect_submodules('aiohttp')
hidden_imports += collect_submodules('num2words')
# piper + onnxruntime + pathvalidate: collect_all() already provides their
# submodule names; don't also call collect_submodules() — duplicate entries
# are harmless but wasteful.
hidden_imports += _all_onnx[2]
hidden_imports += _all_piper[2]
hidden_imports += _all_pathvalidate[2]

# ── Defense-in-depth excludes ─────────────────────────────────────────────
#
# The CI build env (pip install -r requirements.txt) does not install torch,
# transformers, chatterbox, etc. — they live in the separate .venv-chatterbox
# post-install venv. These excludes document that contract and also guard
# against a developer doing a local build on a machine whose site-packages
# has those packages installed.
#
# No Tk / CustomTkinter here — this is a pure console binary.
excludes = [
    # GUI toolkits — CLI needs none of these
    'tkinter',
    'tkinter.ttk',
    'tkinter.messagebox',
    'tkinter.filedialog',
    'tkinter.scrolledtext',
    'customtkinter',
    'darkdetect',
    # Heavy data-science stack (handled by .venv-chatterbox at runtime)
    'torch',
    'torchaudio',
    'torchvision',
    'transformers',
    'chatterbox',
    'chatterbox_tts',
    'voxcpm',
    'pyannote',
    'silero_vad',
    'safetensors',
    'accelerate',
    'bitsandbytes',
    'peft',
    'ctranslate2',
    'faster_whisper',
    'whisper',
    'speechbrain',
    'soundfile',
    'librosa',
    'sklearn',
    'huggingface_hub',
    # Visualisation / data tools not reachable from the CLI
    'matplotlib',
    'scipy',
    'cv2',
    'pandas',
    'IPython',
    'notebook',
    'sphinx',
    'docutils',
    # Test machinery
    'pytest',
    'pytest_asyncio',
    'pytest_cov',
    'pytest_timeout',
    '_pytest',
    'coverage',
    # Dev tooling that pollutes site-packages on developer machines
    'cyclonedx',
    'cyclonedx_python_lib',
    'pip_audit',
    'pip_api',
    'pip_requirements_parser',
    'CacheControl',
    'msgpack',
    'rich',
    'markdown_it',
    'mdurl',
    'Pygments',
    'boolean',
    'docopt',
    'license_expression',
    'tabulate',
    'imageio_ffmpeg',
    'psutil',
    # Jupyter stack
    'jupyter',
    'jupyter_client',
    'jupyter_core',
    'jupyterlab',
    'ipykernel',
    'ipywidgets',
    'tornado',
    'zmq',
    # PIL is not needed by the CLI at all — exclude wholesale.
    # (The GUI spec keeps Bmp/Jpeg/Png/Ico for CTkImage; we have no CTkImage.)
    'PIL',
]

binaries = _all_onnx[1] + _all_piper[1] + _all_pathvalidate[1]

# ── Data files ────────────────────────────────────────────────────────────

datas = []

# Bundle ffmpeg.exe and ffprobe.exe so pydub can find them via
# src/ffmpeg_path.py::setup_ffmpeg_path() at runtime. ffplay is skipped
# (the CLI has no audio-playback feature; omitting it saves ~50 MB).
_ffmpeg_src = os.path.join('dist', 'ffmpeg', 'ffmpeg.exe')
_ffprobe_src = os.path.join('dist', 'ffmpeg', 'ffprobe.exe')
if os.path.exists(_ffmpeg_src):
    datas.append((_ffmpeg_src, '.'))
if os.path.exists(_ffprobe_src):
    datas.append((_ffprobe_src, '.'))

# OCR fallback toolchain: same conditional bundle as audiobookmaker.spec.
# CI populates dist/ocr/ before invoking PyInstaller; a local build without
# that step gracefully degrades to no-OCR mode (EmptyPDFError on scanned
# PDFs). See src/ocr_path.py.
_OCR_SRC = os.path.join('dist', 'ocr')
if os.path.isdir(_OCR_SRC):
    for _f in glob.glob(os.path.join(_OCR_SRC, '*.exe')):
        datas.append((_f, '.'))
    for _f in glob.glob(os.path.join(_OCR_SRC, '*.dll')):
        datas.append((_f, '.'))
    _tessdata = os.path.join(_OCR_SRC, 'tessdata')
    if os.path.isdir(_tessdata):
        for _td in glob.glob(os.path.join(_tessdata, '*.traineddata')):
            datas.append((_td, 'tessdata'))
        _tessconfigs = os.path.join(_tessdata, 'configs')
        if os.path.isdir(_tessconfigs):
            for _cfg in glob.glob(os.path.join(_tessconfigs, '*')):
                if os.path.isfile(_cfg):
                    datas.append((_cfg, os.path.join('tessdata', 'configs')))

# piper / onnxruntime / pathvalidate data (espeak-ng-data, onnxruntime
# config files, etc.) from collect_all().
datas += _all_onnx[0]
datas += _all_piper[0]
datas += _all_pathvalidate[0]

# edge_tts package data
datas += collect_data_files('edge_tts')

# YAML lexicons used by text normalizers
for _yaml in glob.glob(os.path.join('data', '*.yaml')):
    datas.append((_yaml, 'data'))

# Bundle src modules needed by the Chatterbox subprocess script so the
# script can sys.path.insert(_internal) and import them without a full
# Python environment. Same set as the GUI spec minus GUI-only files.
datas += [
    (os.path.join('src', '__init__.py'), 'src'),
    (os.path.join('src', 'tts_engine.py'), 'src'),
    (os.path.join('src', 'tts_normalizer.py'), 'src'),
    (os.path.join('src', 'tts_normalizer_fi.py'), 'src'),
    (os.path.join('src', 'tts_normalizer_fi_legal.py'), 'src'),
    (os.path.join('src', 'tts_normalizer_en.py'), 'src'),
    (os.path.join('src', 'tts_symbols.py'), 'src'),
    (os.path.join('src', '_en_pass_o_dates.py'), 'src'),
    (os.path.join('src', '_en_pass_p_telephone.py'), 'src'),
    (os.path.join('src', '_en_pass_r_urls.py'), 'src'),
    (os.path.join('src', '_en_pass_s_acronyms.py'), 'src'),
    (os.path.join('src', 'tts_chunking.py'), 'src'),
    (os.path.join('src', 'tts_audio.py'), 'src'),
    (os.path.join('src', 'pdf_parser.py'), 'src'),
    (os.path.join('src', 'ocr_path.py'), 'src'),
    (os.path.join('src', 'epub_parser.py'), 'src'),
    (os.path.join('src', 'docx_parser.py'), 'src'),
    (os.path.join('src', 'fi_loanwords.py'), 'src'),
    (os.path.join('src', 'ffmpeg_path.py'), 'src'),
    (os.path.join('src', '_yaml_data.py'), 'src'),
]

# Chatterbox runner script — invoked as a subprocess by the convert
# subcommand when --engine=chatterbox_fi is selected.
_chatterbox_script = os.path.join('scripts', 'generate_chatterbox_audiobook.py')
if os.path.exists(_chatterbox_script):
    datas.append((_chatterbox_script, 'scripts'))

# Grandmom voice reference WAV and pre-baked English sample — needed by
# the Chatterbox subprocess when --engine=chatterbox_fi --lang=en is used.
_grandmom_ref = os.path.join('assets', 'voices', 'grandmom_reference.wav')
_grandmom_en = os.path.join('assets', 'voices', 'grandmom_en_sample.mp3')
if os.path.exists(_grandmom_ref):
    datas.append((_grandmom_ref, os.path.join('assets', 'voices')))
if os.path.exists(_grandmom_en):
    datas.append((_grandmom_en, os.path.join('assets', 'voices')))

# ── Analysis ──────────────────────────────────────────────────────────────

a = Analysis(
    [os.path.join('src', 'cli', '__main__.py')],
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

# Drop ctranslate2's cudnn64_9.dll if it somehow sneaks in via a polluted
# build environment (belt-and-suspenders — ctranslate2 is already in excludes).
a.binaries = [
    b for b in a.binaries
    if not b[0].lower().replace('\\', '/').endswith('ctranslate2/cudnn64_9.dll')
]

# ── Bundle trimming: drop unused subtrees inside piper / onnxruntime ─────
#
# Same logic as audiobookmaker.spec. These trims target physical paths in
# a.datas / a.pure / a.binaries and reclaim space that the module-level
# excludes list cannot reach (they work at import-name granularity only).

def _drop_path(item, *needles):
    """True if any needle matches the item's dest path (case-insensitive).

    Handles both path-style entries (a.datas / a.binaries — e.g.
    ``"piper/tashkeel/foo.onnx"``) and module-name entries (a.pure —
    e.g. ``"piper.tashkeel.foo"``) by checking the raw form and the
    dot-to-slash-normalized form.
    """
    raw = item[0].lower().replace('\\', '/')
    as_path = raw.replace('.', '/')
    return any(n.lower() in raw or n.lower() in as_path for n in needles)


_piper_unused_paths = (
    'piper/tashkeel/',          # Arabic diacritizer ONNX + scaler (4.6 MB)
    'piper/train/',             # training utilities
    'piper/phonemize_chinese',  # CN phonemizer
    'piper/http_server',        # CLI HTTP server entry point
    'piper/__main__',           # python -m piper dispatcher
    'piper/download_voices',    # voice-pack downloader
)

_onnxruntime_unused_paths = (
    'onnxruntime/transformers/',   # ONNX-Runtime transformer optimizers
    'onnxruntime/quantization/',   # quantization toolchain
    'onnxruntime/tools/',          # CLI tools
    'onnxruntime/backend/',        # legacy backend shim
    'onnxruntime/datasets/',       # sample data
)

_unused_path_needles = _piper_unused_paths + _onnxruntime_unused_paths

a.datas = [d for d in a.datas if not _drop_path(d, *_unused_path_needles)]
a.pure = [p for p in a.pure if not _drop_path(p, *_unused_path_needles)]
a.binaries = [b for b in a.binaries if not _drop_path(b, *_unused_path_needles)]

# ── PYZ + EXE + COLLECT ───────────────────────────────────────────────────

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
    name='audiobookmaker',  # produces audiobookmaker.exe
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,           # console mode — the CLI writes to stdout/stderr
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # no icon needed for a console binary
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
    # Output folder: dist/audiobookmaker_cli/. Must NOT be 'audiobookmaker' —
    # on case-insensitive Windows that collides with the main app's
    # dist/AudiobookMaker/ from audiobookmaker.spec, and PyInstaller's COLLECT
    # then aborts with "output directory is not empty". The exe inside stays
    # audiobookmaker.exe (see EXE name= above).
    name='audiobookmaker_cli',
)
