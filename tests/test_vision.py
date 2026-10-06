"""Vision engine: Russian/Cyrillic semantic reading, geometry, caching, OCR tolerance."""

from __future__ import annotations

import pytest
from conftest import word

# ---------------------------------------------------------------- normalisation & fuzz


def test_normalize_folds_case_punctuation_and_yo(vision_mod):
    n = vision_mod.normalize_text
    assert n("  ПРИНЯТЬ!  ") == "принять"
    assert n("Всё") == n("Все")
    assert n("Найти  игру...") == "наити игру"


def test_normalize_folds_latin_homoglyphs(vision_mod):
    """EasyOCR frequently emits Latin lookalikes inside Cyrillic words."""
    n = vision_mod.normalize_text
    assert n("Пpинять") == n("Принять")   # latin 'p'
    assert n("Победa") == n("Победа")     # latin 'a'


@pytest.mark.parametrize(
    "observed,intent",
    [
        ("Принять", "accept"),
        ("ПРИНЯТЬ", "accept"),
        ("Пpинять", "accept"),            # homoglyph
        ("Принять 12", "accept"),         # accept timer glued on
        ("Найти игру", "play"),
        ("Играть", "play"),
        ("Продолжить", "continue"),
        ("Закрыть", "close"),
        ("ОК", "close"),
        ("Победа", "victory"),
        ("Игру можно безопасно покинуть", "safe_to_leave"),
        ("Переподключиться", "reconnect"),
        ("Покинуть игру", "leave_game"),
    ],
)
def test_every_required_russian_keyword_matches(vision_mod, observed, intent):
    score, form = vision_mod.best_keyword_score(observed, intent)
    assert score >= vision_mod.threshold_for(intent), f"{observed!r} failed for {intent}: {score}"
    assert form


def test_keywords_do_not_cross_match(vision_mod):
    """«Принять» must never be mistaken for «Покинуть игру»."""
    score, _ = vision_mod.best_keyword_score("Принять", "leave_game")
    assert score < vision_mod.threshold_for("leave_game")


def test_ocr_noise_tolerance(vision_mod):
    """A single mangled character still resolves to the right intent."""
    score, _ = vision_mod.best_keyword_score("Продолжить", "continue")
    assert score == 1.0
    score, _ = vision_mod.best_keyword_score("Продопжить", "continue")  # l -> п
    assert score >= 0.78


def test_long_phrase_has_looser_threshold(vision_mod):
    assert vision_mod.threshold_for("safe_to_leave") < vision_mod.threshold_for("accept")


# ------------------------------------------------------------------------- geometry


def test_region_geometry(vision_mod):
    r = vision_mod.Region(100, 200, 50, 20)
    assert r.center == (125, 210)
    assert (r.right, r.bottom) == (150, 220)
    assert r.contains(120, 205)
    assert not r.contains(99, 205)
    assert r.offset(10, -10) == vision_mod.Region(110, 190, 50, 20)


def test_relative_region_is_resolution_independent(vision_mod):
    r = vision_mod.relative_region(1920, 1080, 0.5, 0.5, 0.25, 0.1)
    assert (r.left, r.top, r.width, r.height) == (960, 540, 480, 108)


# -------------------------------------------------------------------- semantic search


def test_find_intent_returns_bounding_box_centre(vision, fake_ocr):
    """The core promise: locate Russian text, click *its* box — no blind coordinates."""
    fake_ocr.set_screen([word("Принять", x=800, y=500, w=200, h=60)])
    hit = vision.find_intent("accept")
    assert hit is not None
    assert hit.intent == "accept"
    assert hit.center == (900, 530)
    assert hit.matched_form == "Принять"


def test_find_intent_absent_returns_none(vision, fake_ocr):
    fake_ocr.set_screen([word("Отменить поиск")])
    assert vision.find_intent("accept") is None


def test_low_confidence_detections_are_discarded(vision, fake_ocr):
    fake_ocr.set_screen([word("Принять", conf=0.05)])
    assert vision.find_intent("accept") is None


def test_region_offsets_are_applied_to_coordinates(vision, fake_ocr, vision_mod):
    """Coordinates from a cropped grab must be translated back to desktop space."""
    fake_ocr.set_screen([word("Принять", x=10, y=10, w=100, h=40)])
    region = vision_mod.Region(500, 400, 600, 300)
    hit = vision.find_intent("accept", region=region)
    assert hit.center == (560, 430)


def test_best_match_wins_when_several_candidates(vision, fake_ocr):
    fake_ocr.set_screen([
        word("Принять приглашение", x=0, y=0),
        word("Принять", x=900, y=900, w=100, h=40),
    ])
    hit = vision.find_intent("accept")
    assert hit.center == (950, 920)


def test_detect_state_keywords_single_pass(vision, fake_ocr):
    fake_ocr.set_screen([
        word("Победа", x=100, y=100),
        word("Игру можно безопасно покинуть", x=100, y=300),
        word("Покинуть игру", x=100, y=500),
    ])
    found = vision.detect_state_keywords()
    assert {"victory", "safe_to_leave", "leave_game"} <= set(found)
    assert found["victory"].center == (160, 115)


def test_find_text_matches_localized_hero_name(vision, fake_ocr):
    fake_ocr.set_screen([word("Фантом Ассасин", x=400, y=300, w=220, h=40)])
    hit = vision.find_text("Фантом Ассасин")
    assert hit is not None and hit.center == (510, 320)


def test_cache_prevents_redundant_ocr(vision_mod, fake_screen, fake_ocr):
    clock = [0.0]
    v = vision_mod.Vision(screen=fake_screen, ocr=fake_ocr,
                          config=vision_mod.VisionConfig(cache_ttl=1.0, upscale=1.0),
                          clock=lambda: clock[0])
    fake_ocr.set_screen([word("Принять")])
    v.read_screen()
    v.read_screen()
    assert fake_ocr.reads == 1          # served from cache
    clock[0] = 2.0
    v.read_screen()
    assert fake_ocr.reads == 2          # cache expired
    v.read_screen(fresh=True)
    assert fake_ocr.reads == 3          # fresh bypasses the cache


def test_wait_for_intent_polls_until_it_appears(vision_mod, fake_screen, fake_ocr):
    clock = [0.0]
    fake_ocr.screens = [[], [], [word("Принять", x=300, y=300)]]
    v = vision_mod.Vision(screen=fake_screen, ocr=fake_ocr,
                          config=vision_mod.VisionConfig(cache_ttl=0.0, upscale=1.0),
                          clock=lambda: clock[0])

    def sleep(sec):
        clock[0] += sec
        fake_ocr.advance()

    hit = v.wait_for_intent("accept", timeout=10, poll=1.0, sleep=sleep)
    assert hit is not None and hit.intent == "accept"


def test_wait_for_intent_times_out(vision_mod, fake_screen, fake_ocr):
    clock = [0.0]
    fake_ocr.set_screen([])
    v = vision_mod.Vision(screen=fake_screen, ocr=fake_ocr,
                          config=vision_mod.VisionConfig(cache_ttl=0.0, upscale=1.0),
                          clock=lambda: clock[0])

    def sleep(sec):
        clock[0] += sec

    assert v.wait_for_intent("accept", timeout=3, poll=1.0, sleep=sleep) is None


def test_describe_screen_dump(vision, fake_ocr):
    fake_ocr.set_screen([word("Принять", x=10, y=20)])
    dump = vision.describe_screen()
    assert "Принять" in dump and "accept" in dump


def test_all_required_keyword_intents_present(vision_mod):
    required = {"accept", "play", "continue", "close", "victory",
                "safe_to_leave", "reconnect", "leave_game"}
    assert required <= set(vision_mod.RU_KEYWORDS)


def test_easyocr_backend_defaults_to_russian_and_english(vision_mod):
    backend = vision_mod.EasyOcrBackend()
    assert backend.lang_list == ["ru", "en"]
