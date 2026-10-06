"""End-to-end orchestration: the agent plays the Russian client with no blind clicks."""

from __future__ import annotations

import threading

import pytest
from fake_dota import ClickSpy, FakeDotaClient


@pytest.fixture
def world(tmp_path):
    """A fully wired agent driving the fake Russian Dota client."""
    import dota_logic
    import executor as executor_mod
    import input_handler
    import learning
    import vision

    client = FakeDotaClient()
    clock = {"t": 0.0}

    def now() -> float:
        return clock["t"]

    def sleep(seconds: float) -> None:
        clock["t"] += max(seconds, 0.01)
        client.tick()

    v = vision.Vision(
        screen=client, ocr=client, clock=now,
        config=vision.VisionConfig(cache_ttl=0.0, upscale=1.0),
    )
    inputs = input_handler.InputHandler(
        backend=ClickSpy(client), seed=42, sleep=lambda _s: None
    )
    brain = learning.LearningStore(str(tmp_path / "brain.json"), autosave=False)
    cfg = dota_logic.LoopConfig(
        max_cycles=2, poll_interval=1.0, simulate=True,
        hero_pool=("phantom_assassin", "lina"),
        assets_dir=str(tmp_path / "assets"),
    )
    logs: list[tuple[str, str]] = []
    events = executor_mod.ExecutorEvents(on_log=lambda lvl, m: logs.append((lvl, m)))
    ex = executor_mod.Executor(
        vision=v, inputs=inputs, logic=dota_logic, brain=brain, config=cfg,
        events=events, sleep=sleep, clock=now,
    )
    return {"ex": ex, "client": client, "brain": brain, "logs": logs,
            "logic": dota_logic, "clock": clock}


# ------------------------------------------------------------------ state detection


@pytest.mark.parametrize("screen,expected", [
    ("dashboard", "dashboard"),
    ("queueing", "queueing"),
    ("match_found", "match_found"),
    ("hero_pick", "hero_pick"),
    ("in_game", "in_game"),
    ("post_game", "safe_to_leave"),     # the "safe to leave" banner takes priority
    ("reward", "reward_screen"),
    ("disconnected", "disconnected"),
])
def test_reads_russian_screen_and_identifies_state(world, screen, expected):
    world["client"].goto(screen)
    assert world["ex"].detect_state().value == expected


def test_unknown_screen_is_handled_not_crashed(world):
    world["client"].screen_name = "nothing-here"
    assert world["ex"].detect_state() is world["logic"].GameState.UNKNOWN
    world["ex"].handle_unknown()        # must not raise


# --------------------------------------------------------------- semantic clicking


def test_clicks_the_bounding_box_of_the_russian_word(world):
    """«Принять» is at (880,540,200,70) → the click must land inside that box."""
    client = world["client"]
    client.goto("match_found")
    world["ex"].handle_match_found()
    px, py, label = client.clicks[0]
    assert label == "Принять"
    assert 880 <= px <= 1080 and 540 <= py <= 610
    assert client.screen_name == "hero_pick"


def test_play_button_found_inside_a_cropped_region(world):
    """Region-cropped OCR must still yield absolute desktop coordinates."""
    client = world["client"]
    client.goto("dashboard")
    world["ex"].handle_dashboard()
    assert any(label == "Играть" for _x, _y, label in client.clicks)
    assert "queueing" in client.transitions


def test_no_absolute_coordinates_are_ever_hardcoded(world):
    """Every click must correspond to a caption the agent actually read."""
    client = world["client"]
    world["ex"].run()
    assert client.clicks, "the agent never clicked anything"
    misses = [c for c in client.clicks if c[2] == ""]
    assert not misses, f"agent clicked empty space (blind coordinates): {misses}"


# ------------------------------------------------------------------- hero picking


def test_hero_pick_types_russian_name_and_selects_portrait_fallback(world):
    client = world["client"]
    client.goto("hero_pick")
    ex = world["ex"]
    ex.current_hero = world["logic"].get_hero("phantom_assassin")
    ex.handle_hero_pick()
    assert "Фантом Ассасин" in client.typed        # Cyrillic search query
    labels = [c[2] for c in client.clicks]
    assert "Фантом Ассасин" in labels              # OCR fallback found the grid entry
    assert client.screen_name == "in_game"


def test_planner_chooses_hero_from_the_configured_pool(world):
    ex = world["ex"]
    world["client"].goto("dashboard")
    ex.handle_dashboard()
    assert ex.current_hero.key in {"phantom_assassin", "lina"}


def test_ticket_target_steers_the_hero_choice(world):
    ex = world["ex"]
    ex.goal = world["logic"].TicketGoal(target={"Chaos": 4})
    world["client"].goto("dashboard")
    ex.handle_dashboard()
    assert ex.current_hero.key == "lina"           # the only Chaos source in the pool


# ------------------------------------------------------------------- the full loop


def test_runs_two_complete_cycles_and_stops(world):
    ex, client = world["ex"], world["client"]
    summary = ex.run()
    assert ex.cycle_index == 2
    assert client.cycles_completed >= 2
    assert summary["cycles_completed"] >= 2
    # It genuinely walked the whole cycle, not just one screen.
    assert {"queueing", "match_found", "hero_pick", "in_game", "post_game"} \
        <= set(client.transitions)


def test_cycle_credits_the_expected_tickets(world):
    ex = world["ex"]
    ex.run()
    earned = world["brain"].tickets_earned()
    assert earned, "no tickets were recorded"
    assert sum(earned.values()) > 0


def test_stop_event_halts_the_loop_promptly(world):
    ex = world["ex"]
    ex.config.max_cycles = 0            # unlimited
    ex.stop_event.set()
    ex.run()
    assert ex.cycle_index == 0


def test_stop_during_a_sleep_unwinds_immediately(world):
    ex = world["ex"]
    ex.stop_event.set()
    with pytest.raises(Exception) as exc:
        ex.sleep(100.0)
    assert "StopRequested" in type(exc.value).__name__


def test_pause_blocks_then_resumes(world):
    ex = world["ex"]
    ex.pause_event.set()

    def unpause():
        ex.pause_event.clear()
        ex.stop_event.set()

    timer = threading.Timer(0.05, unpause)
    timer.start()
    ex.run()
    timer.join()
    assert ex.cycle_index == 0          # never progressed while paused


# --------------------------------------------------------------------- resilience


def test_a_failing_tick_does_not_kill_the_session(world):
    ex = world["ex"]
    calls = {"n": 0}
    original = ex.handle_dashboard

    def exploding():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Dota hiccup")
        return original()

    ex.handle_dashboard = exploding
    ex.run()
    assert calls["n"] > 1               # recovered and carried on
    assert ex.cycle_index == 2


def test_disconnect_is_detected_and_reconnected(world):
    ex, client = world["ex"], world["client"]
    client.goto("disconnected")
    assert ex.detect_state() is world["logic"].GameState.DISCONNECTED
    ex.handle_disconnected()
    assert any(label == "Переподключиться" for _x, _y, label in client.clicks)
    assert "in_game" in client.transitions


def test_watchdog_escapes_a_stuck_state(world):
    ex, brain = world["ex"], world["brain"]
    ex.state = world["logic"].GameState.QUEUEING
    brain.note_state("queueing", now=0.0)
    brain.state_history.append(("queueing", 1.0))
    # Pretend a very long time has passed by shrinking the allowed budget.
    world["logic"].STATE_TIMEOUTS[world["logic"].GameState.QUEUEING] = 0.0
    try:
        ex._watchdog()
    finally:
        world["logic"].STATE_TIMEOUTS[world["logic"].GameState.QUEUEING] = 420.0
    assert ("key_press", ("escape",)) in ex.inputs.backend.events


def test_missing_template_assets_do_not_break_the_cycle(world):
    """No .png files exist in the test workspace — the agent must fall back to OCR."""
    ex = world["ex"]
    hero = world["logic"].get_hero("phantom_assassin")
    assert ex._click_hero_portrait(hero) is False
    assert ex._harvest_rewards() == []      # no current_hero set → nothing credited


# ----------------------------------------------------------------------- learning


def test_agent_learns_where_the_accept_button_lives(world):
    ex, brain = world["ex"], world["brain"]
    for _ in range(4):
        world["client"].goto("match_found")
        ex.find("accept")
    stats = brain.stats("accept")
    assert stats.successes >= 4
    assert stats.is_reliable
    region = brain.hot_region("accept", (1920, 1080))
    assert region is not None and region.contains(980, 575)


def test_learned_prior_narrows_the_next_search(world):
    ex = world["ex"]
    for _ in range(4):
        world["client"].goto("match_found")
        ex.find("accept")
    hit = ex.find("accept")             # now served via the hot region
    assert hit is not None and hit.intent == "accept"
    assert 880 <= hit.center[0] <= 1080


def test_session_summary_is_reported(world):
    summary = world["ex"].run()
    assert summary["cycles_total"] >= 2
    assert "tickets" in summary


def test_build_executor_factory_is_exposed():
    import executor

    assert callable(executor.build_executor)
