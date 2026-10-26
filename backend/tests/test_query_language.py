"""Independent invariants for Russian units, date ranges and contextual homonyms."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from backend.agents.query_parser import parse_query
from backend.agents.query_text import normalize_query_text
from backend.contracts import CharacteristicSpec, SearchRequest
from scripts.parser_eval import load_jsonl

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def catalog():
    return [CharacteristicSpec.model_validate(yaml.safe_load(path.read_text()))
            for path in sorted((ROOT/'data/characteristics').glob('*.yaml'))]


@pytest.mark.parametrize(('text', 'cid', 'field', 'value'), [
    ('до моря 0,75 км', 'beach_walk', 'distance_m', 750),
    ('до пляжа не дольше 0,2 часа', 'beach_walk', 'minutes', 12),
    ('парковка в семи метрах', 'car_access', 'parking_distance_m', 7),
    ('аптека в пределах 1,25 км', 'night_pharmacy', 'distance_m', 1250),
    ('площадка для детей в пятистах метрах', 'family_nearby', 'available', True),
    ('интернет от 5 МБ/с', 'connectivity', 'download_mbps', 40),
    ('интернет от 0,1 Гбит/с', 'connectivity', 'download_mbps', 100),
])
def test_unit_conversion_and_conservative_unrecognized_number_words(catalog, text, cid, field, value):
    parsed = parse_query(SearchRequest(text=text), catalog)
    conditions = [c for c in parsed.must if c.characteristic_id == cid]
    assert len(conditions) == 1 and conditions[0].field == field and conditions[0].value == value
    if 'пятистах' in text:
        assert any('пятистах' in fragment for fragment in parsed.unmapped)


@pytest.mark.parametrize('text', [
    'до моря 600 м, аптека 250 м, парковка 90 м',
    'парковка 90 м, до моря 600 м, аптека 250 м',
    'аптека 250 м и парковка 90 м, до моря 600 м',
])
def test_distance_attachment_is_independent_of_condition_order(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert {(c.characteristic_id, c.field, c.value) for c in parsed.must} == {
        ('beach_walk', 'distance_m', 600), ('night_pharmacy', 'distance_m', 250),
        ('car_access', 'parking_distance_m', 90),
    }


@pytest.mark.parametrize(('text', 'arrival', 'departure'), [
    ('с 23 июня по 2 июля 2029, тихо', date(2029, 6, 23), date(2029, 7, 2)),
    ('с 27 декабря 2028 по 3 января 2029, тихо', date(2028, 12, 27), date(2029, 1, 3)),
    ('с 3 по 11 апреля 2028, тихо', date(2028, 4, 3), date(2028, 4, 11)),
])
def test_dates_keep_each_named_month_and_shared_explicit_year(catalog, text, arrival, departure):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert (parsed.check_in, parsed.check_out, parsed.month) == (arrival, departure, arrival.month)


def test_invalid_arrival_does_not_become_the_departure_date(catalog):
    parsed = parse_query(SearchRequest(text='с 2028-02-31 по 2028-03-05, тихо'), catalog)
    assert parsed.check_in is None and parsed.check_out is None
    assert any('календаре' in message for message in parsed.clarifications)


@pytest.mark.parametrize('text', [
    'бар в номере', 'мини-бар в номере', 'зарядка для телефона',
    'будем делать зарядку по утрам', 'площадка для вертолета', 'северный олень на картине',
    'море проблем', 'вид на город', 'без переходов через оживленную дорогу',
    'удаленный от центра отель', 'работа рецепции круглосуточно', 'кошка остается дома',
])
def test_homonyms_do_not_create_hotel_criteria(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert not parsed.must and not parsed.nice


def test_declined_balcony_does_not_replace_a_hard_floor(catalog):
    parsed = parse_query(SearchRequest(text='Балкон не нужен, номер на 6 этаже'), catalog)
    assert parsed.min_floor == 6
    assert [(c.characteristic_id, c.field) for c in parsed.must] == [('room_orientation', 'available')]
    assert parsed.must[0].scope.floor_min == parsed.must[0].scope.floor_max == 6


def test_ordinal_floor_does_not_use_a_shorter_number_prefix(catalog):
    parsed = parse_query(SearchRequest(text='на пятидесятом этаже с видом на море'), catalog)
    assert parsed.min_floor == 50
    assert all(c.scope.floor_min == 50 for c in parsed.must)


def test_only_explicit_spelling_aliases_are_repaired():
    assert normalize_query_text('тишыны и вай-фай') == 'тишины и wi-fi'
    assert normalize_query_text('море и морс') == 'море и морс'


def test_extended_catalogue_keeps_solar_scope_and_explicit_beach_surface(catalog):
    base = next(spec for spec in catalog if spec.id == 'sea_view')
    solar = base.model_copy(update={'id': 'morning_sun', 'name_ru': 'Солнце утром', 'synonyms': []})
    surface = base.model_copy(update={'id': 'beach_surface', 'name_ru': 'Покрытие пляжа', 'synonyms': [],
                                      'scope': 'hotel', 'search': base.search.model_copy(update={
                                          'kind': 'categorical', 'field': 'surface', 'default_value': 'sand'})})
    parsed = parse_query(SearchRequest(text='утреннее солнце на восточной стороне с 5 этажа в июне, галечный пляж'),
                         [*catalog, solar, surface])
    sun = next(c for c in parsed.must if c.characteristic_id == 'morning_sun')
    beach = next(c for c in parsed.must if c.characteristic_id == 'beach_surface')
    assert sun.value is True and sun.scope.facade == 'E' and sun.scope.floor_min == 5
    assert parsed.month == 6 and beach.field == 'surface' and beach.value == 'pebblestone'


def test_negative_corpus_false_criteria_rate_is_at_most_one_percent(catalog):
    rows = load_jsonl(ROOT/'data/eval/queries_negative.jsonl')
    false_queries = []
    for row in rows:
        parsed = parse_query(SearchRequest(text=row['text']), catalog)
        if parsed.must or parsed.nice or parsed.no_data:
            false_queries.append(row['text'])
    assert len(false_queries)/len(rows) <= .01, false_queries
