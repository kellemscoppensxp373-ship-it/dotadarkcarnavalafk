"""
dota_logic.py — Domain knowledge for the Dark Carnival co-op-bots farming loop.

Contains three things and nothing else (no I/O, no OCR, no clicking — this file is pure
logic and is therefore 100% unit-testable):

1. **HERO_DB** — реестр героев с двумя наборами спрайтов: пиксельная иконка (экран
   наград) и 3D-портрет (сетка выбора), плюс русское имя для поля поиска.
2. **Экономика билетов** — вынесена в ``tickets.py`` (11 арканов, отдача ×1/×2/×3)
   и реэкспортируется отсюда. Приоритет — только ×3.
3. **Машина состояний** — цикл автоматизации и русские слова-доказательства
   каждого состояния.

Resolution independence: all screen regions are expressed as fractions of the client
size and materialised at runtime via ``vision.relative_region``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

# --------------------------------------------------------------------------------------
# Экономика билетов — вынесена в tickets.py (11 арканов, отдача ×1/×2/×3)
# --------------------------------------------------------------------------------------
# Реэкспорт, чтобы остальной код (и внешние скрипты) обращались к одному модулю.
from tickets import (  # noqa: E402  (внешний скрипт-сосед, загружается ScriptHost)
    ARCANA,
    DEFAULT_MIN_YIELD,
    YIELDS,
    Pick,
    TicketBook,
    TicketGoal,
    arcana_name_ru,
    arcana_order,
    find_arcana_by_ru,
    plan_next_pick,
)

# --------------------------------------------------------------------------------------
# ЭТАП 3 — РЕЕСТР ГЕРОЕВ С ДВУМЯ НАБОРАМИ СПРАЙТОВ
# --------------------------------------------------------------------------------------
#
#   "emoji_img"    -> пиксельная иконка — экран наград/билетов
#   "portrait_img" -> 3D-портрет        -> сетка выбора героя
#   "name_ru"      -> официальная русская локализация (вставляется в поиск героя)
#
# Файлы лежат в assets/emoji/<emoji_img> и assets/portraits/<portrait_img>.
#
# ВАЖНО: эта таблица — только реестр спрайтов и имён. Сколько билетов даёт герой,
# здесь НЕ хранится: это зависит от аркана и задаётся оператором в data/tickets.json
# (вкладка «Билеты»), потому что состав секций меняется между патчами события.

HERO_DB: dict[str, dict] = {
    "phantom_assassin": {
        "name_ru": "Фантом Ассасин",
        "aliases_ru": ["Фантомка", "ФА"],
        "emoji_img": "emoji_pa.png",
        "portrait_img": "portrait_pa.png",
        "role": "carry", "bot_difficulty": "easy",
    },
    "juggernaut": {
        "name_ru": "Джаггернаут", "aliases_ru": ["Джаг"],
        "emoji_img": "emoji_jugg.png", "portrait_img": "portrait_jugg.png",
        "role": "carry", "bot_difficulty": "easy",
    },
    "lina": {
        "name_ru": "Лина", "aliases_ru": [],
        "emoji_img": "emoji_lina.png", "portrait_img": "portrait_lina.png",
        "role": "mid", "bot_difficulty": "easy",
    },
    "lion": {
        "name_ru": "Лион", "aliases_ru": [],
        "emoji_img": "emoji_lion.png", "portrait_img": "portrait_lion.png",
        "role": "support", "bot_difficulty": "easy",
    },
    "crystal_maiden": {
        "name_ru": "Кристал Мейден", "aliases_ru": ["Кристальная дева", "КМ"],
        "emoji_img": "emoji_cm.png", "portrait_img": "portrait_cm.png",
        "role": "support", "bot_difficulty": "easy",
    },
    "sniper": {
        "name_ru": "Снайпер", "aliases_ru": [],
        "emoji_img": "emoji_sniper.png", "portrait_img": "portrait_sniper.png",
        "role": "carry", "bot_difficulty": "easy",
    },
    "ursa": {
        "name_ru": "Урса", "aliases_ru": [],
        "emoji_img": "emoji_ursa.png", "portrait_img": "portrait_ursa.png",
        "role": "carry", "bot_difficulty": "medium",
    },
    "lycan": {
        "name_ru": "Ликан", "aliases_ru": ["Ликантроп"],
        "emoji_img": "emoji_lycan.png", "portrait_img": "portrait_lycan.png",
        "role": "offlane", "bot_difficulty": "medium",
    },
    "axe": {
        "name_ru": "Акс", "aliases_ru": ["Топор"],
        "emoji_img": "emoji_axe.png", "portrait_img": "portrait_axe.png",
        "role": "offlane", "bot_difficulty": "easy",
    },
    "shadow_fiend": {
        "name_ru": "Шэдоу Филд", "aliases_ru": ["Невермор", "СФ"],
        "emoji_img": "emoji_sf.png", "portrait_img": "portrait_sf.png",
        "role": "mid", "bot_difficulty": "medium",
    },
    "wraith_king": {
        "name_ru": "Призрачный Король", "aliases_ru": ["ВК", "Скелет"],
        "emoji_img": "emoji_wk.png", "portrait_img": "portrait_wk.png",
        "role": "carry", "bot_difficulty": "easy",
    },
    "zeus": {
        "name_ru": "Зевс", "aliases_ru": [],
        "emoji_img": "emoji_zeus.png", "portrait_img": "portrait_zeus.png",
        "role": "mid", "bot_difficulty": "easy",
    },
}


def _slug(name: str) -> str:
    """Служебный ключ для героя, которого нет в реестре спрайтов."""
    return "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower()).strip("_")


@dataclass(frozen=True, slots=True)
class Hero:
    """Герой: русское имя для поиска + (опционально) спрайты."""

    key: str
    name_ru: str
    emoji_img: str = ""
    portrait_img: str = ""
    aliases_ru: tuple[str, ...] = ()
    role: str = "unknown"
    bot_difficulty: str = "easy"

    @property
    def all_names_ru(self) -> tuple[str, ...]:
        return (self.name_ru, *self.aliases_ru)

    @property
    def has_assets(self) -> bool:
        return bool(self.emoji_img and self.portrait_img)

    def emoji_path(self, assets_dir: str = "assets") -> str:
        return f"{assets_dir}/emoji/{self.emoji_img}" if self.emoji_img else ""

    def portrait_path(self, assets_dir: str = "assets") -> str:
        return f"{assets_dir}/portraits/{self.portrait_img}" if self.portrait_img else ""


def get_hero(key: str) -> Hero:
    """Герой из реестра по ключу."""
    try:
        row = HERO_DB[key]
    except KeyError as exc:
        raise KeyError(f"неизвестный герой {key!r}; известные: {sorted(HERO_DB)}") from exc
    return Hero(
        key=key,
        name_ru=row["name_ru"],
        emoji_img=row.get("emoji_img", ""),
        portrait_img=row.get("portrait_img", ""),
        aliases_ru=tuple(row.get("aliases_ru", ())),
        role=row.get("role", "unknown"),
        bot_difficulty=row.get("bot_difficulty", "easy"),
    )


def all_heroes() -> list[Hero]:
    return [get_hero(k) for k in HERO_DB]


def find_hero_by_ru(name: str, min_score: float = 0.74) -> Hero | None:
    """Найти героя реестра по русскому имени (с устойчивостью к ошибкам OCR)."""
    try:
        from vision import text_similarity
    except Exception:  # pragma: no cover
        from difflib import SequenceMatcher

        def text_similarity(a: str, b: str) -> float:
            return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()

    best, best_score = None, 0.0
    for hero in all_heroes():
        for candidate in hero.all_names_ru:
            score = text_similarity(name, candidate)
            if score > best_score:
                best, best_score = hero, score
    return best if best_score >= min_score else None


def resolve_hero(name_ru: str) -> Hero:
    """Превратить имя из таблицы билетов в объект героя.

    Если героя нет в реестре спрайтов — он всё равно полностью рабочий: имя
    вводится в поиск, а выбор подтверждается чтением названия в сетке (OCR).
    Так бот поддерживает любого из 120+ героев, не требуя заранее нарезанных PNG.
    """
    known = find_hero_by_ru(name_ru, min_score=0.88)
    if known is not None:
        return known
    return Hero(key=_slug(name_ru), name_ru=name_ru.strip())


def emoji_asset_map(assets_dir: str = "assets") -> dict[str, str]:
    """``{ключ: путь}`` для экрана наград (пиксельные иконки)."""
    return {h.key: h.emoji_path(assets_dir) for h in all_heroes() if h.emoji_img}


def portrait_asset_map(assets_dir: str = "assets") -> dict[str, str]:
    """``{ключ: путь}`` для сетки выбора (3D-портреты)."""
    return {h.key: h.portrait_path(assets_dir) for h in all_heroes() if h.portrait_img}


# --------------------------------------------------------------------------------------
# The automation state machine
# --------------------------------------------------------------------------------------


class GameState(StrEnum):
    """Every phase of the cyclical Dark Carnival loop."""

    UNKNOWN = "unknown"            # cannot identify the screen — re-read, then recover
    DASHBOARD = "dashboard"        # main menu, ready to queue
    QUEUEING = "queueing"          # searching for a match
    MATCH_FOUND = "match_found"    # "Принять" popup is up
    HERO_PICK = "hero_pick"        # pick grid / strategy screen
    IN_GAME = "in_game"            # match running, bots doing the work
    POST_GAME = "post_game"        # "Победа" / scoreboard
    SAFE_TO_LEAVE = "safe_to_leave"  # "Игру можно безопасно покинуть"
    REWARD_SCREEN = "reward_screen"  # carnival tickets being awarded
    DISCONNECTED = "disconnected"  # needs "Переподключиться"
    ERROR = "error"


#: Which Russian keyword intents *prove* a given state. Ordered by priority: the first
#: state whose evidence is present wins, so transient modals (accept popup, reconnect)
#: outrank the steady-state screens behind them.
STATE_EVIDENCE: list[tuple[GameState, tuple[str, ...]]] = [
    (GameState.DISCONNECTED, ("reconnect", "disconnected")),
    (GameState.MATCH_FOUND, ("accept", "queue_found")),
    (GameState.SAFE_TO_LEAVE, ("safe_to_leave",)),
    (GameState.REWARD_SCREEN, ("claim_reward",)),
    (GameState.POST_GAME, ("victory", "defeat")),
    (GameState.HERO_PICK, ("search_hero", "lock_in", "ready")),
    (GameState.IN_GAME, ("leave_game",)),
    (GameState.QUEUEING, ("cancel_search",)),
    (GameState.DASHBOARD, ("play", "coop_bots", "dark_carnival")),
]


def classify_state(intents: Iterable[str]) -> GameState:
    """Map a set of detected Russian keyword intents onto a :class:`GameState`."""
    present = set(intents)
    for state, evidence in STATE_EVIDENCE:
        if present.intersection(evidence):
            return state
    return GameState.UNKNOWN


#: The happy-path cycle. Used by the GUI to render progress and by ``learning.py`` to
#: detect "we are stuck in the same state far too long".
CYCLE_ORDER: tuple[GameState, ...] = (
    GameState.DASHBOARD,
    GameState.QUEUEING,
    GameState.MATCH_FOUND,
    GameState.HERO_PICK,
    GameState.IN_GAME,
    GameState.POST_GAME,
    GameState.SAFE_TO_LEAVE,
    GameState.REWARD_SCREEN,
)

#: Realistic upper bound (seconds) for how long a state may legitimately last. Exceeding
#: it triggers the recovery routine in ``executor.py``.
STATE_TIMEOUTS: dict[GameState, float] = {
    GameState.DASHBOARD: 60.0,
    GameState.QUEUEING: 420.0,
    GameState.MATCH_FOUND: 45.0,
    GameState.HERO_PICK: 120.0,
    GameState.IN_GAME: 3600.0,
    GameState.POST_GAME: 180.0,
    GameState.SAFE_TO_LEAVE: 90.0,
    GameState.REWARD_SCREEN: 120.0,
    GameState.DISCONNECTED: 300.0,
    GameState.UNKNOWN: 90.0,
    GameState.ERROR: 60.0,
}


# --------------------------------------------------------------------------------------
# Screen layout (fractions of the client area — never absolute pixels)
# --------------------------------------------------------------------------------------

#: ``name -> (left, top, width, height)`` as fractions of the Dota client.
#: Narrowing OCR to a region is a ~5x speed-up versus reading the whole desktop.
LAYOUT: dict[str, tuple[float, float, float, float]] = {
    "full":            (0.00, 0.00, 1.00, 1.00),
    "top_bar":         (0.00, 0.00, 1.00, 0.12),
    "bottom_bar":      (0.00, 0.86, 1.00, 0.14),
    "center_modal":    (0.28, 0.28, 0.44, 0.44),
    "play_button":     (0.70, 0.84, 0.30, 0.16),
    "hero_grid":       (0.05, 0.18, 0.90, 0.60),
    "hero_search":     (0.05, 0.10, 0.35, 0.08),
    "reward_panel":    (0.15, 0.15, 0.70, 0.70),
    "scoreboard":      (0.10, 0.05, 0.80, 0.30),
}


def region_for(name: str, screen_w: int, screen_h: int):
    """Materialise a named layout region for the current resolution."""
    try:
        from vision import Region
    except Exception:  # pragma: no cover - packaged import fallback
        from scripts.vision import Region  # type: ignore
    left, top, width, height = LAYOUT[name]
    return Region(int(screen_w * left), int(screen_h * top),
                  int(screen_w * width), int(screen_h * height))


# --------------------------------------------------------------------------------------
# Run configuration
# --------------------------------------------------------------------------------------


@dataclass
class LoopConfig:
    """Operator-tunable parameters for one farming session."""

    max_cycles: int = 0                        # 0 = без ограничения
    #: Сколько билетов каждого аркана нужно: {"death": 30, "jester": 12}
    ticket_target: dict[str, int] = field(default_factory=dict)
    #: Минимальная отдача за игру. 3 = играть только на «тройных» героях.
    min_ticket_yield: int = DEFAULT_MIN_YIELD
    avoid_heroes: tuple[str, ...] = ()         # имена героев, которых не брать
    tickets_file: str = "data/tickets.json"
    leave_early: bool = True                   # выходить по «можно безопасно покинуть»
    accept_timeout: float = 40.0
    queue_timeout: float = 420.0
    game_timeout: float = 3600.0
    poll_interval: float = 2.0
    simulate: bool = False
    assets_dir: str = "assets"

    def goal(self) -> TicketGoal:
        return TicketGoal(target=dict(self.ticket_target))

    def book(self) -> TicketBook:
        book = TicketBook.load(self.tickets_file)
        book.min_yield = self.min_ticket_yield
        return book


__all__ = [
    # Герои
    "HERO_DB", "Hero", "get_hero", "all_heroes", "find_hero_by_ru", "resolve_hero",
    "emoji_asset_map", "portrait_asset_map",
    # Билеты (реэкспорт из tickets.py)
    "ARCANA", "YIELDS", "DEFAULT_MIN_YIELD", "TicketBook", "TicketGoal", "Pick",
    "plan_next_pick", "arcana_order", "arcana_name_ru", "find_arcana_by_ru",
    # Состояния и разметка экрана
    "GameState", "STATE_EVIDENCE", "classify_state", "CYCLE_ORDER", "STATE_TIMEOUTS",
    "LAYOUT", "region_for", "LoopConfig",
]
