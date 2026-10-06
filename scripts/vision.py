"""
vision.py — Cognitive Vision Engine ("True Eyes") for the Dark Carnival RPA agent.

Design goals
------------
* **Semantic, not blind.** Nothing in this module clicks fixed coordinates. The agent
  reads the screen with OCR, finds *meaning* (Russian words), and returns the bounding
  box of that meaning. Coordinates are always derived at runtime.
* **Russian-first.** EasyOCR is initialised with ``lang_list=['ru', 'en']``. All matching
  runs through a Cyrillic-aware normaliser that survives the classic OCR confusions
  (Latin/Cyrillic homoglyphs, ``ё``/``е``, broken kerning).
* **Hot-swappable.** No import-time side effects, no heavyweight work at module scope.
  Every expensive dependency (numpy, mss, easyocr, cv2) is imported lazily so this file
  can be reloaded by the GUI at any instant, and so the pure logic stays unit-testable
  on a headless CI box with none of those wheels installed.
* **Injectable.** ``OcrBackend`` / ``ScreenSource`` are protocols. Tests inject fakes;
  production injects EasyOCR + MSS.

This module is loaded as an external script by the thin loader (see ``main.py``).
"""

from __future__ import annotations

import difflib
import logging
import re
import time
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("dc.vision")

# --------------------------------------------------------------------------------------
# PHASE 2.3 — Russian keyword map.
# --------------------------------------------------------------------------------------
# Every semantic intent the agent can "look for" maps to a list of Russian (and a few
# English fallback) surface forms. The agent never searches for a button *image* for
# these: it searches for the words and clicks what it read.

RU_KEYWORDS: dict[str, list[str]] = {
    "accept": ["Принять", "Принять игру", "Accept"],
    "play": ["Найти игру", "Играть", "Find Match", "Play"],
    "continue": ["Продолжить", "Continue"],
    "close": ["Закрыть", "ОК", "OK", "Close"],
    "victory": ["Победа", "Victory"],
    "defeat": ["Поражение", "Defeat"],
    "safe_to_leave": [
        "Игру можно безопасно покинуть",
        "можно безопасно покинуть",
        "безопасно покинуть",
        "Safe to leave",
    ],
    "reconnect": ["Переподключиться", "Переподключение", "Reconnect"],
    "leave_game": ["Покинуть игру", "Покинуть", "Leave Game"],
    # Supporting vocabulary for the Dark Carnival co-op bot loop.
    "cancel_search": ["Отменить поиск", "Отменить", "Cancel"],
    "coop_bots": ["Против ботов", "Кооп. матч с ботами", "Co-op Bots"],
    "dark_carnival": ["Тёмный карнавал", "Темный карнавал", "Dark Carnival"],
    "search_hero": ["Поиск", "Поиск героя", "Search"],
    "lock_in": ["Выбрать", "Закрепить", "Lock In"],
    "ready": ["Готов", "Я готов", "Ready"],
    "disconnected": ["Соединение потеряно", "Нет соединения", "Disconnected"],
    "queue_found": ["Игра найдена", "Матч найден", "Match Found"],
    "claim_reward": ["Забрать", "Получить награду", "Забрать награду", "Claim"],
}

#: Intents whose phrasing is long enough that a looser fuzzy threshold is safe.
_LONG_PHRASE_INTENTS = frozenset({"safe_to_leave", "dark_carnival", "queue_found"})


# --------------------------------------------------------------------------------------
# Cyrillic-aware text normalisation
# --------------------------------------------------------------------------------------

# EasyOCR routinely swaps visually identical Latin glyphs into Cyrillic strings and vice
# versa ("Пpинять" with a Latin 'p'). Fold every homoglyph onto its Cyrillic twin so the
# comparison is apples to apples.
_HOMOGLYPHS = str.maketrans(
    {
        "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "y": "у", "x": "х",
        "A": "а", "B": "в", "E": "е", "K": "к", "M": "м", "H": "н", "O": "о",
        "P": "р", "C": "с", "T": "т", "X": "х", "Y": "у",
        "ё": "е", "Ё": "е", "й": "и", "Й": "и",
    }
)

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+", re.UNICODE)


def normalize_text(value: str) -> str:
    """Fold OCR output into a comparable canonical form.

    Lowercases, strips accents/punctuation, collapses whitespace and maps Latin
    homoglyphs onto their Cyrillic twins so ``"ПPИHЯTЬ!"`` == ``"принять"``.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    text = text.translate(_HOMOGLYPHS)
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


#: A matched word must make up at least this share of the observed caption before a
#: containment match is believed. Without it, «ОК» matches inside «ПОКинуть игру» and
#: «Поиск» matches inside «Отменить поиск» — both catastrophic misreads in this UI.
_DOMINANCE = 0.5

#: Per-token floor for multi-word phrases. «Принять игру» vs «Покинуть игру» share a
#: token but differ on the verb; demanding every token align kills that false positive.
_TOKEN_FLOOR = 0.72


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def text_similarity(observed: str, keyword: str) -> float:
    """Fuzzy similarity in ``[0.0, 1.0]`` between OCR output and a target keyword.

    Deliberately *not* a plain substring/ratio test. Dota's Russian UI is full of
    captions that overlap at the character level while meaning opposite things
    («Принять» vs «Покинуть»), so matching is done token-wise with a dominance rule.
    """
    na, nb = normalize_text(observed), normalize_text(keyword)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    tokens_a, tokens_b = na.split(), nb.split()

    if len(tokens_b) == 1:
        # Single-word keyword: allow it to sit inside a longer caption ("Принять 12"),
        # but only if it dominates what was read — otherwise it is an accident.
        best, best_token = 0.0, ""
        for token in tokens_a:
            score = _ratio(token, nb)
            if score > best:
                best, best_token = score, token
        if best >= _TOKEN_FLOOR and len(best_token) / len(na.replace(" ", "")) >= _DOMINANCE:
            return min(1.0, 0.95 * best)
        return _ratio(na, nb)

    # Multi-word keyword: slide a window of the same token count and require *every*
    # token to align, so one shared word cannot carry an otherwise wrong phrase.
    span = len(tokens_b)
    best_window = 0.0
    for start in range(0, max(0, len(tokens_a) - span) + 1):
        window = tokens_a[start:start + span]
        if len(window) < span:
            break
        ratios = [_ratio(w, k) for w, k in zip(window, tokens_b, strict=True)]
        if min(ratios) >= _TOKEN_FLOOR:
            best_window = max(best_window, sum(ratios) / span)
    if best_window:
        return min(1.0, 0.95 * best_window)

    # Token alignment failed. A raw character ratio is dangerous here — «Покинуть игру»
    # scores 0.80 against «Принять игру», which would make the agent abandon the match
    # instead of accepting it. Only a near-identical string (OCR merged two words, say)
    # is trusted; anything else is damped well below every threshold.
    fallback = _ratio(na, nb)
    return fallback if fallback >= 0.88 else fallback * 0.8


def best_keyword_score(observed: str, intent: str) -> tuple[float, str]:
    """Score ``observed`` OCR text against every surface form of ``intent``.

    Returns ``(score, matched_surface_form)``.
    """
    best, best_form = 0.0, ""
    for form in RU_KEYWORDS.get(intent, []):
        score = text_similarity(observed, form)
        if score > best:
            best, best_form = score, form
    return best, best_form


def threshold_for(intent: str, base: float = 0.78) -> float:
    """Longer phrases tolerate more OCR noise; short words must match tightly."""
    return 0.72 if intent in _LONG_PHRASE_INTENTS else base


# --------------------------------------------------------------------------------------
# Geometry primitives
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Region:
    """An axis-aligned screen region in absolute desktop pixels."""

    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def offset(self, dx: int, dy: int) -> Region:
        return Region(self.left + dx, self.top + dy, self.width, self.height)

    def scaled(self, fx: float, fy: float | None = None) -> Region:
        fy = fx if fy is None else fy
        return Region(
            int(self.left * fx), int(self.top * fy),
            int(self.width * fx), int(self.height * fy),
        )

    def as_dict(self) -> dict[str, int]:
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}


def relative_region(screen_w: int, screen_h: int, left: float, top: float,
                    width: float, height: float) -> Region:
    """Build a Region from *fractional* screen coordinates (resolution independent)."""
    return Region(int(screen_w * left), int(screen_h * top),
                  int(screen_w * width), int(screen_h * height))


@dataclass(frozen=True, slots=True)
class TextHit:
    """One OCR detection, already resolved to absolute desktop coordinates."""

    text: str
    region: Region
    confidence: float
    intent: str = ""
    score: float = 0.0
    matched_form: str = ""

    @property
    def center(self) -> tuple[int, int]:
        return self.region.center

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        x, y = self.center
        return f"<{self.intent or 'text'} {self.text!r} @({x},{y}) ocr={self.confidence:.2f} sim={self.score:.2f}>"


@dataclass(frozen=True, slots=True)
class TemplateHit:
    """One template (emoji / portrait) match."""

    key: str
    region: Region
    score: float

    @property
    def center(self) -> tuple[int, int]:
        return self.region.center


# --------------------------------------------------------------------------------------
# Backends (protocol-ish; duck-typed so tests can inject trivial fakes)
# --------------------------------------------------------------------------------------


class ScreenSource:
    """Captures the desktop. Production implementation uses MSS."""

    def grab(self, region: Region | None = None) -> Any:  # pragma: no cover - interface
        raise NotImplementedError

    def size(self) -> tuple[int, int]:  # pragma: no cover - interface
        raise NotImplementedError


class MssScreen(ScreenSource):
    """Fast desktop capture via ``mss``; returns an RGB numpy array."""

    def __init__(self, monitor: int = 1) -> None:
        self._monitor_index = monitor
        self._sct = None
        self._size: tuple[int, int] | None = None

    def _ensure(self):
        if self._sct is None:
            import mss  # lazy: not needed on CI

            self._sct = mss.mss()
        return self._sct

    def _monitor(self) -> dict:
        sct = self._ensure()
        mons = sct.monitors
        idx = self._monitor_index if self._monitor_index < len(mons) else 0
        return mons[idx]

    def size(self) -> tuple[int, int]:
        if self._size is None:
            m = self._monitor()
            self._size = (int(m["width"]), int(m["height"]))
        return self._size

    def grab(self, region: Region | None = None):
        import numpy as np  # lazy

        sct = self._ensure()
        mon = self._monitor()
        box = region.as_dict() if region else {
            "left": mon["left"], "top": mon["top"],
            "width": mon["width"], "height": mon["height"],
        }
        raw = sct.grab(box)
        frame = np.asarray(raw)  # BGRA
        return frame[:, :, :3][:, :, ::-1].copy()  # -> RGB

    def close(self) -> None:
        if self._sct is not None:  # pragma: no cover - teardown
            try:
                self._sct.close()
            finally:
                self._sct = None


class OcrBackend:
    """Reads text from an image. Returns ``(text, (l, t, w, h), confidence)`` triples."""

    def read(self, image: Any) -> list[tuple[str, tuple[int, int, int, int], float]]:
        raise NotImplementedError  # pragma: no cover - interface


class EasyOcrBackend(OcrBackend):
    """EasyOCR wrapper pinned to Russian + English.

    The reader is built on first use (model download / GPU warm-up is slow) and then
    cached on the instance, so a hot-reload of this module does not re-download models
    as long as the caller keeps the same backend object alive.
    """

    _shared_reader: Any = None  # survives module reloads when re-attached by the host

    def __init__(self, lang_list: Sequence[str] | None = None, gpu: bool = True) -> None:
        self.lang_list = list(lang_list or ["ru", "en"])
        self.gpu = gpu
        self._reader: Any = None

    @property
    def reader(self):
        if self._reader is None:
            if EasyOcrBackend._shared_reader is not None:
                self._reader = EasyOcrBackend._shared_reader
            else:
                import easyocr  # lazy, heavy

                log.info("Initialising EasyOCR reader lang_list=%s gpu=%s", self.lang_list, self.gpu)
                t0 = time.perf_counter()
                try:
                    self._reader = easyocr.Reader(self.lang_list, gpu=self.gpu)
                except Exception:  # pragma: no cover - CUDA missing etc.
                    log.warning("EasyOCR GPU init failed; falling back to CPU", exc_info=True)
                    self._reader = easyocr.Reader(self.lang_list, gpu=False)
                log.info("EasyOCR ready in %.1fs", time.perf_counter() - t0)
                EasyOcrBackend._shared_reader = self._reader
        return self._reader

    def read(self, image: Any) -> list[tuple[str, tuple[int, int, int, int], float]]:
        results = self.reader.readtext(image, detail=1, paragraph=False)
        out: list[tuple[str, tuple[int, int, int, int], float]] = []
        for box, text, conf in results:
            xs = [int(p[0]) for p in box]
            ys = [int(p[1]) for p in box]
            left, top = min(xs), min(ys)
            out.append((text, (left, top, max(xs) - left, max(ys) - top), float(conf)))
        return out


# --------------------------------------------------------------------------------------
# Frame preprocessing — makes Dota's stylised Cyrillic legible to the OCR net
# --------------------------------------------------------------------------------------


def preprocess(image: Any, upscale: float = 1.6) -> Any:
    """Upscale + CLAHE + mild denoise. Returns the input untouched if cv2 is absent."""
    try:
        import cv2
        import numpy as np
    except Exception:  # pragma: no cover - cv2 optional
        return image
    try:
        img = np.asarray(image)
        if upscale and upscale != 1.0:
            img = cv2.resize(img, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img
        gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
    except Exception:  # pragma: no cover - never let preprocessing kill a cycle
        log.debug("preprocess failed; using raw frame", exc_info=True)
        return image


# --------------------------------------------------------------------------------------
# The Vision engine
# --------------------------------------------------------------------------------------


@dataclass
class VisionConfig:
    ocr_confidence: float = 0.35       # discard OCR detections below this
    match_threshold: float = 0.78      # fuzzy similarity needed to accept an intent
    template_threshold: float = 0.80   # normalised cross-correlation for emoji/portrait
    cache_ttl: float = 0.45            # seconds a full-screen read stays reusable
    upscale: float = 1.6
    assets_dir: str = "assets"


class Vision:
    """Semantic screen reader.

    Typical use::

        v = Vision()
        hit = v.find_intent("accept")      # OCR the screen, locate "Принять"
        if hit: input_handler.click(*hit.center)
    """

    def __init__(
        self,
        screen: ScreenSource | None = None,
        ocr: OcrBackend | None = None,
        config: VisionConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config or VisionConfig()
        self.screen = screen if screen is not None else MssScreen()
        self.ocr = ocr if ocr is not None else EasyOcrBackend(["ru", "en"])
        self._clock = clock
        self._cache: tuple[float, tuple[int, int, int, int] | None, list[TextHit]] | None = None
        self._template_cache: dict[str, Any] = {}
        self.last_read: list[TextHit] = []

    # -- raw reading ---------------------------------------------------------------

    def invalidate_cache(self) -> None:
        self._cache = None

    def read_screen(self, region: Region | None = None, *, fresh: bool = False) -> list[TextHit]:
        """OCR a region (or the whole desktop) and return absolute-coordinate hits."""
        key = (region.left, region.top, region.width, region.height) if region else None
        now = self._clock()
        if not fresh and self._cache is not None:
            ts, cached_key, hits = self._cache
            if cached_key == key and (now - ts) <= self.config.cache_ttl:
                return hits

        frame = self.screen.grab(region)
        scale = self.config.upscale or 1.0
        processed = preprocess(frame, upscale=scale)
        # preprocess() is a no-op without cv2 — detect that and keep coordinates honest.
        if processed is frame:
            scale = 1.0

        ox = region.left if region else 0
        oy = region.top if region else 0

        hits: list[TextHit] = []
        for text, box, conf in self.ocr.read(processed):
            if conf < self.config.ocr_confidence:
                continue
            bl, bt, bw, bh = box
            hits.append(
                TextHit(
                    text=str(text),
                    region=Region(int(bl / scale) + ox, int(bt / scale) + oy,
                                  max(1, int(bw / scale)), max(1, int(bh / scale))),
                    confidence=float(conf),
                )
            )

        self._cache = (now, key, hits)
        self.last_read = hits
        return hits

    # -- semantic querying ---------------------------------------------------------

    def find_intent(
        self,
        intent: str,
        region: Region | None = None,
        *,
        fresh: bool = False,
        threshold: float | None = None,
    ) -> TextHit | None:
        """Find the best on-screen match for a semantic intent (e.g. ``"accept"``)."""
        hits = self.find_all_intents([intent], region=region, fresh=fresh, threshold=threshold)
        return hits[0] if hits else None

    def find_all_intents(
        self,
        intents: Iterable[str],
        region: Region | None = None,
        *,
        fresh: bool = False,
        threshold: float | None = None,
    ) -> list[TextHit]:
        """Return every matching hit for ``intents``, best score first."""
        words = self.read_screen(region, fresh=fresh)
        intents = list(intents)
        scored: list[TextHit] = []
        for intent in intents:
            limit = threshold if threshold is not None else threshold_for(intent, self.config.match_threshold)
            for hit in words:
                score, form = best_keyword_score(hit.text, intent)
                if score >= limit:
                    scored.append(
                        TextHit(hit.text, hit.region, hit.confidence,
                                intent=intent, score=score, matched_form=form)
                    )
        scored.sort(key=lambda h: (h.score, h.confidence), reverse=True)
        return scored

    def detect_state_keywords(self, region: Region | None = None, *, fresh: bool = False) -> dict[str, TextHit]:
        """One OCR pass → best hit per known intent. The state machine's primary sensor."""
        hits = self.find_all_intents(RU_KEYWORDS.keys(), region=region, fresh=fresh)
        best: dict[str, TextHit] = {}
        for hit in hits:
            cur = best.get(hit.intent)
            if cur is None or hit.score > cur.score:
                best[hit.intent] = hit
        return best

    def find_text(
        self,
        needle: str,
        region: Region | None = None,
        *,
        fresh: bool = False,
        threshold: float = 0.80,
    ) -> TextHit | None:
        """Find arbitrary literal text (e.g. a localized hero name) on screen."""
        best: TextHit | None = None
        for hit in self.read_screen(region, fresh=fresh):
            score = text_similarity(hit.text, needle)
            if score >= threshold and (best is None or score > best.score):
                best = TextHit(hit.text, hit.region, hit.confidence,
                               intent="literal", score=score, matched_form=needle)
        return best

    def wait_for_intent(
        self,
        intent: str,
        timeout: float = 15.0,
        poll: float = 0.6,
        region: Region | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> TextHit | None:
        """Block until ``intent`` appears on screen or ``timeout`` elapses."""
        deadline = self._clock() + timeout
        while True:
            hit = self.find_intent(intent, region=region, fresh=True)
            if hit:
                return hit
            if self._clock() >= deadline:
                return None
            sleep(poll)

    def wait_for_any(
        self,
        intents: Sequence[str],
        timeout: float = 15.0,
        poll: float = 0.6,
        region: Region | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> TextHit | None:
        """Block until any of ``intents`` appears; returns the strongest match."""
        deadline = self._clock() + timeout
        while True:
            hits = self.find_all_intents(intents, region=region, fresh=True)
            if hits:
                return hits[0]
            if self._clock() >= deadline:
                return None
            sleep(poll)

    # -- template matching (Phase 3: emojis + portraits) ---------------------------

    def _load_template(self, path: str):
        import cv2  # lazy

        tpl = self._template_cache.get(path)
        if tpl is None:
            tpl = cv2.imread(path, cv2.IMREAD_COLOR)
            if tpl is None:
                raise FileNotFoundError(f"template not found: {path}")
            self._template_cache[path] = tpl
        return tpl

    def clear_template_cache(self) -> None:
        self._template_cache.clear()

    def find_template(
        self,
        path: str,
        region: Region | None = None,
        *,
        threshold: float | None = None,
        key: str = "",
    ) -> TemplateHit | None:
        """Locate a sprite (hero emoji or 3D portrait) via normalised cross-correlation."""
        import cv2
        import numpy as np

        limit = self.config.template_threshold if threshold is None else threshold
        try:
            tpl = self._load_template(path)
        except FileNotFoundError:
            log.warning("missing template asset: %s", path)
            return None

        frame = np.asarray(self.screen.grab(region))
        if frame.ndim == 3 and frame.shape[2] == 3:
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        if frame.shape[0] < tpl.shape[0] or frame.shape[1] < tpl.shape[1]:
            return None

        res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score < limit:
            return None
        ox = region.left if region else 0
        oy = region.top if region else 0
        return TemplateHit(
            key=key or path,
            region=Region(loc[0] + ox, loc[1] + oy, tpl.shape[1], tpl.shape[0]),
            score=float(score),
        )

    def find_templates(
        self,
        paths: dict[str, str],
        region: Region | None = None,
        *,
        threshold: float | None = None,
    ) -> list[TemplateHit]:
        """Batch template search; returns matches sorted by score descending."""
        out = [h for k, p in paths.items()
               if (h := self.find_template(p, region, threshold=threshold, key=k))]
        out.sort(key=lambda h: h.score, reverse=True)
        return out

    # -- diagnostics ---------------------------------------------------------------

    def describe_screen(self, region: Region | None = None) -> str:
        """Human-readable dump of what the eyes currently see (Dev Console helper)."""
        hits = self.read_screen(region, fresh=True)
        if not hits:
            return "(no text detected)"
        lines = []
        for h in sorted(hits, key=lambda h: (h.region.top, h.region.left)):
            x, y = h.center
            score, intent = 0.0, ""
            for name in RU_KEYWORDS:
                s, _ = best_keyword_score(h.text, name)
                if s > score:
                    score, intent = s, name
            tag = f"  ~{intent} ({score:.2f})" if score >= 0.70 else ""
            lines.append(f"[{h.confidence:.2f}] ({x:>5},{y:>5}) {h.text!r}{tag}")
        return "\n".join(lines)


__all__ = [
    "RU_KEYWORDS", "Region", "TextHit", "TemplateHit", "Vision", "VisionConfig",
    "ScreenSource", "MssScreen", "OcrBackend", "EasyOcrBackend",
    "normalize_text", "text_similarity", "best_keyword_score", "threshold_for",
    "relative_region", "preprocess",
]
