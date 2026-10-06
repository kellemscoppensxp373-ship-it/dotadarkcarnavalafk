"""Path resolution that behaves identically in a dev checkout and inside a PyInstaller exe.

The golden rule of this architecture: **the scripts folder lives NEXT TO the executable,
never inside it.** ``sys._MEIPASS`` (the temp extraction dir) is read-only and vanishes on
exit, so writing hot-swapped scripts there would silently lose every update.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def app_dir() -> Path:
    """Directory that *contains* the running app (exe dir when frozen, repo root in dev)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """Read-only directory of bundled resources (``sys._MEIPASS`` when frozen)."""
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return app_dir()


def scripts_dir() -> Path:
    """Writable, hot-swappable business-logic directory beside the executable."""
    override = os.environ.get("DC_SCRIPTS_DIR")
    path = Path(override).expanduser().resolve() if override else app_dir() / "scripts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def assets_dir() -> Path:
    override = os.environ.get("DC_ASSETS_DIR")
    path = Path(override).expanduser().resolve() if override else app_dir() / "assets"
    path.mkdir(parents=True, exist_ok=True)
    (path / "emoji").mkdir(exist_ok=True)
    (path / "portraits").mkdir(exist_ok=True)
    return path


def data_dir() -> Path:
    """Writable storage for the brain, settings, logs and script backups."""
    override = os.environ.get("DC_DATA_DIR")
    path = Path(override).expanduser().resolve() if override else app_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backups_dir() -> Path:
    path = data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_file() -> Path:
    return data_dir() / "settings.json"


def brain_file() -> Path:
    return data_dir() / "brain.json"


def tickets_file() -> Path:
    """Таблица «аркан → герой ×3», редактируемая на вкладке «Билеты»."""
    return data_dir() / "tickets.json"


def log_file() -> Path:
    return data_dir() / "agent.log"


__all__ = [
    "is_frozen", "app_dir", "bundle_dir", "scripts_dir", "assets_dir",
    "data_dir", "backups_dir", "settings_file", "brain_file", "tickets_file",
    "log_file",
]
