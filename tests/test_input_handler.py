"""Humanised input synthesis — verified against the DryRun backend (no OS calls)."""

from __future__ import annotations

import math

import pytest


@pytest.fixture
def handler():
    import input_handler

    return input_handler.InputHandler(
        backend=input_handler.DryRunBackend(), seed=1337, sleep=lambda _s: None
    )


@pytest.fixture
def mod():
    import input_handler

    return input_handler


def test_click_moves_then_presses_then_releases(handler):
    handler.click(500, 400)
    kinds = [k for k, _ in handler.backend.events]
    assert kinds[-2:] == ["mouse_down", "mouse_up"]
    assert "move_to" in kinds
    assert handler.backend.events[-3][1] == (500, 400)   # final move lands exactly


def test_path_is_curved_not_a_straight_teleport(mod):
    import random

    path = mod.bezier_path((0, 0), (1000, 0), steps=40, rng=random.Random(7))
    assert len(path) == 40
    assert path[-1] == (1000, 0)
    # A straight line would keep y == 0 the whole way; a human arc does not.
    assert max(abs(y) for _x, y in path) > 0


def test_path_is_eased_not_constant_velocity(mod):
    import random

    path = mod.bezier_path((0, 0), (1000, 1000), steps=30, rng=random.Random(3))
    def step(i):
        return math.dist(path[i], path[i + 1])
    assert step(len(path) // 2) > step(0)        # fast in the middle
    assert step(len(path) // 2) > step(len(path) - 2)   # decelerates on approach


def test_jitter_stays_inside_the_box_but_off_centre(mod, vision_mod):
    import random

    rng = random.Random(11)
    box = vision_mod.Region(100, 100, 200, 60)
    points = [mod.jitter_point(box, rng) for _ in range(60)]
    for x, y in points:
        assert box.contains(x, y)
    assert len(set(points)) > 1          # never the same pixel twice
    assert any(p != box.center for p in points)  # not always dead centre


def test_click_box_targets_inside_an_ocr_hit(handler, vision_mod):
    box = vision_mod.Region(800, 500, 200, 60)
    x, y = handler.click_box(box)
    assert box.contains(x, y)


def test_click_hit_accepts_a_vision_text_hit(handler, vision_mod):
    hit = vision_mod.TextHit("Принять", vision_mod.Region(700, 400, 180, 50), 0.98)
    x, y = handler.click_hit(hit)
    assert hit.region.contains(x, y)


def test_cyrillic_typing_is_routed_to_the_backend(handler):
    handler.type_text("Фантом Ассасин")
    assert ("type_text", ("Фантом Ассасин",)) in handler.backend.events


def test_hotkey_presses_and_releases_in_reverse_order(handler):
    handler.hotkey("ctrl", "a")
    kinds = [(k, a[0]) for k, a in handler.backend.events]
    assert kinds == [("key_down", "ctrl"), ("key_down", "a"),
                     ("key_up", "a"), ("key_up", "ctrl")]


def test_kill_switch_suppresses_all_output(handler):
    handler.enabled = False
    handler.click(10, 10)
    handler.type_text("x")
    handler.press("escape")
    assert handler.backend.events == []


def test_dryrun_is_chosen_when_simulating(mod):
    assert isinstance(mod.auto_backend(simulate=True), mod.DryRunBackend)


def test_wiggle_keeps_the_pointer_near_its_origin(handler):
    handler.backend.move_to(500, 500)
    handler.wiggle()
    x, y = handler.backend.position()
    assert math.dist((x, y), (500, 500)) < 80
