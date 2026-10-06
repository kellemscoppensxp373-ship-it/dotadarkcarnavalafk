"""Persistent GUI settings (JSON, atomic write)."""

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

    # Loop
    hero_pool: list[str] = field(default_factory=lambda: [
        "phantom_assassin", "juggernaut", "lina", "wraith_king",
    ])
    max_cycles: int = 0
    ticket_target: dict[str, int] = field(default_factory=dict)
    leave_early: bool = True
    poll_interval: float = 2.0
    simulate: bool = True          # safe default: never move a real mouse unasked

    # Vision
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
