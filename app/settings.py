"""Настройки интерфейса (JSON, атомарная запись)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("dc.settings")


@dataclass
class Settings:
    # OTA
    ota_owner: str = "kellemscoppensxp373-ship-it"
    ota_repo: str = "dotadarkcarnavalafk"
    ota_branch: str = "main"
    ota_path: str = "scripts"
    ota_token: str = ""
    auto_sync_on_start: bool = False

    # Цикл фарма
    max_cycles: int = 0
    #: {ключ аркана: сколько билетов нужно}, например {"death": 30}
    ticket_target: dict[str, int] = field(default_factory=dict)
    #: Минимальная отдача за игру. 3 = играть только на «тройных» героях.
    min_ticket_yield: int = 3
    avoid_heroes: list[str] = field(default_factory=list)
    leave_early: bool = True
    poll_interval: float = 2.0
    simulate: bool = True          # безопасно по умолчанию: мышь не трогаем

    # Зрение
    ocr_gpu: bool = True
    match_threshold: float = 0.78
    upscale: float = 1.6

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def load(cls, path: Path | str) -> Settings:
        path = Path(path)
        if not path.is_file():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("settings unreadable, using defaults", exc_info=True)
            return cls()
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:  # pragma: no cover
            log.error("failed to save settings", exc_info=True)
            if os.path.exists(tmp):
                os.unlink(tmp)


__all__ = ["Settings"]
