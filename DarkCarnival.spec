# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — компилирует ТОЛЬКО тонкую GUI-оболочку.

Намеренно НЕ попадает в бинарник:
  * ``scripts/``  — бизнес-логика лежит РЯДОМ с .exe обычными .py-файлами, чтобы её
                    можно было менять на лету и обновлять по OTA. Упаковать её внутрь
                    значило бы похоронить всю архитектуру.
  * ``assets/``   — спрайты героев, меняются между патчами события.

ВАЖНО — почему здесь есть авто-сканирование импортов
-----------------------------------------------------
PyInstaller строит список модулей, обходя импорты ТОЧКИ ВХОДА. Внешние скрипты он
не видит в принципе: они подгружаются из файлов уже в рантайме. Поэтому любой модуль
стандартной библиотеки, который нужен только скриптам (``difflib``, ``unicodedata``…),
в сборку не попадал — и .exe падал с ``ModuleNotFoundError: No module named 'difflib'``
ровно в тот момент, когда пользователь жал «Старт».

Ниже импорты всех файлов ``scripts/*.py`` разбираются через ``ast`` и добавляются в
``hiddenimports``. Добавите завтра новый скрипт с новым импортом — сборка подхватит
его сама, без правки этого файла.

Сборка:  pyinstaller DarkCarnival.spec --noconfirm
Итог:    dist/DarkCarnival/DarkCarnival.exe  (+ рядом копируются scripts/ и assets/)
"""

import sys

sys.path.insert(0, ".")
# Сообщения ниже — только латиницей: консоль сборщика на Windows живёт в cp1251,
# и кириллический print роняет весь .spec с UnicodeEncodeError.
from app.packaging import HEAVY, build_hiddenimports, scan_script_imports  # noqa: E402

block_cipher = None

hidden = build_hiddenimports("scripts")
print("[spec] scanned script imports:", ", ".join(sorted(scan_script_imports("scripts"))))
print("[spec] hiddenimports total:", len(hidden))

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=sorted(HEAVY),
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
    console=False,          # оконное приложение, без консоли
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
