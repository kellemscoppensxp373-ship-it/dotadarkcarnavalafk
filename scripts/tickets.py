"""
tickets.py — Экономика билетов «Тёмного карнавала» (11 арканов Таро).

Как устроено событие
--------------------
В интерфейсе события каждый аркан (ШУТ, МАГ, ВЕРХОВНАЯ ЖРИЦА, …) — это отдельная
панель из ТРЁХ секций:

    ┌── СЕКЦИЯ ×1 ──┐   много героев   → игра на них даёт 1 билет этого аркана
    ├── СЕКЦИЯ ×2 ──┤   меньше героев  → 2 билета
    └── СЕКЦИЯ ×3 ──┘   обычно 1 герой → 3 билета   ← ТОЛЬКО ЭТО НАМ НУЖНО

Бот фармит ×3 и только ×3: одна игра на «тройном» герое стоит ровно столько же
времени, сколько игра на «одиночном», но приносит втрое больше. Поэтому
``min_yield`` по умолчанию равен 3, а герои с меньшей отдачей просто не
рассматриваются планировщиком.

Почему это файл-конфиг, а не захардкоженная таблица
---------------------------------------------------
Состав героев в секциях Valve меняет между патчами события, и опознать героя по
пиксельной иконке 35×35 автоматически невозможно. Поэтому соответствие
«аркан → герой ×3» живёт в редактируемом JSON (``data/tickets.json``) и
правится прямо в GUI, на вкладке «Билеты». Логика ниже ничего не знает о
конкретных героях — она работает с тем, что указал оператор.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field

log = logging.getLogger("dc.tickets")

SCHEMA_VERSION = 1

#: 11 арканов события. ``order`` — канонический номер Старшего аркана Таро,
#: по нему панели сортируются так же, как в самой игре.
ARCANA: dict[str, dict] = {
    "jester":         {"name_ru": "ШУТ",              "order": 0},
    "magician":       {"name_ru": "МАГ",              "order": 1},
    "high_priestess": {"name_ru": "ВЕРХОВНАЯ ЖРИЦА",  "order": 2},
    "emperor":        {"name_ru": "ИМПЕРАТОР",        "order": 4},
    "lovers":         {"name_ru": "ВЛЮБЛЁННЫЕ",       "order": 6},
    "strength":       {"name_ru": "СИЛА",             "order": 8},
    "hermit":         {"name_ru": "ОТШЕЛЬНИК",        "order": 9},
    "wheel":          {"name_ru": "КОЛЕСО ФОРТУНЫ",   "order": 10},
    "death":          {"name_ru": "СМЕРТЬ",           "order": 13},
    "devil":          {"name_ru": "ДЬЯВОЛ",           "order": 15},
    "star":           {"name_ru": "ЗВЕЗДА",           "order": 17},
}

#: Поддерживаемая отдача билетов за одну игру.
YIELDS: tuple[int, ...] = (3, 2, 1)

#: Отдача, ниже которой играть бессмысленно (требование: «3 в приоритете, меньше не надо»).
DEFAULT_MIN_YIELD = 3


def arcana_order() -> list[str]:
    """Ключи арканов в порядке интерфейса события."""
    return sorted(ARCANA, key=lambda k: ARCANA[k]["order"])


def arcana_name_ru(key: str) -> str:
    return ARCANA.get(key, {}).get("name_ru", key)


def find_arcana_by_ru(name: str, min_score: float = 0.78) -> str | None:
    """Опознать аркан по заголовку панели, прочитанному OCR («ВЕРХОВНАЯ ЖРИЦА»)."""
    try:
        from vision import text_similarity
    except Exception:  # pragma: no cover - автономный режим
        from difflib import SequenceMatcher

        def text_similarity(a: str, b: str) -> float:
            return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()

    best, best_score = None, 0.0
    for key in ARCANA:
        score = text_similarity(name, ARCANA[key]["name_ru"])
        if score > best_score:
            best, best_score = key, score
    return best if best_score >= min_score else None


# --------------------------------------------------------------------------------------
# Книга билетов (редактируемая таблица «аркан → герои по отдаче»)
# --------------------------------------------------------------------------------------


@dataclass
class TicketBook:
    """Таблица соответствий «аркан → герои», сохраняемая в JSON.

    Структура повторяет интерфейс события один-в-один, чтобы её можно было
    заполнять, просто глядя на экран:

    ``{"jester": {3: ["Фантом Ассасин"], 2: [...], 1: [...]}, ...}``
    """

    path: str = "data/tickets.json"
    min_yield: int = DEFAULT_MIN_YIELD
    table: dict[str, dict[int, list[str]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key in ARCANA:
            self.table.setdefault(key, {})
            for y in YIELDS:
                self.table[key].setdefault(y, [])

    # -- хранилище -----------------------------------------------------------------

    @classmethod
    def load(cls, path: str = "data/tickets.json") -> TicketBook:
        book = cls(path=path)
        if not os.path.exists(path):
            log.info("таблица билетов не найдена, создаётся пустая: %s", path)
            book.save()
            return book
        try:
            with open(path, encoding="utf-8") as fh:
                blob = json.load(fh)
        except Exception:
            log.warning("не удалось прочитать %s, используется пустая таблица", path, exc_info=True)
            return book
        book.min_yield = int(blob.get("min_yield", DEFAULT_MIN_YIELD))
        for key, section in (blob.get("arcana") or {}).items():
            if key not in ARCANA:
                continue
            for y in YIELDS:
                names = section.get(f"x{y}") or []
                book.table[key][y] = [str(n).strip() for n in names if str(n).strip()]
        return book

    def save(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        blob = {
            "schema": SCHEMA_VERSION,
            "min_yield": self.min_yield,
            "_комментарий": (
                "Для каждого аркана укажите героев из соответствующей секции панели "
                "события. x3 — нижняя секция (3 билета), именно она используется ботом."
            ),
            "arcana": {
                key: {
                    "name_ru": ARCANA[key]["name_ru"],
                    **{f"x{y}": self.table[key][y] for y in YIELDS},
                }
                for key in arcana_order()
            },
        }
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(blob, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:  # pragma: no cover - сбой диска
            log.error("не удалось сохранить таблицу билетов", exc_info=True)
            if os.path.exists(tmp):
                os.unlink(tmp)

    # -- чтение --------------------------------------------------------------------

    def heroes_for(self, arcana: str, yield_: int = 3) -> list[str]:
        """Имена героев, дающих ровно ``yield_`` билетов этого аркана."""
        return list(self.table.get(arcana, {}).get(yield_, []))

    def candidates(self, arcana: str, min_yield: int | None = None) -> list[tuple[str, int]]:
        """``[(имя героя, отдача), …]`` для аркана, от большей отдачи к меньшей."""
        limit = self.min_yield if min_yield is None else min_yield
        out: list[tuple[str, int]] = []
        for y in YIELDS:
            if y < limit:
                continue
            out.extend((name, y) for name in self.heroes_for(arcana, y))
        return out

    def yield_of(self, hero_name: str, arcana: str) -> int:
        """Сколько билетов этого аркана даёт герой (0 — если не числится в секциях)."""
        try:
            from vision import text_similarity
        except Exception:  # pragma: no cover
            from difflib import SequenceMatcher

            def text_similarity(a: str, b: str) -> float:
                return SequenceMatcher(None, a.lower(), b.lower()).ratio()

        for y in YIELDS:
            for name in self.heroes_for(arcana, y):
                if text_similarity(hero_name, name) >= 0.88:
                    return y
        return 0

    def configured(self, min_yield: int | None = None) -> list[str]:
        """Арканы, для которых оператор уже указал героя нужной отдачи."""
        limit = self.min_yield if min_yield is None else min_yield
        return [k for k in arcana_order() if self.candidates(k, limit)]

    def missing(self, min_yield: int | None = None) -> list[str]:
        """Арканы, по которым бот работать не сможет — таблица не заполнена."""
        limit = self.min_yield if min_yield is None else min_yield
        return [k for k in arcana_order() if not self.candidates(k, limit)]

    def is_empty(self) -> bool:
        return not any(self.candidates(k, 1) for k in ARCANA)

    # -- запись --------------------------------------------------------------------

    def set_heroes(self, arcana: str, yield_: int, names: list[str]) -> None:
        if arcana not in ARCANA:
            raise KeyError(f"неизвестный аркан: {arcana!r}")
        if yield_ not in YIELDS:
            raise ValueError(f"отдача должна быть одной из {YIELDS}, получено {yield_}")
        self.table[arcana][yield_] = [n.strip() for n in names if n and n.strip()]

    def add_hero(self, arcana: str, yield_: int, name: str) -> None:
        current = self.heroes_for(arcana, yield_)
        if name.strip() and name.strip() not in current:
            self.set_heroes(arcana, yield_, [*current, name])

    def summary(self) -> str:
        lines = []
        for key in arcana_order():
            x3 = ", ".join(self.heroes_for(key, 3)) or "— не заполнено —"
            lines.append(f"{ARCANA[key]['name_ru']:<18} ×3: {x3}")
        return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Цель по билетам и планировщик
# --------------------------------------------------------------------------------------


@dataclass
class TicketGoal:
    """Сколько билетов каждого аркана нужно оператору и сколько уже получено."""

    target: dict[str, int] = field(default_factory=dict)
    owned: dict[str, int] = field(default_factory=dict)

    def remaining(self) -> dict[str, int]:
        return {k: max(0, v - self.owned.get(k, 0)) for k, v in self.target.items()}

    def satisfied(self) -> bool:
        return all(v == 0 for v in self.remaining().values())

    def credit(self, arcana: str, count: int = 1) -> None:
        self.owned[arcana] = self.owned.get(arcana, 0) + int(count)

    def need(self, arcana: str) -> int:
        return self.remaining().get(arcana, 0)

    def progress(self) -> str:
        if not self.target:
            return "цель не задана — фарм без ограничений"
        parts = []
        for key in arcana_order():
            if key in self.target:
                parts.append(f"{arcana_name_ru(key)}: "
                             f"{self.owned.get(key, 0)}/{self.target[key]}")
        return " · ".join(parts)


@dataclass(frozen=True)
class Pick:
    """Решение планировщика на один цикл."""

    hero_name: str
    arcana: str
    yield_: int

    @property
    def arcana_ru(self) -> str:
        return arcana_name_ru(self.arcana)

    def describe(self) -> str:
        return (f"{self.hero_name} → {self.yield_} бил. "
                f"аркана «{self.arcana_ru}»")


def plan_next_pick(
    book: TicketBook,
    goal: TicketGoal,
    *,
    min_yield: int | None = None,
    avoid: list[str] | None = None,
    rotation: int = 0,
) -> Pick | None:
    """Выбрать героя на следующий цикл.

    Стратегия: взять аркан с наибольшей недостачей и сыграть на герое с
    максимальной отдачей (по умолчанию — только ×3). Если цель не задана,
    фармятся по кругу все заполненные арканы, чтобы прогресс шёл равномерно.

    Возвращает ``None``, если таблица билетов не заполнена — звать бота в этом
    случае бессмысленно, и executor честно об этом сообщит.
    """
    limit = book.min_yield if min_yield is None else min_yield
    avoid = list(avoid or [])

    ranked: list[str]
    remaining = goal.remaining()
    if goal.target and not goal.satisfied():
        ranked = sorted(
            (k for k, v in remaining.items() if v > 0),
            key=lambda k: (-remaining[k], ARCANA.get(k, {}).get("order", 99)),
        )
    else:
        # Цели нет (или она достигнута) — равномерная ротация по заполненным арканам.
        configured = book.configured(limit)
        if not configured:
            return None
        ranked = configured[rotation % len(configured):] + configured[:rotation % len(configured)]

    for arcana in ranked:
        candidates = [c for c in book.candidates(arcana, limit) if c[0] not in avoid]
        if not candidates:
            continue
        # Герои одной отдачи чередуются, чтобы не играть 50 игр подряд на одном.
        best_yield = candidates[0][1]
        same = [c for c in candidates if c[1] == best_yield]
        name, yield_ = same[rotation % len(same)]
        return Pick(hero_name=name, arcana=arcana, yield_=yield_)
    return None


__all__ = [
    "ARCANA", "YIELDS", "DEFAULT_MIN_YIELD", "SCHEMA_VERSION",
    "TicketBook", "TicketGoal", "Pick", "plan_next_pick",
    "arcana_order", "arcana_name_ru", "find_arcana_by_ru",
]
