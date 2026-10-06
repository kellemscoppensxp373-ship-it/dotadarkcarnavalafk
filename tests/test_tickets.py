"""Таблица билетов: 11 арканов, отдача ×1/×2/×3, хранение в JSON."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def tickets_mod():
    import tickets

    return tickets


# ------------------------------------------------------------------ структура файла


def test_new_book_has_all_arcana_with_empty_sections(book, tickets_mod):
    for key in tickets_mod.ARCANA:
        for y in tickets_mod.YIELDS:
            assert book.heroes_for(key, y) == []
    assert book.is_empty()


def test_book_is_created_on_disk_at_first_load(tmp_path, tickets_mod):
    path = tmp_path / "tickets.json"
    tickets_mod.TicketBook.load(str(path))
    assert path.is_file()
    blob = json.loads(path.read_text(encoding="utf-8"))
    assert blob["min_yield"] == 3
    assert len(blob["arcana"]) == 11
    assert blob["arcana"]["jester"]["name_ru"] == "ШУТ"
    assert blob["arcana"]["jester"]["x3"] == []


def test_round_trip_through_json(tmp_path, tickets_mod):
    path = str(tmp_path / "t.json")
    book = tickets_mod.TicketBook(path=path)
    book.set_heroes("death", 3, ["Фантом Ассасин"])
    book.set_heroes("star", 2, ["Лина", "Зевс"])
    book.save()

    again = tickets_mod.TicketBook.load(path)
    assert again.heroes_for("death", 3) == ["Фантом Ассасин"]
    assert again.heroes_for("star", 2) == ["Лина", "Зевс"]


def test_corrupt_file_does_not_crash(tmp_path, tickets_mod):
    path = tmp_path / "t.json"
    path.write_text("{ сломано", encoding="utf-8")
    book = tickets_mod.TicketBook.load(str(path))
    assert book.is_empty()


def test_unknown_arcana_in_file_is_ignored(tmp_path, tickets_mod):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({
        "min_yield": 3,
        "arcana": {"tower": {"x3": ["Кто-то"]}, "death": {"x3": ["Лина"]}},
    }), encoding="utf-8")
    book = tickets_mod.TicketBook.load(str(path))
    assert book.heroes_for("death", 3) == ["Лина"]


def test_blank_names_are_stripped(book):
    book.set_heroes("death", 3, ["  Лина  ", "", "   "])
    assert book.heroes_for("death", 3) == ["Лина"]


def test_rejects_bad_arcana_and_yield(book):
    with pytest.raises(KeyError):
        book.set_heroes("несуществующий", 3, ["Х"])
    with pytest.raises(ValueError):
        book.set_heroes("death", 7, ["Х"])


def test_add_hero_is_idempotent(book):
    book.add_hero("death", 3, "Лина")
    book.add_hero("death", 3, "Лина")
    assert book.heroes_for("death", 3) == ["Лина"]


# -------------------------------------------------------------------- запросы


def test_candidates_respect_min_yield(book):
    book.set_heroes("death", 1, ["Один"])
    book.set_heroes("death", 2, ["Два"])
    book.set_heroes("death", 3, ["Три"])
    assert book.candidates("death", 3) == [("Три", 3)]
    assert book.candidates("death", 2) == [("Три", 3), ("Два", 2)]
    assert len(book.candidates("death", 1)) == 3


def test_candidates_are_sorted_by_yield_descending(book):
    book.set_heroes("death", 1, ["Один"])
    book.set_heroes("death", 3, ["Три"])
    assert [y for _n, y in book.candidates("death", 1)] == [3, 1]


def test_configured_and_missing_report_setup_progress(book, tickets_mod):
    assert len(book.missing()) == 11
    book.set_heroes("death", 3, ["Лина"])
    assert book.configured() == ["death"]
    assert "death" not in book.missing()
    assert len(book.missing()) == 10


def test_two_ticket_hero_does_not_count_as_configured(book):
    """×2 не закрывает аркан: приоритет — только ×3."""
    book.set_heroes("death", 2, ["Двойной"])
    assert book.configured() == []
    assert "death" in book.missing()


def test_yield_of_resolves_a_hero_back_to_its_section(book):
    book.set_heroes("death", 3, ["Фантом Ассасин"])
    assert book.yield_of("Фантом Ассасин", "death") == 3
    assert book.yield_of("Фантом Ассaсин", "death") == 3     # латинская «a» от OCR
    assert book.yield_of("Лина", "death") == 0


def test_summary_lists_every_arcana_in_russian(book):
    book.set_heroes("death", 3, ["Лина"])
    text = book.summary()
    assert "СМЕРТЬ" in text and "Лина" in text
    assert "не заполнено" in text          # остальные ещё пустые


def test_min_yield_is_persisted(tmp_path, tickets_mod):
    path = str(tmp_path / "t.json")
    book = tickets_mod.TicketBook(path=path, min_yield=2)
    book.save()
    assert tickets_mod.TicketBook.load(path).min_yield == 2
