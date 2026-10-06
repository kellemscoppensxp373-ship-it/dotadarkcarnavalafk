"""
dota_logic.py — Domain knowledge for the Dark Carnival co-op-bots farming loop.

Contains three things and nothing else (no I/O, no OCR, no clicking — this file is pure
logic and is therefore 100% unit-testable):

1. **HERO_DB** — the dual-asset hero database. The event UI renders heroes as *pixel-art
   emojis* on the ticket/reward screen, while the pick screen renders *high-res 3D
   portraits*. Same hero, two completely different sprites, so every entry carries both
   plus the Russian localisation string used to drive the hero search field.
2. **Ticket economy** — which hero grants which carnival tickets, and a planner that
   chooses the next hero to pick given what the user still needs.
3. **The state machine** — the canonical cycle of the automation loop, expressed as
   states + the Russian keywords that *prove* the client is in that state.

Resolution independence: all screen regions are expressed as fractions of the client
size and materialised at runtime via ``vision.relative_region``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

# --------------------------------------------------------------------------------------
# Ticket economy
# --------------------------------------------------------------------------------------

#: Canonical Dark Carnival ticket types, with their Russian captions as rendered in the
#: reward/ticket screen. The vision layer reads the Russian form; the logic layer speaks
#: the English key.
TICKET_TYPES: dict[str, str] = {
    "Death": "Смерть",
    "Blood": "Кровь",
    "Chaos": "Хаос",
    "Mystery": "Тайна",
    "Fortune": "Удача",
    "Beast": "Зверь",
}

TICKET_RU_TO_KEY: dict[str, str] = {v: k for k, v in TICKET_TYPES.items()}


# --------------------------------------------------------------------------------------
# PHASE 3 — DUAL-ASSET HERO DATABASE
# --------------------------------------------------------------------------------------
#
#   "emoji_img"    -> pixel-art sprite, used to parse the TICKET / REWARD screen
#   "portrait_img" -> 3D portrait,      used to find the hero in the PICK GRID
#   "name_ru"      -> official RU localisation, pasted into the hero search field
#   "grants_tickets" -> the tickets this hero's completed game awards
#
# Asset files live under assets/emoji/<emoji_img> and assets/portraits/<portrait_img>.

HERO_DB: dict[str, dict] = {
    "phantom_assassin": {
        "name_ru": "Фантом Ассасин",
        "aliases_ru": ["Фантомка", "Фантом-ассасин", "ФА"],
        "emoji_img": "emoji_pa.png",
        "portrait_img": "portrait_pa.png",
        "grants_tickets": ["Death", "Death", "Death"],
        "tier": 3,
        "role": "carry",
        "bot_difficulty": "easy",
    },
    "juggernaut": {
        "name_ru": "Джаггернаут",
        "aliases_ru": ["Джаг", "Джага"],
        "emoji_img": "emoji_jugg.png",
        "portrait_img": "portrait_jugg.png",
        "grants_tickets": ["Blood", "Blood", "Death"],
        "tier": 3,
        "role": "carry",
        "bot_difficulty": "easy",
    },
    "lina": {
        "name_ru": "Лина",
        "aliases_ru": ["Лину"],
        "emoji_img": "emoji_lina.png",
        "portrait_img": "portrait_lina.png",
        "grants_tickets": ["Chaos", "Chaos"],
        "tier": 2,
        "role": "mid",
        "bot_difficulty": "easy",
    },
    "lion": {
        "name_ru": "Лион",
        "aliases_ru": [],
        "emoji_img": "emoji_lion.png",
        "portrait_img": "portrait_lion.png",
        "grants_tickets": ["Death", "Mystery"],
        "tier": 2,
        "role": "support",
        "bot_difficulty": "easy",
    },
    "crystal_maiden": {
        "name_ru": "Кристал Мейден",
        "aliases_ru": ["Кристальная дева", "КМ"],
        "emoji_img": "emoji_cm.png",
        "portrait_img": "portrait_cm.png",
        "grants_tickets": ["Mystery", "Fortune"],
        "tier": 2,
        "role": "support",
        "bot_difficulty": "easy",
    },
    "sniper": {
        "name_ru": "Снайпер",
        "aliases_ru": [],
        "emoji_img": "emoji_sniper.png",
        "portrait_img": "portrait_sniper.png",
        "grants_tickets": ["Fortune", "Fortune"],
        "tier": 2,
        "role": "carry",
        "bot_difficulty": "easy",
    },
    "ursa": {
        "name_ru": "Урса",
        "aliases_ru": [],
        "emoji_img": "emoji_ursa.png",
        "portrait_img": "portrait_ursa.png",
        "grants_tickets": ["Beast", "Beast", "Blood"],
        "tier": 3,
        "role": "carry",
        "bot_difficulty": "medium",
    },
    "lycan": {
        "name_ru": "Ликан",
        "aliases_ru": ["Ликантроп"],
        "emoji_img": "emoji_lycan.png",
        "portrait_img": "portrait_lycan.png",
        "grants_tickets": ["Beast", "Blood"],
        "tier": 2,
        "role": "offlane",
        "bot_difficulty": "medium",
    },
    "axe": {
        "name_ru": "Акс",
        "aliases_ru": ["Топор"],
        "emoji_img": "emoji_axe.png",
        "portrait_img": "portrait_axe.png",
        "grants_tickets": ["Blood", "Chaos"],
        "tier": 2,
        "role": "offlane",
        "bot_difficulty": "easy",
    },
    "shadow_fiend": {
        "name_ru": "Шэдоу Филд",
        "aliases_ru": ["Невермор", "СФ"],
        "emoji_img": "emoji_sf.png",
        "portrait_img": "portrait_sf.png",
        "grants_tickets": ["Death", "Chaos", "Mystery"],
        "tier": 3,
        "role": "mid",
        "bot_difficulty": "medium",
    },
    "wraith_king": {
        "name_ru": "Призрачный Король",
        "aliases_ru": ["ВК", "Скелет"],
        "emoji_img": "emoji_wk.png",
        "portrait_img": "portrait_wk.png",
        "grants_tickets": ["Death", "Death", "Fortune"],
        "tier": 3,
        "role": "carry",
        "bot_difficulty": "easy",
    },
    "zeus": {
        "name_ru": "Зевс",
        "aliases_ru": [],
        "emoji_img": "emoji_zeus.png",
        "portrait_img": "portrait_zeus.png",
        "grants_tickets": ["Chaos", "Fortune"],
        "tier": 2,
        "role": "mid",
        "bot_difficulty": "easy",
    },
}


@dataclass(frozen=True, slots=True)
class Hero:
    """Typed view over a HERO_DB row."""

    key: str
    name_ru: str
    emoji_img: str
    portrait_img: str
    grants_tickets: tuple[str, ...]
    aliases_ru: tuple[str, ...] = ()
    tier: int = 1
    role: str = "unknown"
    bot_difficulty: str = "easy"

    @property
    def all_names_ru(self) -> tuple[str, ...]:
        return (self.name_ru, *self.aliases_ru)

    def emoji_path(self, assets_dir: str = "assets") -> str:
        return f"{assets_dir}/emoji/{self.emoji_img}"

    def portrait_path(self, assets_dir: str = "assets") -> str:
        return f"{assets_dir}/portraits/{self.portrait_img}"

    def ticket_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.grants_tickets:
            out[t] = out.get(t, 0) + 1
        return out


def get_hero(key: str) -> Hero:
    """Materialise a :class:`Hero` from the raw DB, raising a clear error if unknown."""
    try:
        row = HERO_DB[key]
    except KeyError as exc:
        raise KeyError(f"unknown hero key {key!r}; known: {sorted(HERO_DB)}") from exc
    return Hero(
        key=key,
        name_ru=row["name_ru"],
        emoji_img=row["emoji_img"],
        portrait_img=row["portrait_img"],
        grants_tickets=tuple(row.get("grants_tickets", ())),
        aliases_ru=tuple(row.get("aliases_ru", ())),
        tier=int(row.get("tier", 1)),
        role=row.get("role", "unknown"),
        bot_difficulty=row.get("bot_difficulty", "easy"),
    )


def all_heroes() -> list[Hero]:
    return [get_hero(k) for k in HERO_DB]


def heroes_granting(ticket: str) -> list[Hero]:
    """Every hero whose completed game awards ``ticket``, richest first."""
    out = [h for h in all_heroes() if ticket in h.grants_tickets]
    out.sort(key=lambda h: (h.ticket_counts().get(ticket, 0), h.tier), reverse=True)
    return out


def find_hero_by_ru(name: str, min_score: float = 0.74) -> Hero | None:
    """Resolve an OCR-read Russian hero name (fuzzy) back to a DB entry.

    Imports the matcher from ``vision`` lazily so this module stays dependency-free
    when used standalone (e.g. in tests or tooling).
    """
    try:
        from vision import text_similarity  # external-script sibling import
    except Exception:  # pragma: no cover - fallback for packaged imports
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


def emoji_asset_map(assets_dir: str = "assets") -> dict[str, str]:
    """``{hero_key: path}`` for the *ticket/reward* screen (pixel-art emojis)."""
    return {h.key: h.emoji_path(assets_dir) for h in all_heroes()}


def portrait_asset_map(assets_dir: str = "assets") -> dict[str, str]:
    """``{hero_key: path}`` for the *pick grid* (3D portraits)."""
    return {h.key: h.portrait_path(assets_dir) for h in all_heroes()}


# --------------------------------------------------------------------------------------
# Ticket planning
# --------------------------------------------------------------------------------------


@dataclass
class TicketGoal:
    """What the operator still wants, and what they already banked."""

    target: dict[str, int] = field(default_factory=dict)
    owned: dict[str, int] = field(default_factory=dict)

    def remaining(self) -> dict[str, int]:
        return {k: max(0, v - self.owned.get(k, 0)) for k, v in self.target.items()}

    def satisfied(self) -> bool:
        return all(v == 0 for v in self.remaining().values())

    def credit(self, tickets: Iterable[str]) -> None:
        for t in tickets:
            self.owned[t] = self.owned.get(t, 0) + 1

    def score_hero(self, hero: Hero) -> int:
        """How many *still-needed* tickets one game on this hero would deliver."""
        remaining = self.remaining()
        total = 0
        for ticket, count in hero.ticket_counts().items():
            total += min(count, remaining.get(ticket, 0))
        return total


def plan_next_hero(
    goal: TicketGoal,
    *,
    allowed: Sequence[str] | None = None,
    avoid: Sequence[str] = (),
) -> Hero | None:
    """Greedy planner: the hero that closes the most of the remaining ticket gap.

    Ties break toward the lower-tier (= faster, easier bot game) hero, which empirically
    beats picking the flashiest carry every cycle.
    """
    pool = [get_hero(k) for k in (allowed if allowed is not None else list(HERO_DB))]
    pool = [h for h in pool if h.key not in set(avoid)]
    if not pool:
        return None
    if goal.satisfied():
        # Nothing specific needed — grind the easiest hero available.
        return min(pool, key=lambda h: (h.tier, h.key))
    scored = [(goal.score_hero(h), -h.tier, h.key, h) for h in pool]
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    best_score = scored[0][0]
    if best_score <= 0:
        return min(pool, key=lambda h: (h.tier, h.key))
    return scored[0][3]


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

    max_cycles: int = 0                       # 0 = unlimited
    hero_pool: tuple[str, ...] = ("phantom_assassin", "juggernaut", "lina", "wraith_king")
    ticket_target: dict[str, int] = field(default_factory=dict)
    leave_early: bool = True                  # leave as soon as "безопасно покинуть" shows
    accept_timeout: float = 40.0
    queue_timeout: float = 420.0
    game_timeout: float = 3600.0
    poll_interval: float = 2.0
    simulate: bool = False
    assets_dir: str = "assets"

    def goal(self) -> TicketGoal:
        return TicketGoal(target=dict(self.ticket_target))


__all__ = [
    "HERO_DB", "Hero", "get_hero", "all_heroes", "heroes_granting", "find_hero_by_ru",
    "emoji_asset_map", "portrait_asset_map", "TICKET_TYPES", "TICKET_RU_TO_KEY",
    "TicketGoal", "plan_next_hero", "GameState", "STATE_EVIDENCE", "classify_state",
    "CYCLE_ORDER", "STATE_TIMEOUTS", "LAYOUT", "region_for", "LoopConfig",
]
