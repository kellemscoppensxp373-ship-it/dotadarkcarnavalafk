"""
input_handler.py — Humanised input synthesis.

Dota 2 is a DirectX client: plain ``SendInput`` via pyautogui is usually accepted in the
dashboard/HUD, but raw-input surfaces are better served by ``pydirectinput``. This module
picks the strongest available backend at runtime and degrades gracefully to a DryRun
backend (pure logging) so the whole stack can be exercised on CI or in "simulation" mode
from the GUI.

Humanisation matters: constant-velocity teleport clicks at identical pixels are the single
most obvious automation signature. Every move is a Bezier path with eased timing, every
click target is jittered inside the bounding box, and every delay is sampled from a
distribution rather than a constant.
"""

from __future__ import annotations

import logging
import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("dc.input")


# --------------------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------------------


class InputBackend:
    """Minimal surface the executor relies on."""

    name = "abstract"

    def move_to(self, x: int, y: int) -> None: raise NotImplementedError
    def mouse_down(self, button: str = "left") -> None: raise NotImplementedError
    def mouse_up(self, button: str = "left") -> None: raise NotImplementedError
    def scroll(self, clicks: int) -> None: raise NotImplementedError
    def key_press(self, key: str) -> None: raise NotImplementedError
    def key_down(self, key: str) -> None: raise NotImplementedError
    def key_up(self, key: str) -> None: raise NotImplementedError
    def type_text(self, text: str) -> None: raise NotImplementedError
    def position(self) -> tuple[int, int]: return (0, 0)


class DryRunBackend(InputBackend):
    """Records intent without touching the OS. Default on non-Windows / simulation mode."""

    name = "dryrun"

    def __init__(self, sink: Callable[[str], None] | None = None) -> None:
        self.events: list[tuple[str, tuple[Any, ...]]] = []
        self._sink = sink or (lambda msg: log.info("[DRYRUN] %s", msg))
        self._pos = (0, 0)

    def _record(self, kind: str, *args: Any) -> None:
        self.events.append((kind, args))
        self._sink(f"{kind}{args}")

    def move_to(self, x: int, y: int) -> None:
        self._pos = (x, y)
        self._record("move_to", x, y)

    def mouse_down(self, button: str = "left") -> None: self._record("mouse_down", button)
    def mouse_up(self, button: str = "left") -> None: self._record("mouse_up", button)
    def scroll(self, clicks: int) -> None: self._record("scroll", clicks)
    def key_press(self, key: str) -> None: self._record("key_press", key)
    def key_down(self, key: str) -> None: self._record("key_down", key)
    def key_up(self, key: str) -> None: self._record("key_up", key)
    def type_text(self, text: str) -> None: self._record("type_text", text)
    def position(self) -> tuple[int, int]: return self._pos


class PyAutoGuiBackend(InputBackend):
    """SendInput-based backend; prefers pydirectinput for game-surface compatibility."""

    name = "pyautogui"

    def __init__(self, prefer_directinput: bool = True) -> None:
        import pyautogui  # lazy

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.0
        self._gui = pyautogui
        self._di = None
        if prefer_directinput:
            try:
                import pydirectinput

                pydirectinput.FAILSAFE = True
                pydirectinput.PAUSE = 0.0
                self._di = pydirectinput
                self.name = "pydirectinput"
            except Exception:  # pragma: no cover - optional dep
                log.info("pydirectinput unavailable; using pyautogui")

    @property
    def _m(self):
        return self._di or self._gui

    def move_to(self, x: int, y: int) -> None: self._m.moveTo(int(x), int(y))
    def mouse_down(self, button: str = "left") -> None: self._m.mouseDown(button=button)
    def mouse_up(self, button: str = "left") -> None: self._m.mouseUp(button=button)
    def scroll(self, clicks: int) -> None: self._gui.scroll(int(clicks))
    def key_press(self, key: str) -> None: self._m.press(key)
    def key_down(self, key: str) -> None: self._m.keyDown(key)
    def key_up(self, key: str) -> None: self._m.keyUp(key)
    def position(self) -> tuple[int, int]:
        p = self._gui.position()
        return (int(p[0]), int(p[1]))

    def type_text(self, text: str) -> None:
        # pyautogui cannot type Cyrillic reliably; use the clipboard for non-ASCII.
        if text.isascii():
            self._gui.typewrite(text, interval=random.uniform(0.04, 0.11))
            return
        try:  # pragma: no cover - requires a clipboard
            import pyperclip

            pyperclip.copy(text)
            self._gui.hotkey("ctrl", "v")
        except Exception:
            log.warning("clipboard paste failed for %r; falling back to typewrite", text)
            self._gui.typewrite(text, interval=0.08)


def auto_backend(simulate: bool = False) -> InputBackend:
    """Pick the best backend available on this machine."""
    if simulate:
        return DryRunBackend()
    try:
        return PyAutoGuiBackend()
    except Exception:
        log.warning("No OS input backend available — running in DRY RUN mode.")
        return DryRunBackend()


# --------------------------------------------------------------------------------------
# Humanised controller
# --------------------------------------------------------------------------------------


@dataclass
class HumanProfile:
    """Tunable "personality" of the synthetic operator."""

    move_duration: tuple[float, float] = (0.18, 0.42)
    click_hold: tuple[float, float] = (0.045, 0.11)
    post_click: tuple[float, float] = (0.12, 0.30)
    jitter_ratio: float = 0.28       # fraction of the box half-size used for target jitter
    overshoot_chance: float = 0.22
    steps_per_100px: float = 7.0
    min_steps: int = 10
    max_steps: int = 90


def _bezier(p0, p1, p2, p3, t: float) -> tuple[float, float]:
    u = 1.0 - t
    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
            a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def _ease(t: float) -> float:
    """Ease-in-out: slow start, fast middle, decelerating approach."""
    return 3 * t * t - 2 * t * t * t


def bezier_path(
    start: tuple[int, int],
    end: tuple[int, int],
    steps: int,
    rng: random.Random,
    curvature: float = 0.22,
) -> list[tuple[int, int]]:
    """Generate a curved, eased pointer path between two points."""
    steps = max(2, int(steps))
    (x0, y0), (x1, y1) = start, end
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy) or 1.0
    # Perpendicular offsets create the natural arc of a human wrist movement.
    nx, ny = -dy / dist, dx / dist
    m1 = rng.uniform(-curvature, curvature) * dist
    m2 = rng.uniform(-curvature, curvature) * dist
    c1 = (x0 + dx * 0.33 + nx * m1, y0 + dy * 0.33 + ny * m1)
    c2 = (x0 + dx * 0.66 + nx * m2, y0 + dy * 0.66 + ny * m2)
    pts = []
    for i in range(1, steps + 1):
        px, py = _bezier(start, c1, c2, end, _ease(i / steps))
        pts.append((int(round(px)), int(round(py))))
    pts[-1] = (int(x1), int(y1))
    return pts


def jitter_point(
    box: Any,
    rng: random.Random,
    ratio: float = 0.28,
) -> tuple[int, int]:
    """Pick a believable click point inside a bounding box (never the exact centre)."""
    cx, cy = box.center
    half_w = max(1, box.width // 2)
    half_h = max(1, box.height // 2)
    jx = int(rng.gauss(0, half_w * ratio / 2))
    jy = int(rng.gauss(0, half_h * ratio / 2))
    jx = max(-int(half_w * ratio), min(int(half_w * ratio), jx))
    jy = max(-int(half_h * ratio), min(int(half_h * ratio), jy))
    return (cx + jx, cy + jy)


class InputHandler:
    """High-level, humanised input API used by ``executor.py``."""

    def __init__(
        self,
        backend: InputBackend | None = None,
        profile: HumanProfile | None = None,
        *,
        simulate: bool = False,
        seed: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.backend = backend if backend is not None else auto_backend(simulate)
        self.profile = profile or HumanProfile()
        self.rng = random.Random(seed)
        self._sleep = sleep
        self.enabled = True  # master kill-switch flipped by the GUI panic key

    # -- primitives ----------------------------------------------------------------

    def _pause(self, span: tuple[float, float]) -> None:
        self._sleep(self.rng.uniform(*span))

    def move(self, x: int, y: int) -> None:
        if not self.enabled:
            return
        start = self.backend.position()
        dist = math.hypot(x - start[0], y - start[1])
        steps = int(min(self.profile.max_steps,
                        max(self.profile.min_steps, dist / 100.0 * self.profile.steps_per_100px)))
        duration = self.rng.uniform(*self.profile.move_duration)

        target = (x, y)
        if self.rng.random() < self.profile.overshoot_chance and dist > 180:
            # Humans overshoot fast long throws, then correct.
            ox = x + int(self.rng.uniform(-18, 18))
            oy = y + int(self.rng.uniform(-18, 18))
            for px, py in bezier_path(start, (ox, oy), steps, self.rng):
                self.backend.move_to(px, py)
                self._sleep(duration / steps)
            start = (ox, oy)
            steps = max(4, steps // 3)
            duration *= 0.35

        for px, py in bezier_path(start, target, steps, self.rng):
            self.backend.move_to(px, py)
            self._sleep(duration / steps)

    def click(self, x: int, y: int, button: str = "left") -> None:
        if not self.enabled:
            return
        self.move(x, y)
        self._pause((0.02, 0.07))
        self.backend.mouse_down(button)
        self._pause(self.profile.click_hold)
        self.backend.mouse_up(button)
        self._pause(self.profile.post_click)
        log.debug("click %s @(%d,%d)", button, x, y)

    def click_box(self, box: Any, button: str = "left") -> tuple[int, int]:
        """Click a believable random point inside an OCR/template bounding box."""
        x, y = jitter_point(box, self.rng, self.profile.jitter_ratio)
        self.click(x, y, button)
        return (x, y)

    def click_hit(self, hit: Any, button: str = "left") -> tuple[int, int]:
        """Click a ``TextHit`` / ``TemplateHit`` returned by ``vision.py``."""
        return self.click_box(hit.region, button)

    def double_click(self, x: int, y: int) -> None:
        self.click(x, y)
        self._sleep(self.rng.uniform(0.06, 0.13))
        self.backend.mouse_down("left")
        self._pause(self.profile.click_hold)
        self.backend.mouse_up("left")

    def scroll(self, clicks: int) -> None:
        if not self.enabled:
            return
        direction = 1 if clicks > 0 else -1
        for _ in range(abs(int(clicks))):
            self.backend.scroll(direction * self.rng.randint(1, 2))
            self._sleep(self.rng.uniform(0.05, 0.12))

    def press(self, key: str) -> None:
        if not self.enabled:
            return
        self.backend.key_press(key)
        self._pause((0.05, 0.14))

    def hotkey(self, *keys: str) -> None:
        if not self.enabled:
            return
        for k in keys:
            self.backend.key_down(k)
            self._sleep(self.rng.uniform(0.02, 0.05))
        for k in reversed(keys):
            self.backend.key_up(k)
            self._sleep(self.rng.uniform(0.02, 0.05))

    def type_text(self, text: str) -> None:
        """Type a string — Cyrillic-safe (clipboard paste under the hood when needed)."""
        if not self.enabled:
            return
        self.backend.type_text(text)
        self._pause((0.10, 0.22))

    def clear_field(self) -> None:
        self.hotkey("ctrl", "a")
        self.press("backspace")

    def idle(self, lo: float = 0.4, hi: float = 1.2) -> None:
        """Deliberate human-scale pause between logical actions."""
        self._sleep(self.rng.uniform(lo, hi))

    def wiggle(self) -> None:
        """Tiny anti-idle pointer drift — keeps the client from idling the account out."""
        x, y = self.backend.position()
        self.move(x + self.rng.randint(-40, 40), y + self.rng.randint(-25, 25))


__all__ = [
    "InputHandler", "HumanProfile", "InputBackend", "DryRunBackend",
    "PyAutoGuiBackend", "auto_backend", "bezier_path", "jitter_point",
]
