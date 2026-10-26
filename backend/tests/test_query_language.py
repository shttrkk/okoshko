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
    ('площадка для детей в нескольких метрах', 'family_nearby', 'available', True),
    ('интернет от 5 МБ/с', 'connectivity', 'download_mbps', 40),
    ('интернет от 0,1 Гбит/с', 'connectivity', 'download_mbps', 100),
])
def test_unit_conversion_and_conservative_unrecognized_number_words(catalog, text, cid, field, value):
    parsed = parse_query(SearchRequest(text=text), catalog)
    conditions = [c for c in parsed.must if c.characteristic_id == cid]
    assert len(conditions) == 1 and conditions[0].field == field and conditions[0].value == value
    if 'нескольких' in text:
        assert any('нескольких' in fragment for fragment in parsed.unmapped)


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


@pytest.mark.parametrize('text', [
    'вид на море, западная сторона не нужна',
    'вид на море, с 5 этажа неважно',
])
def test_declined_room_scope_does_not_constrain_other_hard_conditions(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert parsed.facade is None and parsed.min_floor is None
    assert [(c.characteristic_id, c.scope) for c in parsed.must] == [('sea_view', None)]


def test_excluded_facade_is_an_explicit_limitation_instead_of_an_inverted_hotel_trait(catalog):
    parsed = parse_query(SearchRequest(text='без западной стороны, морской вид'), catalog)
    assert parsed.facade is None and [c.characteristic_id for c in parsed.must] == ['sea_view']
    assert any('Исключение' in fragment for fragment in parsed.unmapped)
    assert any('пока не применяется' in message for message in parsed.clarifications)


def test_adult_wheelchair_does_not_invent_a_family_persona(catalog):
    parsed = parse_query(SearchRequest(text='до пляжа на инвалидной коляске'), catalog)
    assert 'family_nearby' not in {c.characteristic_id for c in parsed.must}
    assert parsed.persona is None
    assert any(c.characteristic_id == 'beach_walk' and c.no_stairs for c in parsed.must)


def test_pet_staying_home_does_not_set_a_pet_persona_or_weight(catalog):
    parsed = parse_query(SearchRequest(text='собаку 14 кг оставим дома'), catalog)
    assert not parsed.must and parsed.persona is None and parsed.pet_weight_kg is None


def test_catalogue_spelling_hint_keeps_the_original_unknown_condition(catalog):
    parsed = parse_query(SearchRequest(text='шумоизолляция'), catalog)
    assert parsed.unmapped == ['шумоизолляция'] and not parsed.must and not parsed.nice
    assert any('Возможно' in message and 'Шумоизоляция и окна' in message for message in parsed.clarifications)
    assert len(parsed.clarifications) <= 2


def test_short_unknown_amenity_is_not_converted_into_a_catalogue_suggestion(catalog):
    parsed = parse_query(SearchRequest(text='спа'), catalog)
    assert not parsed.must and not parsed.nice and parsed.unmapped == ['спа']
    assert not any('Возможно' in message for message in parsed.clarifications)


@pytest.mark.parametrize(('text', 'cid', 'field', 'value'), [
    ('без парковки', 'car_access', 'available', False),
    ('без интернета', 'connectivity', 'available', False),
    ('без ночной аптеки', 'night_pharmacy', 'available', False),
    ('без баров рядом', 'nightlife', 'bars_500m', 0),
    ('электромобиль без зарядки для электромобиля', 'car_access', 'ev_available', False),
])
def test_explicit_absence_does_not_become_a_positive_default(catalog, text, cid, field, value):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert [(c.characteristic_id, c.field, c.value) for c in parsed.must] == [(cid, field, value)]
    assert 'Без' in parsed.must[0].label or '= 0' in parsed.must[0].label


@pytest.mark.parametrize('text', ['не двойные окна', 'без двойных окон'])
def test_categorical_negation_does_not_guess_a_different_window_type(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert not parsed.must and parsed.unmapped
    assert any('отрицание пока не применяется' in message for message in parsed.clarifications)


@pytest.mark.parametrize(('positive', 'negative', 'cid', 'field'), [
    ('вид на море', 'без вида на море', 'sea_view', 'available'),
    ('кондиционер', 'без кондиционера', 'soundproofing', 'air_conditioning'),
    ('балкон', 'без балкона', 'room_orientation', 'balcony'),
])
@pytest.mark.parametrize('reverse', [False, True])
def test_opposing_hard_boolean_mentions_are_preserved_in_either_order(catalog, positive, negative, cid, field, reverse):
    parts = [positive, negative]
    parsed = parse_query(SearchRequest(text=', '.join(reversed(parts) if reverse else parts)), catalog)
    assert {c.value for c in parsed.must if c.characteristic_id == cid and c.field == field} == {True, False}
    assert any('противоречат' in message for message in parsed.clarifications)


@pytest.mark.parametrize('text', ['желательно вид на море, но обязательно вид на море',
                                 'обязательно вид на море, желательно вид на море'])
def test_a_later_hard_mention_cannot_remain_only_a_preference(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert len(parsed.must) == 1 and parsed.must[0].characteristic_id == 'sea_view'
    assert not parsed.nice


@pytest.mark.parametrize('text', ['номер на 0 этаже', 'номер на -1 этаже',
                                 'номер на 101 этаже', 'номер на 1.5 этаже'])
def test_unsupported_floor_numbers_are_visible_and_never_rounded_or_dropped(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert parsed.min_floor is None and not parsed.must
    assert any('Этаж:' in fragment for fragment in parsed.unmapped)
    assert any('целый этаж от 1 до 100' in message for message in parsed.clarifications)


@pytest.mark.parametrize('text', ['с собакой 150 кг', 'с собакой -5 кг'])
def test_unsupported_pet_weight_is_not_replaced_by_a_weight_free_match(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert parsed.pet_weight_kg is None and not parsed.must
    assert any('Вес питомца:' in fragment for fragment in parsed.unmapped)


def test_enormous_numeric_input_is_bounded_and_clarified(catalog):
    parsed = parse_query(SearchRequest(text='интернет от ' + '9'*400 + ' Mbps'), catalog)
    assert not parsed.must and parsed.unmapped and parsed.clarifications
    assert 'слишком велик' in parsed.clarifications[0]


def test_invalid_number_does_not_hide_an_out_of_scope_destination(catalog):
    parsed = parse_query(SearchRequest(text='в Москве интернет от ' + '9'*400 + ' Mbps'), catalog)
    assert 'region_out_of_scope' in parsed.unmapped and len(parsed.clarifications) <= 2


def test_pharmacy_minutes_and_minimum_distance_keep_explicit_values(catalog):
    parsed = parse_query(SearchRequest(text='до аптеки 6 минут, до моря не ближе 650 метров'), catalog)
    assert {(c.characteristic_id, c.field, c.op, c.value) for c in parsed.must} == {
        ('night_pharmacy', 'minutes', 'lte', 6), ('beach_walk', 'distance_m', 'gte', 650)}


@pytest.mark.parametrize('text', ['тишина ночью желательна', 'закат над морем будет плюсом'])
def test_overlapping_short_match_does_not_erase_a_postfix_preference(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert not parsed.must and len(parsed.nice) == 1


@pytest.mark.parametrize('text', ['интернет, интернет от 100 Мбит/с',
                                 'интернет от 100 Мбит/с, интернет',
                                 'интернет от 30 Мбит/с, интернет от 100 Мбит/с'])
def test_repeated_numeric_requirement_keeps_the_strongest_hard_bound(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert [(c.field, c.op, c.value) for c in parsed.must] == [('download_mbps', 'gte', 100)]


@pytest.mark.parametrize('text', [
    'желательно интернет от 200 Мбит/с, интернет от 100 Мбит/с обязательно',
    'интернет от 100 Мбит/с обязательно, желательно интернет от 200 Мбит/с',
])
def test_each_repeated_numeric_mention_keeps_its_own_priority(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert [(c.field, c.value) for c in parsed.must] == [('download_mbps', 100)]
    assert [(c.field, c.value) for c in parsed.nice] == [('download_mbps', 200)]


def test_later_explicit_beach_limit_is_hard_even_when_the_first_mention_was_soft(catalog):
    parsed = parse_query(SearchRequest(text='желательно до моря 10 минут, обязательно до пляжа 4 минуты'), catalog)
    assert [(c.field, c.value) for c in parsed.must] == [('minutes', 4)]
    assert not parsed.nice


def test_generic_family_requirement_does_not_make_a_later_distance_preference_hard(catalog):
    parsed = parse_query(SearchRequest(text='с ребенком, желательно площадка в 100 м'), catalog)
    assert [(c.field, c.value) for c in parsed.must] == [('available', True)]
    assert [(c.field, c.value) for c in parsed.nice] == [('playground_distance_m', 100)]


@pytest.mark.parametrize('text', ['на машине, желательно зарядка для электромобиля',
                                 'на машине, зарядка для электромобиля желательно'])
def test_optional_ev_charging_does_not_remove_hard_car_parking(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert [(c.field, c.value) for c in parsed.must] == [('parking_distance_m', 500)]
    assert [(c.field, c.value) for c in parsed.nice] == [('ev_distance_m', 1000)]


@pytest.mark.parametrize('text', ['интернет от 75 Мбит/с желательно', 'до пляжа 6 минут желательно'])
def test_a_postfix_preference_can_follow_an_explicit_numeric_limit(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert not parsed.must and len(parsed.nice) == 1


@pytest.mark.parametrize(('text', 'field', 'minimum', 'maximum'), [
    ('вода от 20 до 24 градусов в декабре', 'water_c', 20, 24),
    ('вода 20–24 °C', 'water_c', 20, 24),
    ('интернет от 50 до 100 Мбит/с', 'download_mbps', 50, 100),
    ('до моря 5–10 минут', 'minutes', 5, 10),
    ('парковка от 500 м до 1 км', 'parking_distance_m', 500, 1000),
])
def test_numeric_interval_retains_both_inclusive_bounds(catalog, text, field, minimum, maximum):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert {(c.field, c.op, c.value) for c in parsed.must} == {(field, 'gte', minimum), (field, 'lte', maximum)}


def test_interval_postfix_preference_applies_to_both_bounds(catalog):
    parsed = parse_query(SearchRequest(text='интернет от 50 до 100 Мбит/с желательно'), catalog)
    assert not parsed.must
    assert {(c.op, c.value) for c in parsed.nice} == {('gte', 50), ('lte', 100)}


def test_reversed_interval_is_not_silently_swapped(catalog):
    parsed = parse_query(SearchRequest(text='вода от 24 до 20 градусов'), catalog)
    assert {(c.op, c.value) for c in parsed.must} == {('gte', 24), ('lte', 20)}
    assert any('Минимальный порог выше' in message for message in parsed.clarifications)


@pytest.mark.parametrize('text', ['не без вида на море', 'не хочу номер без вида на море'])
def test_double_negation_does_not_invert_the_requested_sea_view(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert [(c.characteristic_id, c.value) for c in parsed.must] == [('sea_view', True)]


def test_double_negation_of_a_noise_free_phrase_remains_negative_quiet(catalog):
    parsed = parse_query(SearchRequest(text='не без шума'), catalog)
    assert [(c.characteristic_id, c.value) for c in parsed.must] == [('quiet_night', False)]


@pytest.mark.parametrize('text', ['парковка в трех минутах пешком', 'до пляжа 500 шагов',
                                 'интернет 5 GHz', 'вода 80 °F'])
def test_unsupported_measurement_does_not_turn_into_a_positive_default(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert not parsed.must and not parsed.nice
    assert parsed.unmapped and parsed.clarifications


def test_a_beach_time_does_not_become_an_unsupported_family_distance(catalog):
    parsed = parse_query(SearchRequest(text='с ребенком рядом с морем 7 минут'), catalog)
    assert {c.characteristic_id for c in parsed.must} == {'family_nearby', 'beach_walk'}


@pytest.mark.parametrize(('text', 'field', 'value'), [
    ('до пляжа не больше девяноста двух метров', 'distance_m', 92),
    ('парковка не дальше трехсот м', 'parking_distance_m', 300),
    ('интернет от семидесяти пяти мбит/с', 'download_mbps', 75),
    ('К пляжу собираюсь ходить пешком, максимум шесть минут', 'minutes', 6),
    ('парковка поблизости, расстояние максимум 230 м', 'parking_distance_m', 230),
])
def test_bounded_number_words_and_following_measurement_clauses(catalog, text, field, value):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert [(c.field, c.value) for c in parsed.must] == [(field, value)]


def test_explicit_water_temperature_replaces_the_implicit_default(catalog):
    parsed = parse_query(SearchRequest(text='будем купаться, море от 18 градусов устроит'), catalog)
    assert [(c.field, c.op, c.value) for c in parsed.must] == [('water_c', 'gte', 18)]


def test_same_unit_clause_for_another_poi_is_not_attached_to_parking(catalog):
    parsed = parse_query(SearchRequest(text='парковка рядом, до аптеки 200 метров'), catalog)
    assert {(c.field, c.value) for c in parsed.must} == {('parking_distance_m', 500), ('distance_m', 200)}


def test_spelled_ordinal_keeps_a_floor_ceiling(catalog):
    parsed = parse_query(SearchRequest(text='не выше тринадцатого этажа'), catalog)
    assert parsed.min_floor is None and parsed.must[0].scope.floor_max == 13


@pytest.mark.parametrize(('text', 'cid', 'field'), [
    ('Нужен номер, чтобы видеть заходящее солнце', 'sunset_view', 'available'),
    ('Море в окне обязательно', 'sea_view', 'available'),
    ('Светонепроницаемые шторы в номере', 'soundproofing', 'blackout'),
    ('Снимки окрестностей отеля', 'nearby_photos', 'available'),
    ('Европейский AQI не более 28', 'air_quality', 'european_aqi'),
])
def test_explicit_catalogue_paraphrases_keep_their_declared_fields(catalog, text, cid, field):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert [(c.characteristic_id, c.field) for c in parsed.must] == [(cid, field)]


def test_venue_count_is_not_replaced_by_the_radius(catalog):
    parsed = parse_query(SearchRequest(text='нужно хотя бы 35 кафе, ресторанов и баров в полукилометре'), catalog)
    assert [(c.field, c.op, c.value) for c in parsed.must] == [('venues_500m', 'gte', 35)]


@pytest.mark.parametrize('text', ['не пересекая шоссе', 'не переходя автомобильные дороги'])
def test_route_prohibition_is_an_explicit_crossing_constraint(catalog, text):
    parsed = parse_query(SearchRequest(text='маршрут к морю до 8 минут, ' + text), catalog)
    assert len(parsed.must) == 1 and parsed.must[0].value == 8 and parsed.must[0].no_crossings


def test_stairs_prohibition_is_preserved_in_a_separate_clause(catalog):
    parsed = parse_query(SearchRequest(text='лестниц на дороге к пляжу быть не должно, идти до моря 7 минут'), catalog)
    assert len(parsed.must) == 1 and parsed.must[0].value == 7 and parsed.must[0].no_stairs


@pytest.mark.parametrize('text', ['закат будет приятным бонусом', 'вид на горы можно по возможности',
                                 'балкон хотелось бы, но не обязательно'])
def test_explicit_postfix_preferences_do_not_become_hard(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert not parsed.must and len(parsed.nice) == 1


def test_not_interested_with_a_pronoun_does_not_add_sunset(catalog):
    parsed = parse_query(SearchRequest(text='закат меня не интересует, нужен тихий номер'), catalog)
    assert [c.characteristic_id for c in parsed.must] == ['quiet_night']


@pytest.mark.parametrize('text', ['Объясните, что означает термин «парковка»',
                                 'В учебнике цитируется песня «У моря»',
                                 'Рассчитайте площадь общественной парковки',
                                 'Интернет-сервис бронирования обновился',
                                 'Слово «площадка» обозначает портал в договоре'])
def test_metalinguistic_and_artifact_mentions_do_not_create_search_conditions(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert not parsed.must and not parsed.nice


def test_meta_quote_does_not_erase_a_real_requirement_outside_the_quote(catalog):
    parsed = parse_query(SearchRequest(text='Термин «тихий» обсуждали в договоре; нужен тихий номер'), catalog)
    assert [c.characteristic_id for c in parsed.must] == ['quiet_night']


@pytest.mark.parametrize('text', ['Рядом скверный сервис', 'Скверно рядом с отелем'])
def test_green_space_noun_is_not_the_adjective_for_poor_quality(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert 'green_nearby' not in {c.characteristic_id for c in parsed.must+parsed.nice}


@pytest.mark.parametrize('text', ['парк рядом', 'сквер рядом', 'сад рядом', 'зелень рядом'])
def test_declared_green_space_phrases_keep_the_catalogue_default(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert [(c.characteristic_id, c.field, c.value) for c in parsed.must] == [('green_nearby', 'distance_m', 500)]


def test_negative_corpus_false_criteria_rate_is_at_most_one_percent(catalog):
    rows = load_jsonl(ROOT/'data/eval/queries_negative.jsonl')
    false_queries = []
    for row in rows:
        parsed = parse_query(SearchRequest(text=row['text']), catalog)
        if parsed.must or parsed.nice or parsed.no_data:
            false_queries.append(row['text'])
    assert len(false_queries)/len(rows) <= .01, false_queries
