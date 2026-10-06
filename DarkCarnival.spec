# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — compiles ONLY the thin GUI shell.

Deliberately excluded from the binary:
  * ``scripts/``  — the business logic ships *beside* the exe as raw .py files so it can
                    be hot-swapped / OTA-updated without a rebuild. Bundling it would
                    defeat the entire architecture.
  * ``assets/``   — hero emoji + portrait sprites, swappable per patch.

Build:  pyinstaller DarkCarnival.spec --noconfirm
Output: dist/DarkCarnival/DarkCarnival.exe  (+ scripts/ and assets/ copied next to it)
"""

import sys

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    # The shell only needs Qt + stdlib. Heavy ML wheels are imported lazily *by the
    # external scripts* at runtime, from the user's own Python environment or from the
    # optional bundled runtime — keeping the shell small and fast to start.
    hiddenimports=[
        "app", "app.loader", "app.ota", "app.paths", "app.settings",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "torch", "torchvision", "easyocr", "cv2", "numpy", "scipy",
        "matplotlib", "pandas", "tkinter", "PIL",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DarkCarnival",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # GUI app — no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DarkCarnival",
)
