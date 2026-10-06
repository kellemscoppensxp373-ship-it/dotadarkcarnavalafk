"""A scripted, region-aware fake Dota 2 client rendering RUSSIAN text.

This is the test harness that lets the whole agent be driven end-to-end with no Dota, no
GPU, no OCR model and no real mouse:

* It exposes the :class:`vision.ScreenSource` / :class:`vision.OcrBackend` duck types, so
  the **real** ``Vision`` engine runs against it (real normalisation, real fuzzy matching,
  real region arithmetic).
* It is **region-aware**: ``grab(region)`` crops, and the OCR output is returned in
  region-local coordinates — exactly like EasyOCR on a cropped frame. That means a test
  failure here is a genuine coordinate bug, not a harness artefact.
* It reacts to **clicks**: the DryRun input backend feeds pointer coordinates back in, the
  client works out which Russian caption was hit, and advances its own state machine.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Caption:
    text: str
    x: int
    y: int
    w: int = 180
    h: int = 48
    conf: float = 0.95

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h


# Each screen of the Russian client, as the OCR would see it.
SCREENS: dict[str, list[Caption]] = {
    "dashboard": [
        Caption("Тёмный карнавал", 200, 60, 320),
        Caption("Против ботов", 1400, 820, 240),
        Caption("Играть", 1500, 950, 200, 60),
    ],
    "queueing": [
        Caption("Тёмный карнавал", 200, 60, 320),
        Caption("Отменить поиск", 1450, 950, 220),
    ],
    "match_found": [
        Caption("Игра найдена", 860, 420, 260),
        Caption("Принять", 880, 540, 200, 70),
    ],
    "hero_pick": [
        Caption("Поиск", 200, 130, 160),
        Caption("Фантом Ассасин", 420, 400, 260),
        Caption("Джаггернаут", 760, 400, 240),
        Caption("Лина", 1060, 400, 160),
        Caption("Призрачный Король", 1300, 400, 300),
        Caption("Выбрать", 860, 760, 200, 60),
        Caption("Готов", 1600, 980, 160),
    ],
    "in_game": [
        Caption("Покинуть игру", 100, 1000, 240),
    ],
    "post_game": [
        Caption("Победа", 880, 200, 220, 70),
        Caption("Игру можно безопасно покинуть", 640, 320, 620),
        Caption("Покинуть игру", 880, 700, 240),
    ],
    "reward": [
        Caption("Забрать награду", 820, 560, 300),
        Caption("Продолжить", 880, 760, 220),
    ],
    "disconnected": [
        Caption("Соединение потеряно", 780, 420, 360),
        Caption("Переподключиться", 820, 540, 300),
    ],
}


class FakeDotaClient:
    """Region-aware screen + OCR + click-driven state machine."""

    def __init__(self, width: int = 1920, height: int = 1080, start: str = "dashboard") -> None:
        self.width, self.height = width, height
        self.screen_name = start
        self.clicks: list[tuple[int, int, str]] = []
        self.typed: list[str] = []
        self.transitions: list[str] = []
        self.queue_ticks = 0
        self.game_ticks = 0
        self.cycles_completed = 0
        self._region = None

    # -- vision.ScreenSource ------------------------------------------------------

    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def grab(self, region=None):
        self._region = region
        return [[0]]

    # -- vision.OcrBackend --------------------------------------------------------

    def read(self, image):
        """Return captions visible in the last grabbed region, in region-local coords."""
        region = self._region
        out = []
        for cap in self.captions():
            if region is None:
                out.append((cap.text, cap.box, cap.conf))
                continue
            # Keep only captions fully inside the crop, translated to local space.
            if (cap.x >= region.left and cap.y >= region.top
                    and cap.x + cap.w <= region.right and cap.y + cap.h <= region.bottom):
                out.append((cap.text,
                            (cap.x - region.left, cap.y - region.top, cap.w, cap.h),
                            cap.conf))
        return out

    def captions(self) -> list[Caption]:
        return SCREENS.get(self.screen_name, [])

    # -- simulation ---------------------------------------------------------------

    def goto(self, name: str) -> None:
        if name != self.screen_name:
            self.transitions.append(name)
        self.screen_name = name

    def tick(self) -> None:
        """Time passing inside the client (queue completing, bots finishing the game)."""
        if self.screen_name == "queueing":
            self.queue_ticks += 1
            if self.queue_ticks >= 2:
                self.queue_ticks = 0
                self.goto("match_found")
        elif self.screen_name == "in_game":
            self.game_ticks += 1
            if self.game_ticks >= 2:
                self.game_ticks = 0
                self.goto("post_game")

    def click(self, px: int, py: int) -> str:
        """Resolve a pointer position to a caption and advance the client."""
        hit = next((c for c in self.captions() if c.contains(px, py)), None)
        label = hit.text if hit else ""
        self.clicks.append((px, py, label))
        if hit is None:
            return ""
        text = hit.text
        screen = self.screen_name
        if text == "Играть":
            self.goto("queueing")
        elif text == "Отменить поиск":
            self.goto("dashboard")
        elif text == "Принять":
            self.goto("hero_pick")
        elif text in {"Выбрать", "Готов"} and screen == "hero_pick":
            self.goto("in_game")
        elif text == "Покинуть игру" and screen == "post_game":
            self.goto("reward")
        elif text in {"Забрать награду", "Продолжить"} and screen == "reward":
            self.cycles_completed += 1
            self.goto("dashboard")
        elif text == "Переподключиться":
            self.goto("in_game")
        return label

    def type_text(self, text: str) -> None:
        self.typed.append(text)


class ClickSpy:
    """Input backend that forwards synthesised clicks into the fake client."""

    name = "fake-dota"

    def __init__(self, client: FakeDotaClient) -> None:
        self.client = client
        self._pos = (0, 0)
        self.events: list[tuple[str, tuple]] = []

    def move_to(self, x: int, y: int) -> None:
        self._pos = (int(x), int(y))
        self.events.append(("move_to", (x, y)))

    def mouse_down(self, button: str = "left") -> None:
        self.events.append(("mouse_down", (button,)))

    def mouse_up(self, button: str = "left") -> None:
        self.events.append(("mouse_up", (button,)))
        self.client.click(*self._pos)

    def scroll(self, clicks: int) -> None:
        self.events.append(("scroll", (clicks,)))

    def key_press(self, key: str) -> None:
        self.events.append(("key_press", (key,)))

    def key_down(self, key: str) -> None:
        self.events.append(("key_down", (key,)))

    def key_up(self, key: str) -> None:
        self.events.append(("key_up", (key,)))

    def type_text(self, text: str) -> None:
        self.events.append(("type_text", (text,)))
        self.client.type_text(text)

    def position(self) -> tuple[int, int]:
        return self._pos
