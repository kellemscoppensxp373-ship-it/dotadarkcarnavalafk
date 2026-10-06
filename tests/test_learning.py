"""Adaptive memory: spatial priors, statistics, persistence and stuck detection."""

from __future__ import annotations

import json

import pytest


def test_records_success_and_failure(brain):
    brain.record_hit("accept", center=(960, 540), screen=(1920, 1080), latency=0.4)
    brain.record_miss("accept")
    st = brain.stats("accept")
    assert (st.attempts, st.successes, st.failures) == (2, 1, 1)
    assert 0.0 < st.success_rate < 1.0


def test_spatial_prior_converges_on_the_button(brain):
    for _ in range(5):
        brain.record_hit("accept", center=(960, 540), screen=(1920, 1080))
    st = brain.stats("accept")
    assert st.hot_x == pytest.approx(0.5, abs=0.01)
    assert st.hot_y == pytest.approx(0.5, abs=0.01)
    assert st.is_reliable


def test_hot_region_is_only_offered_once_confident(brain):
    assert brain.hot_region("accept", (1920, 1080)) is None
    for _ in range(4):
        brain.record_hit("accept", center=(960, 540), screen=(1920, 1080))
    region = brain.hot_region("accept", (1920, 1080))
    assert region is not None
    assert region.contains(960, 540)
    assert region.width < 1920 and region.height < 1080     # it's a *narrowed* search


def test_hot_region_is_clamped_to_the_screen(brain):
    for _ in range(4):
        brain.record_hit("close", center=(1910, 10), screen=(1920, 1080))
    r = brain.hot_region("close", (1920, 1080))
    assert r.left >= 0 and r.top >= 0
    assert r.right <= 1920 and r.bottom <= 1080


def test_repeated_failures_drive_the_ewma_down(brain):
    for _ in range(10):
        brain.record_hit("play", center=(100, 100), screen=(1920, 1080))
    high = brain.stats("play").ewma_success
    for _ in range(10):
        brain.record_miss("play")
    assert brain.stats("play").ewma_success < high


def test_suggest_timeout_tightens_once_latency_is_known(brain):
    default = 60.0
    assert brain.suggest_timeout("accept", default) == default
    for _ in range(6):
        brain.record_hit("accept", center=(1, 1), screen=(1920, 1080), latency=1.0)
    assert brain.suggest_timeout("accept", default) < default


def test_persistence_round_trip(tmp_path):
    import learning

    path = tmp_path / "brain.json"
    a = learning.LearningStore(str(path), autosave=False)
    a.record_hit("accept", center=(960, 540), screen=(1920, 1080), latency=0.3)
    a.bump("cycles_total", 7)
    a.record_cycle(learning.CycleRecord(index=0, hero="lina", tickets=["Chaos"], completed=True))
    a.save()

    b = learning.LearningStore(str(path), autosave=False)
    assert b.counters["cycles_total"] >= 7
    assert b.stats("accept").successes == 1
    assert b.cycles[-1].hero == "lina"


def test_corrupt_brain_file_is_survivable(tmp_path):
    import learning

    path = tmp_path / "brain.json"
    path.write_text("{ this is not json", encoding="utf-8")
    store = learning.LearningStore(str(path), autosave=False)
    assert store.intents == {}      # started fresh instead of crashing


def test_schema_change_discards_stale_memory(tmp_path):
    import learning

    path = tmp_path / "brain.json"
    path.write_text(json.dumps({"schema": -1, "intents": {"accept": {}}}), encoding="utf-8")
    store = learning.LearningStore(str(path), autosave=False)
    assert store.intents == {}


def test_cycle_accounting_and_summary(tmp_path):
    import learning

    store = learning.LearningStore(str(tmp_path / "b.json"), autosave=False)
    store.record_cycle(learning.CycleRecord(
        index=0, hero="phantom_assassin", duration=300.0,
        tickets=["Death", "Death", "Death"], victory=True, completed=True))
    store.record_cycle(learning.CycleRecord(
        index=1, hero="lina", duration=200.0, tickets=["Chaos"], completed=True))
    summary = store.summary()
    assert summary["cycles_total"] == 2
    assert summary["cycles_completed"] == 2
    assert summary["victories"] == 1
    assert summary["tickets"] == {"Death": 3, "Chaos": 1}
    assert summary["avg_cycle_seconds"] == 250.0


def test_stuck_detection(brain):
    brain.note_state("queueing", now=0.0)
    brain.note_state("queueing", now=100.0)
    assert brain.time_in_state(now=120.0) == pytest.approx(120.0)
    assert brain.is_stuck(limit=60.0, now=120.0)
    assert not brain.is_stuck(limit=300.0, now=120.0)


def test_state_change_resets_the_stuck_timer(brain):
    brain.note_state("queueing", now=0.0)
    brain.note_state("match_found", now=100.0)
    assert brain.time_in_state(now=101.0) == pytest.approx(1.0)


def test_flapping_detection(brain):
    for i in range(8):
        brain.note_state("dashboard" if i % 2 else "unknown", now=float(i))
    assert brain.is_flapping()


def test_steady_state_is_not_flapping(brain):
    for i in range(8):
        brain.note_state("in_game", now=float(i))
    assert not brain.is_flapping()


def test_reset_clears_everything(brain):
    brain.record_hit("accept", center=(1, 1), screen=(10, 10))
    brain.bump("x")
    brain.reset()
    assert not brain.intents and not brain.counters
