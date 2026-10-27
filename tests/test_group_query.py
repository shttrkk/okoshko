"""Separate wishes retain their owner, domain, unsupported hard conditions and dates."""
import json
from pathlib import Path

import pytest

from backend.agents.group_query import room_query
from backend.agents.query_parser import _fallback, parse_query
from backend.agents.query_rules import apply_region_coverage
from backend.contracts import Criterion, ParsedQuery, RoomRequest, RoomScope, SearchRequest
from scripts.parser_eval import catalogue


@pytest.fixture(scope='module')
def catalog():
    return catalogue()


def meanings(values):
    return {(c.characteristic_id, c.field, c.op, json.dumps(c.value)) for c in values}


def test_family_wishes_keep_personal_floor_and_shared_hotel_needs(catalog):
    parsed = parse_query(SearchRequest(text='Нам с мужем тихо и вид на море, маме без лестниц до пляжа и не выше 3 этажа, детям площадка рядом.'), catalog)
    assert [r.label for r in parsed.rooms] == ['Нам с мужем', 'Маме', 'Детям']
    assert {c.characteristic_id for c in parsed.rooms[0].must} == {'quiet_night', 'sea_view'}
    assert parsed.rooms[1].scope == RoomScope(floor_max=3)
    assert not parsed.rooms[1].must and not parsed.rooms[2].must
    assert {c.characteristic_id for c in parsed.must} == {'beach_walk', 'family_nearby'}
    assert next(c for c in parsed.must if c.characteristic_id == 'beach_walk').no_stairs
    assert not parsed.min_floor and not parsed.facade


def test_courtyard_is_unknown_required_not_cardinal_evidence(catalog):
    parsed = parse_query(SearchRequest(text='Два номера: один с видом на море, второй тихий во двор.'), catalog)
    second = parsed.rooms[1]
    assert not second.scope.facade
    assert meanings(second.no_data) == {('room_orientation', 'courtyard', 'eq', 'true')}
    assert second.no_data_must == second.no_data
    apply_region_coverage(parsed, {})
    assert second.no_data_must == second.no_data
    assert not parsed.no_data_must


def test_number_of_people_never_creates_room_inventory(catalog):
    parsed = parse_query(SearchRequest(text='Семья из 5 человек: бабушке низкий этаж, остальным не принципиально. Едем с собакой.'), catalog)
    assert len(parsed.rooms) == 2
    assert parsed.rooms[0].scope == RoomScope()
    assert 'низкий этаж' in parsed.rooms[0].unmapped
    assert parsed.rooms[0].clarifications
    assert not parsed.rooms[1].must
    assert [c.characteristic_id for c in parsed.must] == ['pets']


def test_common_route_is_not_duplicated_for_each_room(catalog):
    parsed = parse_query(SearchRequest(text='Родителям тихо, нам с другом вид на горы, всем до пляжа 10 минут.'), catalog)
    assert len(parsed.must) == 1
    assert parsed.must[0].characteristic_id == 'beach_walk'
    assert all(all(c.characteristic_id != 'beach_walk' for c in r.must) for r in parsed.rooms)


@pytest.mark.parametrize('count', [13, 25, 100])
def test_over_twelve_explicit_rooms_are_not_silently_truncated(count, catalog):
    parsed = parse_query(SearchRequest(text=f'Нужны {count} номеров, тихо и вид на море.'), catalog)
    assert parsed.rooms == []
    assert any('до 12' in c for c in parsed.clarifications)
    assert parsed.unmapped


def test_twelve_requests_are_allowed_but_are_only_wishes(catalog):
    parsed = parse_query(SearchRequest(text='Нужны 12 номеров.'), catalog)
    assert len(parsed.rooms) == 12
    assert all(not room.must and room.scope == RoomScope() for room in parsed.rooms)


def test_provider_cannot_erase_groups_or_unsupported_hard_condition(catalog):
    class Provider:
        def structured(self, *args, **kwargs):
            pytest.fail('Explicit group extraction is offline and bypasses the provider')
    parsed = parse_query(SearchRequest(text='Маме тихо во двор; мне вид на море.'), catalog, Provider())
    assert parsed.mode == 'offline'
    assert len(parsed.rooms) == 2
    assert parsed.rooms[0].no_data_must


def test_editor_groups_and_unknown_required_ids_are_preserved(catalog):
    unknown = Criterion(characteristic_id='unsupported_accessibility', label='Лифт без порога')
    edited = ParsedQuery(text='Исходный текст без групп', rooms=[
        RoomRequest(label='Родители', must=[unknown], scope=RoomScope(floor_max=3)),
        RoomRequest(label='Мы', nice=[Criterion(characteristic_id='sea_view', label='Море')])])
    request = SearchRequest(text='Теперь текст другой', parsed=edited)
    result = parse_query(request, catalog)
    assert result.rooms == edited.rooms
    assert result.text == request.text
    apply_region_coverage(result, {'unsupported_accessibility': {'status': 'no_data'}})
    assert result.rooms[0].no_data_must == [unknown]
    assert not edited.rooms[0].no_data  # The request object was not mutated.


def test_coverage_priority_change_and_removal_work_inside_one_room(catalog):
    criterion = Criterion(characteristic_id='sea_view', label='Море')
    parsed = ParsedQuery(text='', rooms=[RoomRequest(label='Мы', no_data=[criterion], no_data_must=[criterion])])
    parsed.rooms[0].nice = [criterion]
    apply_region_coverage(parsed, {'sea_view': {'status': 'no_data'}})
    assert parsed.rooms[0].no_data == [criterion] and not parsed.rooms[0].no_data_must
    parsed.rooms[0].no_data = []
    apply_region_coverage(parsed, {'sea_view': {'status': 'no_data'}})
    assert not parsed.rooms[0].no_data_must


def test_dates_month_and_scope_are_not_mixed_between_people(catalog):
    parsed = parse_query(SearchRequest(text='В июле: маме тихо не выше 3 этажа; мне вид на море на 6 этаже.'), catalog)
    assert parsed.month == 7
    assert parsed.rooms[0].scope.floor_max == 3
    assert parsed.rooms[1].scope.floor_min == parsed.rooms[1].scope.floor_max == 6
    for room in parsed.rooms:
        flat = room_query(room, parsed)
        assert not flat.rooms and flat.month == 7
    assert parsed.rooms[0].scope.facade is None


def test_unknown_wishes_are_retained_with_their_owner(catalog):
    parsed = parse_query(SearchRequest(text='Маме джакузи, нам вид на море.'), catalog)
    assert 'джакузи' in parsed.rooms[0].unmapped
    assert not parsed.rooms[1].unmapped


def test_every_authored_trap_uses_the_unchanged_single_query_path(catalog):
    path = Path(__file__).resolve().parents[1]/'data/eval/group_traps.jsonl'
    for line in path.read_text().splitlines():
        row = json.loads(line)
        request = SearchRequest(text=row['text'])
        assert parse_query(request, catalog).model_dump() == _fallback(request, catalog).model_dump(), row['id']


@pytest.mark.parametrize('text', ['Нам тихо, нам вид на море.', 'Мама сказала, что маме тихо, нам вид на море.', 'Два номера не нужны, нужен один.', 'Мне нужны не два номера, а один.'])
def test_repeated_recipient_and_reported_or_rejected_rooms_do_not_split(text, catalog):
    assert not parse_query(SearchRequest(text=text), catalog).rooms


def test_adopted_quoted_wish_is_kept_but_quoted_roles_do_not_split(catalog):
    parsed = parse_query(SearchRequest(text='Мне нужен «тихий номер», маме вид на море.'), catalog)
    assert len(parsed.rooms) == 2
    assert parsed.rooms[0].must[0].characteristic_id == 'quiet_night'
    assert not parsed.clarifications


def test_common_ceiling_and_trailing_dates_are_preserved(catalog):
    parsed = parse_query(SearchRequest(text='Для всех не выше 3 этажа: маме тихо; нам море из окна. На 10–17 июля 2027.'), catalog)
    assert parsed.check_in.isoformat() == '2027-07-10'
    assert parsed.check_out.isoformat() == '2027-07-17'
    assert parsed.month == 7
    assert all(room.scope.floor_max == 3 for room in parsed.rooms)
    assert all(all(c.characteristic_id != 'room_orientation' for c in room.must) for room in parsed.rooms)


@pytest.mark.parametrize('floor', ['не выше 3.5 этажа', 'на 0 этаже', 'от 5 по 2 этаж', 'желательно до 3 этажа', 'не на 2 этаже'])
def test_invalid_or_soft_floors_are_retained_without_applying_a_hard_domain(floor, catalog):
    parsed = parse_query(SearchRequest(text=f'Маме {floor}; нам тихо.'), catalog)
    assert parsed.rooms[0].scope == RoomScope()
    assert parsed.rooms[0].unmapped and parsed.rooms[0].clarifications


def test_written_multitoken_room_count_does_not_truncate_to_last_word(catalog):
    parsed = parse_query(SearchRequest(text='Нужны двадцать пять номеров.'), catalog)
    assert not parsed.rooms
    assert any('до 12' in c for c in parsed.clarifications)


def test_orphan_unsupported_hard_room_marker_cannot_disappear_on_projection():
    unknown = Criterion(characteristic_id='unknown_room_need', label='Неподдерживаемое обязательное условие')
    parsed = ParsedQuery(text='', rooms=[RoomRequest(label='Маме', no_data_must=[unknown])])
    apply_region_coverage(parsed, {})
    assert parsed.rooms[0].no_data == parsed.rooms[0].no_data_must == [unknown]


def test_group_normalization_keeps_the_existing_unknown_city_boundary(catalog):
    parsed = parse_query(SearchRequest(text='В Берлине: маме тихо, нам вид на море.'), catalog)
    assert len(parsed.rooms) == 2
    assert 'region_out_of_scope' in parsed.unmapped
    assert any('Сочи' in message for message in parsed.clarifications)
