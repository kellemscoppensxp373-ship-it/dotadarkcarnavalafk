"""
learning.py — Adaptive memory for the agent.

The loop is cyclical, so the agent sees the same screens hundreds of times. That is a gift:
it can *learn* from repetition instead of re-deriving everything each cycle.

Three mechanisms:

1. **Spatial priors.** Every successful click on a semantic intent is remembered as a
   normalised (0..1) screen position. Next cycle, the vision layer OCRs the small region
   around the prior *first* and only falls back to a full-screen read if that misses —
   a large latency win, while remaining fully semantic (it still reads the words).
2. **Action statistics.** Per-intent success/failure counters with an exponentially
   weighted success rate, plus observed latency, so the executor can adapt its waits
   instead of using hard-coded sleeps.
3. **Stuck detection.** Tracks the state history and recognises loops / stalls, telling
   the executor when to escalate recovery.

Everything is JSON-persisted so knowledge survives an app restart, and the store is
atomic-write safe (temp file + replace) so a crash mid-save cannot corrupt the brain.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

log = logging.getLogger("dc.learning")

SCHEMA_VERSION = 2


# --------------------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------------------


@dataclass
class IntentStats:
    """Rolling statistics for one semantic intent (e.g. ``"accept"``)."""

    attempts: int = 0
    successes: int = 0
    failures: int = 0
    ewma_success: float = 0.5          # exponentially weighted success rate
    avg_latency: float = 0.0           # seconds from "looked for it" to "found it"
    last_seen: float = 0.0             # unix ts
    # Normalised (0..1) screen position of the last N successful hits.
    hot_x: float = -1.0
    hot_y: float = -1.0
    hot_samples: int = 0

    def record(self, success: bool, latency: float = 0.0, alpha: float = 0.25) -> None:
        self.attempts += 1
        if success:
            self.successes += 1
            self.last_seen = time.time()
            if latency > 0:
                n = max(1, self.successes)
                self.avg_latency += (latency - self.avg_latency) / n
        else:
            self.failures += 1
        self.ewma_success = (1 - alpha) * self.ewma_success + alpha * (1.0 if success else 0.0)

    def record_position(self, nx: float, ny: float, alpha: float = 0.3) -> None:
        """Blend a new normalised hit position into the spatial prior."""
        if not (0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0):
            return
        if self.hot_samples == 0:
            self.hot_x, self.hot_y = nx, ny
        else:
            self.hot_x = (1 - alpha) * self.hot_x + alpha * nx
            self.hot_y = (1 - alpha) * self.hot_y + alpha * ny
        self.hot_samples += 1

    @property
    def success_rate(self) -> float:
        return self.successes / self.attempts if self.attempts else 0.0

    @property
    def is_reliable(self) -> bool:
        return self.hot_samples >= 3 and self.ewma_success >= 0.6


@dataclass
class CycleRecord:
    """Outcome of one full farming cycle."""

    index: int
    hero: str = ""
    duration: float = 0.0
    tickets: list[str] = field(default_factory=list)
    victory: bool = False
    completed: bool = False
    error: str = ""
    ended_at: float = field(default_factory=time.time)


# --------------------------------------------------------------------------------------
# The brain
# --------------------------------------------------------------------------------------


class LearningStore:
    """Persistent adaptive memory. Thread-safe enough for the single writer here."""

    def __init__(self, path: str = "data/brain.json", autosave: bool = True) -> None:
        self.path = path
        self.autosave = autosave
        self.intents: dict[str, IntentStats] = {}
        self.cycles: list[CycleRecord] = []
        self.counters: dict[str, int] = {}
        self.state_history: deque[tuple[str, float]] = deque(maxlen=64)
        self._dirty = False
        self.load()

    # -- persistence ---------------------------------------------------------------

    def load(self) -> bool:
        if not os.path.exists(self.path):
            return False
        try:
            with open(self.path, encoding="utf-8") as fh:
                blob = json.load(fh)
        except Exception:
            log.warning("brain file unreadable, starting fresh: %s", self.path, exc_info=True)
            return False
        if blob.get("schema") != SCHEMA_VERSION:
            log.info("brain schema %s != %s — discarding stale memory",
                     blob.get("schema"), SCHEMA_VERSION)
            return False
        self.intents = {k: IntentStats(**v) for k, v in blob.get("intents", {}).items()}
        self.cycles = [CycleRecord(**c) for c in blob.get("cycles", [])]
        self.counters = dict(blob.get("counters", {}))
        log.info("brain loaded: %d intents, %d cycles", len(self.intents), len(self.cycles))
        return True

    def save(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        blob = {
            "schema": SCHEMA_VERSION,
            "saved_at": time.time(),
            "intents": {k: asdict(v) for k, v in self.intents.items()},
            "cycles": [asdict(c) for c in self.cycles[-200:]],
            "counters": self.counters,
        }
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(blob, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self._dirty = False
        except Exception:  # pragma: no cover - disk failure
            log.error("failed to persist brain", exc_info=True)
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _touch(self) -> None:
        self._dirty = True
        if self.autosave:
            self.save()

    # -- intent learning -----------------------------------------------------------

    def stats(self, intent: str) -> IntentStats:
        return self.intents.setdefault(intent, IntentStats())

    def record_hit(
        self,
        intent: str,
        center: tuple[int, int] | None = None,
        screen: tuple[int, int] | None = None,
        latency: float = 0.0,
    ) -> None:
        """A semantic lookup succeeded — learn where and how fast."""
        st = self.stats(intent)
        st.record(True, latency)
        if center and screen and screen[0] > 0 and screen[1] > 0:
            st.record_position(center[0] / screen[0], center[1] / screen[1])
        self._touch()

    def record_miss(self, intent: str) -> None:
        self.stats(intent).record(False)
        self._touch()

    def hot_region(
        self,
        intent: str,
        screen: tuple[int, int],
        pad_ratio: float = 0.16,
    ):
        """Region around the learned prior for ``intent``, or ``None`` if not confident."""
        st = self.intents.get(intent)
        if st is None or not st.is_reliable:
            return None
        try:
            from vision import Region
        except Exception:  # pragma: no cover
            from scripts.vision import Region  # type: ignore
        sw, sh = screen
        pw, ph = int(sw * pad_ratio), int(sh * pad_ratio)
        cx, cy = int(st.hot_x * sw), int(st.hot_y * sh)
        left = max(0, cx - pw)
        top = max(0, cy - ph)
        return Region(left, top, min(sw - left, pw * 2), min(sh - top, ph * 2))

    def suggest_timeout(self, intent: str, default: float, floor: float = 3.0) -> float:
        """Adaptive wait: generous while unproven, tightened once latency is known."""
        st = self.intents.get(intent)
        if st is None or st.successes < 5 or st.avg_latency <= 0:
            return default
        return max(floor, min(default, st.avg_latency * 3.0 + 2.0))

    # -- counters & cycles ---------------------------------------------------------

    def bump(self, key: str, amount: int = 1) -> int:
        self.counters[key] = self.counters.get(key, 0) + amount
        self._touch()
        return self.counters[key]

    def record_cycle(self, record: CycleRecord) -> None:
        self.cycles.append(record)
        self.bump("cycles_total")
        if record.completed:
            self.bump("cycles_completed")
        if record.victory:
            self.bump("victories")
        for ticket in record.tickets:
            self.bump(f"ticket:{ticket}")
        self._touch()

    def tickets_earned(self) -> dict[str, int]:
        return {k.split(":", 1)[1]: v for k, v in self.counters.items() if k.startswith("ticket:")}

    # -- stuck detection -----------------------------------------------------------

    def note_state(self, state: str, now: float | None = None) -> None:
        self.state_history.append((str(state), now if now is not None else time.time()))

    def time_in_state(self, now: float | None = None) -> float:
        """Seconds spent continuously in the most recent state."""
        if not self.state_history:
            return 0.0
        now = now if now is not None else time.time()
        current = self.state_history[-1][0]
        start = self.state_history[-1][1]
        for state, ts in reversed(self.state_history):
            if state != current:
                break
            start = ts
        return max(0.0, now - start)

    def is_stuck(self, limit: float, now: float | None = None) -> bool:
        return self.time_in_state(now) > limit

    def is_flapping(self, window: int = 8, distinct_max: int = 2) -> bool:
        """Detect A-B-A-B oscillation between a couple of states."""
        if len(self.state_history) < window:
            return False
        recent = [s for s, _ in list(self.state_history)[-window:]]
        return len(set(recent)) <= distinct_max and len(set(recent)) > 1

    # -- reporting -----------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        done = [c for c in self.cycles if c.completed]
        avg = sum(c.duration for c in done) / len(done) if done else 0.0
        return {
            "cycles_total": self.counters.get("cycles_total", 0),
            "cycles_completed": self.counters.get("cycles_completed", 0),
            "victories": self.counters.get("victories", 0),
            "avg_cycle_seconds": round(avg, 1),
            "tickets": self.tickets_earned(),
            "intents_learned": sum(1 for s in self.intents.values() if s.is_reliable),
            "weakest_intents": sorted(
                ((k, round(v.ewma_success, 2)) for k, v in self.intents.items() if v.attempts >= 3),
                key=lambda kv: kv[1],
            )[:5],
        }

    def reset(self) -> None:
        self.intents.clear()
        self.cycles.clear()
        self.counters.clear()
        self.state_history.clear()
        self._touch()


__all__ = ["LearningStore", "IntentStats", "CycleRecord", "SCHEMA_VERSION"]
