"""Hero database (dual-asset), ticket planning and state classification."""

from __future__ import annotations

import pytest

# ------------------------------------------------------------------ dual-asset HERO_DB


def test_hero_registry_entries_carry_both_sprite_families(logic):
    """ЭТАП 3: иконка для экрана наград, портрет для сетки выбора."""
    assert logic.HERO_DB, "реестр героев не должен быть пустым"
    for key, row in logic.HERO_DB.items():
        assert row["name_ru"].strip(), f"{key}: нет русского имени"
        assert row["emoji_img"].endswith(".png"), f"{key}: нет иконки"
        assert row["portrait_img"].endswith(".png"), f"{key}: нет портрета"
        assert row["emoji_img"] != row["portrait_img"], f"{key}: спрайты должны различаться"


def test_reference_entry_matches_the_specified_schema(logic):
    pa = logic.get_hero("phantom_assassin")
    assert pa.name_ru == "Фантом Ассасин"
    assert "Фантомка" in pa.aliases_ru
    assert pa.emoji_img == "emoji_pa.png"
    assert pa.portrait_img == "portrait_pa.png"
    assert pa.has_assets


def test_asset_paths_resolve_into_the_right_folders(logic):
    pa = logic.get_hero("phantom_assassin")
    assert pa.emoji_path("assets") == "assets/emoji/emoji_pa.png"
    assert pa.portrait_path("assets") == "assets/portraits/portrait_pa.png"


def test_asset_maps_cover_the_registry(logic):
    assert set(logic.emoji_asset_map()) == set(logic.HERO_DB)
    assert set(logic.portrait_asset_map()) == set(logic.HERO_DB)
    assert not set(logic.emoji_asset_map().values()) & set(logic.portrait_asset_map().values())


def test_unknown_hero_raises_a_helpful_error(logic):
    with pytest.raises(KeyError, match="неизвестный герой"):
        logic.get_hero("io")


def test_resolve_hero_matches_a_known_hero(logic):
    hero = logic.resolve_hero("Фантом Ассасин")
    assert hero.key == "phantom_assassin"
    assert hero.has_assets


def test_resolve_hero_supports_heroes_without_sprites(logic):
    """Любой из 120+ героев работает без нарезанных PNG — по имени и OCR."""
    hero = logic.resolve_hero("Тайдхантер")
    assert hero.name_ru == "Тайдхантер"
    assert not hero.has_assets
    assert hero.emoji_path("assets") == ""     # шаблонов нет — сработает запасной путь


# ------------------------------------------------------------------ fuzzy RU name lookup


@pytest.mark.parametrize("name,expected", [
    ("Фантом Ассасин", "phantom_assassin"),
    ("Фантомка", "phantom_assassin"),
    ("Джаггернаут", "juggernaut"),
    ("Кристальная дева", "crystal_maiden"),
    ("Призрачный Король", "wraith_king"),
])
def test_find_hero_by_russian_name(logic, name, expected):
    hero = logic.find_hero_by_ru(name)
    assert hero is not None and hero.key == expected


def test_find_hero_tolerates_ocr_noise(logic):
    assert logic.find_hero_by_ru("Джaггернаут").key == "juggernaut"   # latin 'a'


def test_find_hero_rejects_nonsense(logic):
    assert logic.find_hero_by_ru("qqqqqqzzzz") is None


# ------------------------------------------------- планирование билетов (11 арканов)


def test_all_eleven_arcana_present(logic):
    assert len(logic.ARCANA) == 11
    names = {v["name_ru"] for v in logic.ARCANA.values()}
    assert {"ШУТ", "МАГ", "ВЕРХОВНАЯ ЖРИЦА", "ИМПЕРАТОР", "ВЛЮБЛЁННЫЕ", "СИЛА",
            "ОТШЕЛЬНИК", "КОЛЕСО ФОРТУНЫ", "СМЕРТЬ", "ДЬЯВОЛ", "ЗВЕЗДА"} == names


def test_arcana_sorted_like_the_event_ui(logic):
    order = logic.arcana_order()
    assert order[0] == "jester" and order[1] == "magician"
    assert order[-1] == "star"


def test_find_arcana_by_ocr_title(logic):
    assert logic.find_arcana_by_ru("ВЕРХОВНАЯ ЖРИЦА") == "high_priestess"
    assert logic.find_arcana_by_ru("КОЛЕСО ФОРТУНЫ") == "wheel"
    assert logic.find_arcana_by_ru("ВЛЮБЛEННЫЕ") == "lovers"      # ё/е от OCR
    assert logic.find_arcana_by_ru("какая-то ерунда") is None


def test_goal_tracks_remaining_and_credits(logic):
    goal = logic.TicketGoal(target={"death": 9, "jester": 3})
    assert goal.remaining() == {"death": 9, "jester": 3}
    goal.credit("death", 3)
    assert goal.need("death") == 6
    assert not goal.satisfied()
    goal.credit("death", 6)
    goal.credit("jester", 3)
    assert goal.satisfied()


def test_planner_prefers_the_arcana_with_the_biggest_gap(logic, book):
    book.set_heroes("death", 3, ["Фантом Ассасин"])
    book.set_heroes("jester", 3, ["Лина"])
    goal = logic.TicketGoal(target={"death": 9, "jester": 3})
    pick = logic.plan_next_pick(book, goal)
    assert pick.arcana == "death" and pick.hero_name == "Фантом Ассасин"
    assert pick.yield_ == 3


def test_planner_ignores_heroes_below_three_tickets(logic, book):
    """Главное требование: ×1 и ×2 не нужны — играем только на ×3."""
    book.set_heroes("death", 1, ["Одиночка"])
    book.set_heroes("death", 2, ["Двойной"])
    goal = logic.TicketGoal(target={"death": 9})
    assert logic.plan_next_pick(book, goal) is None      # ×3 не задан — играть не на чем

    book.set_heroes("death", 3, ["Тройной"])
    pick = logic.plan_next_pick(book, goal)
    assert pick.hero_name == "Тройной" and pick.yield_ == 3


def test_lower_yields_can_be_allowed_explicitly(logic, book):
    book.set_heroes("death", 2, ["Двойной"])
    goal = logic.TicketGoal(target={"death": 4})
    assert logic.plan_next_pick(book, goal, min_yield=2).yield_ == 2


def test_planner_skips_satisfied_arcana(logic, book):
    book.set_heroes("death", 3, ["ГеройА"])
    book.set_heroes("star", 3, ["ГеройБ"])
    goal = logic.TicketGoal(target={"death": 3, "star": 3}, owned={"death": 3})
    assert logic.plan_next_pick(book, goal).arcana == "star"


def test_planner_avoids_blacklisted_heroes(logic, book):
    book.set_heroes("death", 3, ["Плохой", "Хороший"])
    goal = logic.TicketGoal(target={"death": 9})
    pick = logic.plan_next_pick(book, goal, avoid=["Плохой"])
    assert pick.hero_name == "Хороший"


def test_planner_rotates_between_equal_heroes(logic, book):
    book.set_heroes("death", 3, ["Первый", "Второй"])
    goal = logic.TicketGoal(target={"death": 30})
    picked = {logic.plan_next_pick(book, goal, rotation=i).hero_name for i in range(2)}
    assert picked == {"Первый", "Второй"}


def test_planner_rotates_arcana_when_no_goal_is_set(logic, book):
    book.set_heroes("death", 3, ["ГеройА"])
    book.set_heroes("star", 3, ["ГеройБ"])
    goal = logic.TicketGoal()
    seen = {logic.plan_next_pick(book, goal, rotation=i).arcana for i in range(2)}
    assert seen == {"death", "star"}


def test_planner_returns_none_on_an_empty_book(logic, book):
    assert logic.plan_next_pick(book, logic.TicketGoal()) is None


def test_pick_describes_itself_in_russian(logic, book):
    book.set_heroes("high_priestess", 3, ["Лина"])
    pick = logic.plan_next_pick(book, logic.TicketGoal(target={"high_priestess": 3}))
    assert "Лина" in pick.describe()
    assert "ВЕРХОВНАЯ ЖРИЦА" in pick.describe()


# ------------------------------------------------------------------ state machine


@pytest.mark.parametrize("intents,expected", [
    (["play"], "dashboard"),
    (["coop_bots"], "dashboard"),
    (["cancel_search"], "queueing"),
    (["accept"], "match_found"),
    (["search_hero"], "hero_pick"),
    (["leave_game"], "in_game"),
    (["victory"], "post_game"),
    (["safe_to_leave"], "safe_to_leave"),
    (["reconnect"], "disconnected"),
    (["claim_reward"], "reward_screen"),
    ([], "unknown"),
])
def test_classify_state(logic, intents, expected):
    assert logic.classify_state(intents).value == expected


def test_transient_modals_outrank_background_screens(logic):
    """The accept popup renders over the dashboard — the popup must win."""
    assert logic.classify_state(["play", "accept"]) is logic.GameState.MATCH_FOUND
    # Likewise a disconnect over anything else.
    assert logic.classify_state(["victory", "reconnect"]) is logic.GameState.DISCONNECTED
    # And "safe to leave" beats the still-visible scoreboard + leave button.
    assert logic.classify_state(["victory", "leave_game", "safe_to_leave"]) \
        is logic.GameState.SAFE_TO_LEAVE


def test_every_state_has_a_timeout(logic):
    for state in logic.GameState:
        assert state in logic.STATE_TIMEOUTS


def test_state_evidence_only_references_known_keywords(logic):
    import vision

    for _state, evidence in logic.STATE_EVIDENCE:
        for intent in evidence:
            assert intent in vision.RU_KEYWORDS, f"unknown intent {intent}"


# ------------------------------------------------------------------------- layout


def test_regions_are_resolution_independent(logic):
    small = logic.region_for("play_button", 1280, 720)
    large = logic.region_for("play_button", 3840, 2160)
    assert large.left == small.left * 3
    assert large.width == small.width * 3


def test_all_layout_regions_are_within_the_screen(logic):
    for name in logic.LAYOUT:
        r = logic.region_for(name, 1920, 1080)
        assert r.left >= 0 and r.top >= 0
        assert r.right <= 1920 and r.bottom <= 1080, name


def test_loop_config_builds_a_goal(logic):
    cfg = logic.LoopConfig(ticket_target={"death": 9})
    assert cfg.goal().remaining() == {"death": 9}


def test_loop_config_defaults_to_triple_tickets_only(logic):
    assert logic.LoopConfig().min_ticket_yield == 3
