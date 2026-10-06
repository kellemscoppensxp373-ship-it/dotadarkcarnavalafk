"""Hero database (dual-asset), ticket planning and state classification."""

from __future__ import annotations

import pytest

# ------------------------------------------------------------------ dual-asset HERO_DB


def test_every_hero_has_both_assets_and_russian_name(logic):
    """Phase 3 contract: emoji for the reward screen, portrait for the pick grid."""
    assert logic.HERO_DB, "hero DB must not be empty"
    for key, row in logic.HERO_DB.items():
        assert row["name_ru"].strip(), f"{key} missing Russian name"
        assert row["emoji_img"].endswith(".png"), f"{key} missing emoji asset"
        assert row["portrait_img"].endswith(".png"), f"{key} missing portrait asset"
        assert row["emoji_img"] != row["portrait_img"], f"{key} must use two distinct assets"
        assert row["grants_tickets"], f"{key} grants no tickets"


def test_reference_entry_matches_the_specified_schema(logic):
    pa = logic.get_hero("phantom_assassin")
    assert pa.name_ru == "Фантом Ассасин"
    assert "Фантомка" in pa.aliases_ru
    assert pa.emoji_img == "emoji_pa.png"
    assert pa.portrait_img == "portrait_pa.png"
    assert pa.grants_tickets == ("Death", "Death", "Death")
    assert pa.tier == 3


def test_asset_paths_resolve_into_the_right_folders(logic):
    pa = logic.get_hero("phantom_assassin")
    assert pa.emoji_path("assets") == "assets/emoji/emoji_pa.png"
    assert pa.portrait_path("assets") == "assets/portraits/portrait_pa.png"


def test_asset_maps_cover_the_whole_db(logic):
    assert set(logic.emoji_asset_map()) == set(logic.HERO_DB)
    assert set(logic.portrait_asset_map()) == set(logic.HERO_DB)
    # The two maps must never collide — different sprite families.
    assert not set(logic.emoji_asset_map().values()) & set(logic.portrait_asset_map().values())


def test_ticket_counts_aggregate_duplicates(logic):
    assert logic.get_hero("phantom_assassin").ticket_counts() == {"Death": 3}
    assert logic.get_hero("juggernaut").ticket_counts() == {"Blood": 2, "Death": 1}


def test_unknown_hero_raises_a_helpful_error(logic):
    with pytest.raises(KeyError, match="unknown hero"):
        logic.get_hero("io")


def test_ticket_types_have_russian_captions(logic):
    for key, ru in logic.TICKET_TYPES.items():
        assert ru and logic.TICKET_RU_TO_KEY[ru] == key


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


# --------------------------------------------------------------------- ticket planning


def test_goal_tracks_remaining_and_credits(logic):
    goal = logic.TicketGoal(target={"Death": 5, "Blood": 2})
    assert goal.remaining() == {"Death": 5, "Blood": 2}
    goal.credit(["Death", "Death", "Blood"])
    assert goal.remaining() == {"Death": 3, "Blood": 1}
    assert not goal.satisfied()
    goal.credit(["Death"] * 3 + ["Blood"])
    assert goal.satisfied()


def test_planner_picks_the_hero_closing_the_biggest_gap(logic):
    goal = logic.TicketGoal(target={"Death": 3})
    hero = logic.plan_next_hero(goal)
    assert hero.key == "phantom_assassin"       # grants Death ×3


def test_planner_switches_when_the_need_changes(logic):
    goal = logic.TicketGoal(target={"Beast": 2})
    assert logic.plan_next_hero(goal).key == "ursa"


def test_planner_respects_the_allowed_pool(logic):
    goal = logic.TicketGoal(target={"Death": 3})
    hero = logic.plan_next_hero(goal, allowed=["lina", "sniper"])
    assert hero.key in {"lina", "sniper"}


def test_planner_avoids_blacklisted_heroes(logic):
    goal = logic.TicketGoal(target={"Death": 3})
    hero = logic.plan_next_hero(goal, avoid=["phantom_assassin"])
    assert hero.key != "phantom_assassin"


def test_planner_falls_back_to_easiest_hero_when_goal_is_met(logic):
    goal = logic.TicketGoal(target={"Death": 1}, owned={"Death": 5})
    hero = logic.plan_next_hero(goal, allowed=["phantom_assassin", "lina"])
    assert hero.key == "lina"          # tier 2 beats tier 3 when nothing is needed


def test_planner_with_empty_pool_returns_none(logic):
    assert logic.plan_next_hero(logic.TicketGoal(), allowed=[]) is None


def test_heroes_granting_is_sorted_by_yield(logic):
    ranked = logic.heroes_granting("Death")
    assert ranked[0].key == "phantom_assassin"
    assert all("Death" in h.grants_tickets for h in ranked)


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
    cfg = logic.LoopConfig(ticket_target={"Death": 4})
    assert cfg.goal().remaining() == {"Death": 4}
