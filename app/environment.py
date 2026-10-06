"""Проверка окружения: что установлено, чего не хватает и чем это грозит.

Исполняемый файл DarkCarnival.exe — тонкая оболочка: внутри только интерфейс и
загрузчик скриптов. Тяжёлый зрительный стек (EasyOCR, torch, OpenCV) намеренно
не упакован — иначе сборка весила бы около двух гигабайт. Поэтому перед первым
запуском полезно честно показать оператору, какие возможности сейчас доступны.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass

__all__ = ["Requirement", "REQUIREMENTS", "check_environment", "environment_report",
           "is_frozen", "capabilities"]


@dataclass(frozen=True)
class Requirement:
    """Один внешний пакет и его роль в работе бота."""

    module: str
    package: str
    purpose: str
    critical: bool = True

    @property
    def installed(self) -> bool:
        try:
            return importlib.util.find_spec(self.module) is not None
        except (ImportError, ValueError):       # повреждённая установка
            return False


REQUIREMENTS: tuple[Requirement, ...] = (
    Requirement("PySide6", "PySide6", "окно программы"),
    Requirement("easyocr", "easyocr", "чтение русского текста на экране"),
    Requirement("torch", "torch", "движок нейросети для EasyOCR"),
    Requirement("cv2", "opencv-python", "сравнение портретов героев"),
    Requirement("numpy", "numpy", "обработка кадров"),
    Requirement("mss", "mss", "снимки экрана"),
    Requirement("pyautogui", "pyautogui", "движения мыши и клики"),
    Requirement("pyperclip", "pyperclip", "ввод кириллицы через буфер обмена", critical=False),
)


def is_frozen() -> bool:
    """Запущены ли мы из собранного .exe (а не из исходников)."""
    return bool(getattr(sys, "frozen", False))


def check_environment() -> list[tuple[Requirement, bool]]:
    """Список пар «требование — установлено ли»."""
    return [(req, req.installed) for req in REQUIREMENTS]


def capabilities() -> dict[str, bool]:
    """Что программа реально умеет прямо сейчас."""
    state = {req.module: req.installed for req in REQUIREMENTS}
    return {
        "интерфейс": state.get("PySide6", False),
        "чтение экрана": state.get("easyocr", False) and state.get("torch", False),
        "снимки экрана": state.get("mss", False) and state.get("numpy", False),
        "управление мышью": state.get("pyautogui", False),
        "сравнение портретов": state.get("cv2", False),
    }


def environment_report() -> list[tuple[str, str]]:
    """Готовые строки для журнала: ``(уровень, текст)`` на русском языке."""
    lines: list[tuple[str, str]] = []
    missing = [req for req, ok in check_environment() if not ok]
    critical = [req for req in missing if req.critical]

    lines.append(("info", f"Python {sys.version.split()[0]}, "
                          f"режим: {'собранный .exe' if is_frozen() else 'исходники'}"))
    for req, ok in check_environment():
        lines.append((
            "success" if ok else ("error" if req.critical else "warning"),
            f"{'✓' if ok else '✗'} {req.package} — {req.purpose}",
        ))

    if not critical:
        lines.append(("success", "Окружение готово: доступен полный режим с распознаванием."))
        return lines

    names = ", ".join(req.package for req in critical)
    lines.append(("error", f"Не хватает пакетов: {names}"))
    if is_frozen():
        lines.append(("error",
                      "Вы запустили .exe — тяжёлые библиотеки распознавания в него не "
                      "упакованы. Для реальной игры запустите ЗАПУСК.bat (работа из "
                      "исходников). В .exe доступны только интерфейс, таблица билетов, "
                      "планирование и обновление скриптов."))
    else:
        lines.append(("error", "Установите их командой:  pip install -r requirements.txt"))
    return lines
