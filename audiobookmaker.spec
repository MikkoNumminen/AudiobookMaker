# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for AudiobookMaker
# Build with: pyinstaller audiobookmaker.spec

import os
from PyInstaller.utils.hooks import collect_submodules

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
    'aiohttp',
    'aiohttp.resolver',
    'aiohttp.connector',
    'certifi',
]

hidden_imports += collect_submodules('edge_tts')
hidden_imports += collect_submodules('aiohttp')

excludes = [
    'matplotlib',
    'numpy',
    'scipy',
    'PIL',
    'cv2',
    'pandas',
    'IPython',
    'notebook',
    'sphinx',
    'docutils',
]

# Bundle ffmpeg.exe from dist/ffmpeg/ into the package root
# so pydub can find it via PATH at runtime (see src/ffmpeg_path.py)
datas = [
    (os.path.join('dist', 'ffmpeg', 'ffmpeg.exe'), '.'),
]

a = Analysis(
    [os.path.join('src', 'main.py')],
    pathex=[os.path.abspath('.')],
    binaries=[],
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
