"""
packaging.py — помощник сборки .exe.

Решает одну конкретную, крайне неприятную проблему.

PyInstaller составляет список модулей, обходя импорты **точки входа**. Внешние
скрипты из ``scripts/`` он не видит никогда: они загружаются из файлов уже в
рантайме, через ``importlib``. Поэтому модуль стандартной библиотеки, нужный
только скриптам, в сборку не попадает, и .exe падает у пользователя с

    ModuleNotFoundError: No module named 'difflib'

причём не при запуске, а позже — когда до этого модуля дойдёт исполнение.

Здесь импорты всех ``scripts/*.py`` разбираются через ``ast`` и отдаются в
``hiddenimports``. Логика вынесена из .spec в обычный модуль, чтобы её можно
было покрыть тестом: тест сборки дешевле, чем отладка чужого .exe по переписке.
"""

from __future__ import annotations

import ast
from pathlib import Path

#: Тяжёлые пакеты зрения/ввода — в тонкую оболочку не кладутся.
HEAVY: frozenset[str] = frozenset({
    "torch", "torchvision", "easyocr", "cv2", "numpy", "scipy",
    "matplotlib", "pandas", "PIL", "mss", "pyautogui", "pydirectinput",
    "pyperclip",
})

#: Импортируется лениво внутри функций — обход верхнего уровня это пропустит.
ALWAYS_INCLUDE: frozenset[str] = frozenset({
    "difflib", "unicodedata", "tempfile", "json", "logging", "math", "random",
    "re", "time", "os", "sys", "threading", "dataclasses", "enum", "typing",
    "collections", "collections.abc", "importlib", "importlib.util", "shutil",
    "hashlib", "traceback", "urllib", "urllib.request", "urllib.error",
    "urllib.parse", "pathlib", "statistics",
})

#: Пакеты самой оболочки.
SHELL_MODULES: frozenset[str] = frozenset({
    "app", "app.loader", "app.ota", "app.paths", "app.settings", "app.packaging",
})


def scan_script_imports(folder: str | Path = "scripts") -> set[str]:
    """Имена модулей, которые импортируют внешние скрипты.

    Соседние скрипты (``import vision`` внутри ``dota_logic``) зависимостями
    сборки не являются и отбрасываются, как и тяжёлые ML-пакеты.

    Отбрасывается и пакетная форма (``scripts.vision``): бизнес-логика обязана
    остаться обычными файлами рядом с .exe, иначе горячая замена потеряет смысл.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return set()

    local = {p.stem for p in folder.glob("*.py")} | {folder.name}
    found: set[str] = set()
    for path in sorted(folder.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name)
            # Относительные импорты (node.level > 0) зависимостями не являются.
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module)
    return {m for m in found if m.split(".")[0] not in (local | HEAVY)}


def build_hiddenimports(folder: str | Path = "scripts") -> list[str]:
    """Итоговый список ``hiddenimports`` для .spec."""
    return sorted(scan_script_imports(folder) | ALWAYS_INCLUDE | SHELL_MODULES)


def missing_from_bundle(folder: str | Path = "scripts") -> list[str]:
    """Импорты скриптов, которые НЕ попадут в сборку (диагностика)."""
    return sorted(scan_script_imports(folder) - set(build_hiddenimports(folder)))


__all__ = [
    "HEAVY", "ALWAYS_INCLUDE", "SHELL_MODULES",
    "scan_script_imports", "build_hiddenimports", "missing_from_bundle",
]
