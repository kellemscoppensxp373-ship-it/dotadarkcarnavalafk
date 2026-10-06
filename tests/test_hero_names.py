"""Имя героя можно писать по-русски и по-английски.

Оператор заполнил таблицу билетов английскими названиями (Io, Rubick, Wraith King),
хотя клиент русский. Бот обязан понять оба написания и пережить опечатку.
"""

from __future__ import annotations

import pytest

# То, что пользователь реально вписал в таблицу билетов.
USER_ROSTER = {
    "Io": "Ио",
    "Rubick": "Рубик",
    "Oracle": "Оракул",
    "Wraith King": "Призрачный Король",
    "Jakiro": "Джакиро",
    "Sven": "Свен",
    "Abadon": "Абаддон",          # опечатка пользователя
    "Chaos Knight": "Рыцарь Хаоса",
    "Muerta": "Муэрта",
    "Doom": "Дум",
    "Keeper of the Light": "Хранитель Света",
}


@pytest.mark.parametrize(("typed", "expected_ru"), sorted(USER_ROSTER.items()))
def test_english_input_resolves_to_russian_hero(logic, typed, expected_ru):
    hero = logic.resolve_hero(typed)
    assert hero.name_ru == expected_ru


@pytest.mark.parametrize(("typed", "expected_ru"), sorted(USER_ROSTER.items()))
def test_search_tries_both_languages(logic, typed, expected_ru):
    hero = logic.resolve_hero(typed)
    names = hero.search_names
    assert names[0] == expected_ru, "русское написание пробуем первым"
    assert any(n.lower() in {typed.lower(), "abaddon"} for n in names)


def test_russian_input_still_works(logic):
    hero = logic.resolve_hero("Призрачный Король")
    assert hero.name_en == "Wraith King"
    assert hero.name_ru == "Призрачный Король"


def test_registry_hero_keeps_its_sprites(logic):
    hero = logic.resolve_hero("Wraith King")
    assert hero.key == "wraith_king"
    assert hero.emoji_img and hero.portrait_img


def test_unknown_name_is_passed_through_unchanged(logic):
    hero = logic.resolve_hero("Неизвестный Герой")
    assert hero.name_ru == "Неизвестный Герой"
    assert hero.search_names[0] == "Неизвестный Герой"


def test_translation_table_is_consistent(logic):
    assert len(logic.HERO_NAMES_EN_RU) == len(logic.HERO_NAMES_RU_EN)
    assert logic.HERO_NAMES_EN_RU["Sven"] == "Свен"
    assert logic.HERO_NAMES_RU_EN["Свен"] == "Sven"
