# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for AudiobookMaker
# Build with: pyinstaller audiobookmaker.spec

import glob
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
# must NOT appear in excludes. Same story for PIL (Pillow) — the hero
# header and icon assets load PNGs via CTkImage which wraps PIL.Image.
#
# Defense-in-depth: today's CI build env (`pip install -r requirements.txt`)
# does not install torch / transformers / chatterbox / pyannote / etc., so
# they currently don't reach the bundle. But a future contributor who
# accidentally adds one of those to requirements.txt — or who builds on a
# dev machine with the chatterbox venv polluting site-packages — would
# silently inflate the installer by hundreds of MB. Excluding by name
# turns "accidentally large" into "build error", which is the desired
# trade.
excludes = [
    'matplotlib',
    'scipy',
    'cv2',
    'pandas',
    'IPython',
    'notebook',
    'sphinx',
    'docutils',
    # Heavy ML stack — Chatterbox SYNTHESIS reaches torch through a separate
    # post-install venv (`.venv-chatterbox`), NOT in-process; VoxCPM2 is
    # sys.frozen-gated; voice-pack cloning runs as subprocess scripts that
    # use the chatterbox venv's Python. None of these belong in the frozen
    # main bundle.
    'torch',
    'torchaudio',
    'torchvision',
    'transformers',
    'chatterbox',
    'chatterbox_tts',
    'voxcpm',
    'pyannote',  # prefix-matches pyannote.audio / pyannote.core / pyannote.metrics
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
    'sklearn',  # also the PyPI 'scikit-learn' wheel — same package
    'huggingface_hub',
    # Test machinery — never reached at runtime.
    'pytest',
    'pytest_asyncio',
    'pytest_cov',
    'pytest_timeout',
    '_pytest',
    'coverage',
    # System-Python pollution from dev tooling (pip_audit / cyclonedx /
    # rich / markdown_it / etc.). Not reachable from the frozen GUI.
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
    # psutil arrives transitively from pip_audit / cyclonedx in the dev
    # site-packages and is not imported by any reachable code under
    # src/ today. If a future GUI feature actually wants psutil (e.g. a
    # chatterbox-venv health check), remove this entry — there's no
    # other reason to keep it excluded.
    'psutil',
    # Jupyter / notebook stack (transitive risk via dev tooling).
    'jupyter',
    'jupyter_client',
    'jupyter_core',
    'jupyterlab',
    'ipykernel',
    'ipywidgets',
    'tornado',
    'zmq',
    # PIL plugins the app does not use. Keep only Bmp/Jpeg/Png/Ico (CTkImage
    # loads PNG icons + ICO window icon; Bmp/Jpeg added as cheap fallbacks
    # for any user-supplied image). Pillow's PyInstaller hook otherwise
    # pulls all 40+ plugin modules and their native sidecar libs (e.g.
    # _avif.cp311-win_amd64.pyd is 7.6 MB on its own).
    'PIL.AvifImagePlugin',
    'PIL.BlpImagePlugin',
    'PIL.BufrStubImagePlugin',
    'PIL.CurImagePlugin',
    'PIL.DcxImagePlugin',
    'PIL.DdsImagePlugin',
    'PIL.EpsImagePlugin',
    'PIL.FitsImagePlugin',
    'PIL.FliImagePlugin',
    'PIL.FpxImagePlugin',
    'PIL.FtexImagePlugin',
    'PIL.GbrImagePlugin',
    'PIL.GifImagePlugin',
    'PIL.GribStubImagePlugin',
    'PIL.Hdf5StubImagePlugin',
    'PIL.IcnsImagePlugin',
    'PIL.ImImagePlugin',
    'PIL.ImtImagePlugin',
    'PIL.IptcImagePlugin',
    'PIL.Jpeg2KImagePlugin',
    'PIL.McIdasImagePlugin',
    'PIL.MicImagePlugin',
    'PIL.MpegImagePlugin',
    'PIL.MpoImagePlugin',
    'PIL.MspImagePlugin',
    'PIL.PalmImagePlugin',
    'PIL.PcdImagePlugin',
    'PIL.PcxImagePlugin',
    'PIL.PdfImagePlugin',
    'PIL.PixarImagePlugin',
    'PIL.PpmImagePlugin',
    'PIL.PsdImagePlugin',
    'PIL.QoiImagePlugin',
    'PIL.SgiImagePlugin',
    'PIL.SpiderImagePlugin',
    'PIL.SunImagePlugin',
    'PIL.TgaImagePlugin',
    'PIL.TiffImagePlugin',
    'PIL.WebPImagePlugin',
    'PIL.WmfImagePlugin',
    'PIL.XVThumbImagePlugin',
    'PIL.XbmImagePlugin',
    'PIL.XpmImagePlugin',
]

binaries = _all_onnx[1] + _all_piper[1] + _all_pathvalidate[1]
# collect_all('piper') already collects espeakbridge.pyd under piper/.
# Do NOT also add it via collect_dynamic_libs — that bundles a second copy
# at the top level, and Python refuses to load the same native module twice
# ("cannot load module more than once per process").
hidden_imports += ['piper.espeakbridge', 'piper.voice', 'piper.config',
                   'piper.phonemize_espeak', 'piper.phoneme_ids', 'piper.const']

# Bundle ffmpeg.exe and ffprobe.exe from dist/ffmpeg/ into the package
# root so pydub can find them at runtime (see src/ffmpeg_path.py).
# ffprobe is required by pydub to read audio file metadata
# (mediainfo_json) — kept because pydub.AudioSegment.from_file() in
# src/tts_audio.py:85 detects format via ffprobe before deciding which
# decoder to use.
#
# ffplay.exe is NOT bundled — the Listen / Preview button plays via
# pygame in src/_audio_player.py (see src/gui_unified.py:_on_listen_click).
# The dead helper UnifiedApp._find_ffplay at src/gui_unified.py:2442 has
# no callers; voice_recorder.py's ffplay usage lives behind the dev-only
# voice-cloning subprocess that ships its own ffplay via the chatterbox
# venv. Skipping ffplay.exe (~195 MB raw / ~50 MB compressed) is the
# single biggest installer-size win.
datas = [
    (os.path.join('dist', 'ffmpeg', 'ffmpeg.exe'), '.'),
    (os.path.join('dist', 'ffmpeg', 'ffprobe.exe'), '.'),
]
# OCR fallback toolchain: tesseract.exe + DLLs + eng/fin language packs +
# Ghostscript (needed by ocrmypdf for some image preprocessing paths).
# CI populates dist/ocr/ before invoking PyInstaller (see
# .github/workflows/build-release.yml). When the directory is missing
# — typical for local dev PyInstaller runs without the OCR download
# step — the spec silently skips bundling and the frozen build degrades
# to no-OCR mode (the runtime resolver returns is_ocr_available() ==
# False and parse_pdf raises the existing EmptyPDFError on scanned input
# rather than crashing). See src/ocr_path.py.
_OCR_SRC = os.path.join('dist', 'ocr')
if os.path.isdir(_OCR_SRC):
    # tesseract.exe / gswin64c.exe / accompanying DLLs at the install root.
    for _f in glob.glob(os.path.join(_OCR_SRC, '*.exe')):
        datas.append((_f, '.'))
    for _f in glob.glob(os.path.join(_OCR_SRC, '*.dll')):
        datas.append((_f, '.'))
    # Trained data lives in tessdata/ next to the exe; Tesseract honors
    # TESSDATA_PREFIX which src.ocr_path.setup_ocr_path exports.
    _tessdata = os.path.join(_OCR_SRC, 'tessdata')
    if os.path.isdir(_tessdata):
        for _td in glob.glob(os.path.join(_tessdata, '*.traineddata')):
            datas.append((_td, 'tessdata'))
        # tessdata/configs/ holds Tesseract output-format presets (`hocr`,
        # `txt`, `pdf`, etc.) that ocrmypdf invokes via Tesseract subprocess
        # call. Without these files, OCR runs to 100% then ocrmypdf crashes
        # trying to read empty .hocr output. Validated locally during the
        # PR #26 smoke test on a 32-page scanned source.
        _tessconfigs = os.path.join(_tessdata, 'configs')
        if os.path.isdir(_tessconfigs):
            for _cfg in glob.glob(os.path.join(_tessconfigs, '*')):
                if os.path.isfile(_cfg):
                    datas.append((_cfg, os.path.join('tessdata', 'configs')))
# Pull piper/onnxruntime/pathvalidate data (includes espeak-ng-data/,
# onnxruntime config files, etc.) from collect_all().
datas += _all_onnx[0]
datas += _all_piper[0]
datas += _all_pathvalidate[0]
# edge_tts package data
datas += collect_data_files('edge_tts')
# customtkinter assets (themes, icons)
datas += collect_data_files('customtkinter')
# YAML lexicons used by the text normalizers. Non-developers curate
# these tables; the Python modules load them lazily from data/.
for _yaml in glob.glob(os.path.join('data', '*.yaml')):
    datas += [(_yaml, 'data')]
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
    (os.path.join('src', 'tts_normalizer_fi_legal.py'), 'src'),
    (os.path.join('src', 'tts_normalizer_en.py'), 'src'),
    (os.path.join('src', '_en_pass_o_dates.py'), 'src'),
    (os.path.join('src', '_en_pass_p_telephone.py'), 'src'),
    (os.path.join('src', '_en_pass_r_urls.py'), 'src'),
    (os.path.join('src', '_en_pass_s_acronyms.py'), 'src'),
    (os.path.join('src', 'tts_chunking.py'), 'src'),
    (os.path.join('src', 'tts_audio.py'), 'src'),
    (os.path.join('src', 'pdf_parser.py'), 'src'),
    (os.path.join('src', 'ocr_path.py'), 'src'),
    (os.path.join('src', 'epub_parser.py'), 'src'),
    (os.path.join('src', 'fi_loanwords.py'), 'src'),
    (os.path.join('src', 'ffmpeg_path.py'), 'src'),
    (os.path.join('src', '_yaml_data.py'), 'src'),
]
# Goat icon for the window title bar and taskbar
datas += [(os.path.join('assets', 'icon.ico'), 'assets')]
datas += [(os.path.join('assets', 'icon.png'), 'assets')]
# Cold Forge custom theme used by src/gui_style.py. Missing file falls back
# to CTk's built-in "blue" theme at runtime; the bundled file is strictly
# a visual upgrade.
datas += [(os.path.join('assets', 'themes', 'cold_forge.json'),
           os.path.join('assets', 'themes'))]
# Lucide-style icon set rendered by scripts/generate_icons.py. Each button
# in the GUI looks up its PNG via gui_style.icon("name"); a missing asset
# degrades to a text-only button (see gui_style.icon fallback) rather than
# crashing — the bundle test below enforces they stay shipped.
for _icon_png in glob.glob(os.path.join('assets', 'icons', '*.png')):
    datas += [(_icon_png, os.path.join('assets', 'icons'))]
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

# Drop ctranslate2's bundled cudnn64_9.dll. The ctranslate2 wheel ships a
# 266 KB single-file cuDNN build that is ABI-incompatible with the 438 KB
# cuDNN 9 suite torch ships (plus its 7 sibling DLLs) under torch/lib/.
# If both land in the frozen package, whichever wins the Windows DLL search
# order crashes the other consumer — torch fails to load cuDNN ops when the
# ctranslate2 copy gets picked first. Keep torch's full cuDNN suite; strip
# the ctranslate2 duplicate so the end-user .exe matches the dev workaround
# (we rename the ctranslate2 copy to .disabled on developer machines).
#
# (Now strictly belt-and-suspenders — ctranslate2 is also in `excludes=`
# above, so it shouldn't enter the bundle in the first place. Left in
# place to defend against dev-machine builds where ctranslate2 lands via
# site-packages despite the exclude.)
a.binaries = [
    b for b in a.binaries
    if not b[0].lower().replace('\\', '/').endswith('ctranslate2/cudnn64_9.dll')
]

# ── Bundle trimming: drop subtrees and native libs we don't use ──────────
#
# These trims target specific paths inside packages we DO need (piper,
# onnxruntime, PIL). The excludes list above operates at module-import
# granularity; this filter operates on physical paths in `a.datas`,
# `a.pure`, and `a.binaries`. Together they keep the frozen bundle from
# carrying unused dependencies AND from carrying unused payload inside
# used dependencies.
def _drop_path(item, *needles):
    """True if any needle matches the item's dest path (case-insensitive).

    Handles both path-style entries (``a.datas`` / ``a.binaries`` —
    ``"piper/tashkeel/foo.onnx"``) and module-name entries (``a.pure`` —
    ``"piper.tashkeel.foo"``) by checking the raw form and the
    dot-to-slash normalized form against needles like ``"piper/tashkeel/"``.
    """
    raw = item[0].lower().replace('\\', '/')
    as_path = raw.replace('.', '/')
    return any(n.lower() in raw or n.lower() in as_path for n in needles)


# piper ships a 4.6 MB Arabic diacritization model (tashkeel/) plus
# training-only tools and CLI helpers the GUI engine never invokes.
# The engine uses piper.voice.PiperVoice + piper.phonemize_espeak only.
_piper_unused_paths = (
    'piper/tashkeel/',          # Arabic diacritizer ONNX + scaler
    'piper/train/',             # training utilities
    'piper/phonemize_chinese',  # CN phonemizer (we ship en/fi voices)
    'piper/http_server',        # CLI HTTP server entry point
    'piper/__main__',           # python -m piper CLI dispatcher
    'piper/download_voices',    # voice-pack downloader (app uses its own)
)

# onnxruntime ships training/quantization toolchains alongside the
# inference runtime. Piper only consumes onnxruntime.InferenceSession
# (loads from onnxruntime/capi/), so the dev tools are dead weight.
_onnxruntime_unused_paths = (
    'onnxruntime/transformers/',   # ONNX-Runtime transformer optimizers
    'onnxruntime/quantization/',   # model quantization toolchain
    'onnxruntime/tools/',          # CLI tools
    'onnxruntime/backend/',        # legacy backend shim (deprecated)
    'onnxruntime/datasets/',       # sample data
)

# Drop native PIL plugin DLLs that pair with the excluded plugin modules.
# Without the .pyd files the plugin imports would have failed at runtime
# anyway; this just reclaims the disk space.
_pil_unused_binaries = (
    'pil/_avif.',     # 7.6 MB — AVIF format
    'pil/_webp.',     # WebP — no WebP assets in the app
    'pil/_imagingcms.',  # ICC color profile management (printing)
)

_unused_path_needles = _piper_unused_paths + _onnxruntime_unused_paths

a.datas = [d for d in a.datas if not _drop_path(d, *_unused_path_needles)]
a.pure = [p for p in a.pure if not _drop_path(p, *_unused_path_needles)]
a.binaries = [
    b for b in a.binaries
    if not _drop_path(b, *_unused_path_needles)
    and not _drop_path(b, *_pil_unused_binaries)
]

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
