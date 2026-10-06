"""Защита от повторения бага «ModuleNotFoundError: difflib» в собранном .exe.

PyInstaller анализирует только граф импортов main.py. Скрипты из /scripts/
загружаются во время работы, поэтому их зависимости из стандартной библиотеки
обязаны попадать в hiddenimports — иначе .exe падает прямо во время игры.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.packaging import (
    ALWAYS_INCLUDE,
    HEAVY,
    build_hiddenimports,
    missing_from_bundle,
    scan_script_imports,
)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_scanner_finds_real_stdlib_dependencies():
    found = scan_script_imports(SCRIPTS)
    assert "difflib" in found, "именно его не хватало в первой сборке"
    assert "unicodedata" in found
    assert "dataclasses" in found


def test_scanner_skips_sibling_scripts_and_heavy_ml():
    found = scan_script_imports(SCRIPTS)
    # Соседние скрипты грузятся загрузчиком, а не импортом — их упаковывать нельзя.
    assert not any(n.split(".")[0] == "executor" for n in found)
    # Тяжёлый ML-стек в тонкую оболочку принципиально не входит.
    assert not (found & HEAVY)
    # Пакетная форма тоже запрещена: иначе PyInstaller утянет логику внутрь .exe
    # и горячая замена файлов перестанет работать.
    assert not any(n.split(".")[0] == "scripts" for n in found), \
        "бизнес-логика должна остаться снаружи"


def test_spec_prints_are_ascii_only():
    """Кириллица в print ломает сборку: консоль Windows не в UTF-8."""
    spec = (SCRIPTS.parent / "DarkCarnival.spec").read_text(encoding="utf-8")
    for line in spec.splitlines():
        if line.startswith("print("):
            assert line.isascii(), f"непечатаемая на Windows строка: {line}"


def test_hiddenimports_cover_every_scanned_import():
    hidden = set(build_hiddenimports(SCRIPTS))
    for name in scan_script_imports(SCRIPTS):
        assert name in hidden, f"{name} не попадёт в .exe"
    assert hidden >= ALWAYS_INCLUDE


def test_nothing_is_missing_from_the_bundle():
    assert missing_from_bundle(SCRIPTS) == []


@pytest.mark.parametrize("module", ["difflib", "unicodedata", "importlib", "json"])
def test_critical_runtime_modules_are_bundled(module):
    assert module in build_hiddenimports(SCRIPTS)


def test_scanner_reads_a_new_script(tmp_path):
    (tmp_path / "fresh.py").write_text(
        "import sqlite3\nfrom textwrap import dedent\nimport torch\n", encoding="utf-8")
    found = scan_script_imports(tmp_path)
    assert {"sqlite3", "textwrap"} <= found
    assert "torch" not in found


def test_spec_file_uses_the_scanner():
    spec = (SCRIPTS.parent / "DarkCarnival.spec").read_text(encoding="utf-8")
    assert "build_hiddenimports" in spec, "спецификация обязана брать список из сканера"
    assert "app.packaging" in spec


# ------------------------------------------------------------------ отчёт об окружении


def test_environment_report_is_russian_and_non_empty():
    from app.environment import environment_report

    lines = environment_report()
    assert lines
    assert any("Python" in text for _, text in lines)
    assert all(level in {"info", "success", "warning", "error"} for level, _ in lines)


def test_missing_packages_are_reported_as_errors(monkeypatch):
    from app import environment

    monkeypatch.setattr(environment.Requirement, "installed", property(lambda self: False))
    levels = {level for level, _ in environment.environment_report()}
    assert "error" in levels
    assert all(not ok for _, ok in environment.check_environment())
    assert not any(environment.capabilities().values())


def test_capabilities_reflect_installed_modules(monkeypatch):
    from app import environment

    monkeypatch.setattr(environment.Requirement, "installed", property(lambda self: True))
    caps = environment.capabilities()
    assert caps["чтение экрана"] and caps["управление мышью"]
    assert all(ok for _, ok in environment.check_environment())


def test_launcher_script_exists_for_full_mode():
    launcher = SCRIPTS.parent / "ЗАПУСК.bat"
    assert launcher.exists(), "нужен понятный способ запустить полный режим"
    text = launcher.read_text(encoding="utf-8")
    assert "requirements.txt" in text and "main.py" in text
