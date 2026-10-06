"""Shared fixtures. Puts the external ``scripts/`` dir on ``sys.path`` exactly the way
the production :class:`app.loader.ScriptHost` does, so tests exercise the real import
topology (plain ``import vision`` from inside ``dota_logic`` etc.)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

for p in (str(ROOT), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)


# --------------------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------------------


class FakeScreen:
    """Screen source that returns a dummy frame of a fixed size."""

    def __init__(self, width: int = 1920, height: int = 1080) -> None:
        self.width, self.height = width, height
        self.grabs: list = []

    def size(self):
        return (self.width, self.height)

    def grab(self, region=None):
        self.grabs.append(region)
        return [[0]]  # opaque to the fake OCR backend


class FakeOcr:
    """OCR backend driven by a scripted list of screens.

    Each "screen" is a list of ``(text, (l, t, w, h), confidence)`` triples. Every call to
    :meth:`read` returns the current screen; call :meth:`advance` to move to the next one.
    """

    def __init__(self, screens=None) -> None:
        self.screens = list(screens or [[]])
        self.index = 0
        self.reads = 0

    @property
    def current(self):
        return self.screens[min(self.index, len(self.screens) - 1)]

    def set_screen(self, items) -> None:
        self.screens = [items]
        self.index = 0

    def advance(self) -> None:
        self.index = min(self.index + 1, len(self.screens) - 1)

    def read(self, image):
        self.reads += 1
        return list(self.current)


def word(text: str, x: int = 100, y: int = 100, w: int = 120, h: int = 30, conf: float = 0.95):
    """Helper to build one OCR detection tuple."""
    return (text, (x, y, w, h), conf)


@pytest.fixture
def fake_screen():
    return FakeScreen()


@pytest.fixture
def fake_ocr():
    return FakeOcr()


@pytest.fixture
def vision_mod():
    import vision

    return vision


@pytest.fixture
def vision(fake_screen, fake_ocr, vision_mod):
    """A Vision engine wired to fakes, with caching effectively disabled."""
    cfg = vision_mod.VisionConfig(cache_ttl=0.0, upscale=1.0)
    return vision_mod.Vision(screen=fake_screen, ocr=fake_ocr, config=cfg)


@pytest.fixture
def logic():
    import dota_logic

    return dota_logic


@pytest.fixture
def brain(tmp_path):
    import learning

    return learning.LearningStore(str(tmp_path / "brain.json"), autosave=False)


@pytest.fixture
def book(tmp_path):
    """Пустая таблица билетов во временной папке."""
    import tickets

    return tickets.TicketBook(path=str(tmp_path / "tickets.json"))
