from datetime import timedelta

import pytest
import yaml

from backend.agents import query_parser
from backend.agents.explainer import verdict
from backend.agents.query_match import assess_hotel
from backend.agents.query_parser import parse_query
from backend.agents.resolver import resolve_claims, stale_prepared_landmarks
from backend.agents.spec_compiler import compile_spec
from backend.contracts import Claim, Criterion, ParsedQuery, Scope, SearchRequest
from tests.test_tz_search import AT, catalogue, fact


@pytest.mark.parametrize('text,target', [('вид на Фишт', 'fisht'), ('вид на морской вокзал', 'sea_terminal'),
    ('вид на Зимний театр', 'winter_theatre'), ('вид на Большой', 'bolshoy'),
    ('вид на олимпийский парк', 'olympic_park'), ('view of Fisht', 'fisht')])
def test_named_target_remains_in_criterion_scope(text, target):
    parsed = parse_query(SearchRequest(text=text), catalogue())
    [criterion] = [c for c in parsed.must if c.characteristic_id == 'landmark_view']
    assert criterion.scope.landmark_id == target


def test_distinct_targets_do_not_conflict_and_any_is_existential():
    claims = []
    for target, available in [('sea_terminal', True), ('winter_theatre', False)]:
        witness = fact('landmark_view', {'available': available, 'landmark_id': target, 'name': target},
                       identity=target, facade='W', floor_min=2, floor_max=3, landmark_id=target)
        claims.append(Claim(id='claim-' + target, hotel_id='h', characteristic_id='landmark_view',
            scope=witness.scope, value=witness.value, confidence=.85, evidence=witness.evidence,
            source='tz_osm_geofabrik', method='tz_landmark_los@1', observed_at=AT, valid_until=AT+timedelta(days=30)))
    facts, conflicts = resolve_claims('h', claims, catalogue(), [], at=AT)
    assert not conflicts
    landmarks = [f for f in facts if f.characteristic_id == 'landmark_view']
    assert {f.scope.landmark_id for f in landmarks} == {'sea_terminal', 'winter_theatre'}
    criterion = Criterion(characteristic_id='landmark_view', label='Любой объект', field='available')
    assert assess_hotel(facts, ParsedQuery(text='', must=[criterion])).matches[0].status == 'yes'
    criterion.scope = Scope(landmark_id='winter_theatre', floor_min=2, floor_max=3)
    assert assess_hotel(facts, ParsedQuery(text='', must=[criterion])).matches[0].status == 'no'
    criterion.scope = Scope(landmark_id='fisht')
    match = assess_hotel(facts, ParsedQuery(text='', must=[criterion])).matches[0]
    assert match.status == 'unknown' and 'высоты' in match.reason


def test_fisht_metadata_never_claims_a_calculated_view():
    witness = fact('landmark_view', {'available': None, 'record_type': 'target_metadata', 'name': 'Фишт',
        'unknown_reason': 'Нет подтверждённой высоты; видимость не рассчитана'}, status='unknown', landmark_id='fisht')
    criterion = Criterion(characteristic_id='landmark_view', label='Фишт', scope=Scope(landmark_id='fisht'))
    parsed = ParsedQuery(text='вид на Фишт', must=[criterion])
    match = assess_hotel([witness], parsed).matches[0]
    assert match.status == 'unknown'
    text = verdict(criterion, match, True, parsed).text
    assert 'высоты' in text and 'не рассчитана' in text
    assert 'Рассчитан вид' not in text


def test_generic_landmark_view_is_unknown_if_only_known_target_is_hidden():
    hidden = fact('landmark_view', {'available': False, 'name': 'Морской вокзал'},
                  landmark_id='sea_terminal', facade='W', floor_min=1, floor_max=3)
    unknown = fact('landmark_view', {'available': None, 'record_type': 'target_metadata', 'name': 'Фишт'},
                   identity='unknown', status='unknown', landmark_id='fisht')
    criterion = Criterion(characteristic_id='landmark_view', label='Любая достопримечательность')
    match = assess_hotel([hidden, unknown], ParsedQuery(text='вид на достопримечательность', must=[criterion])).matches[0]
    assert match.status == 'unknown'
    assert 'все объекты не подтверждено' in match.reason


def test_geometry_overlay_stales_the_same_hotel_static_los_and_alias_only():
    old = fact('landmark_view', {'available': True}, landmark_id='sea_terminal')
    alias = old.model_copy(update={'id': 'alias', 'characteristic_id': 'custom_sea_terminal'})
    unrelated = old.model_copy(update={'id': 'other', 'hotel_id': 'other'})
    event = {'id': 'e', 'kind': 'osm_building_new', 'affected_hotel_ids': ['h'],
             'payload': {'geo_artifact': '/data/runtime/geo/changed.json'}}
    after = stale_prepared_landmarks([old, alias, unrelated], [event])
    assert [f.status for f in after] == ['stale', 'stale', 'computed']
    assert old.status == 'computed' and after[0].value == old.value
    assert after[0].confidence == 0 and old.confidence == .85
    assert after[0].evidence == old.evidence
    assert after[0].supporting_claim_ids == old.supporting_claim_ids
    assert stale_prepared_landmarks([old], [])[0].status == 'computed'
    recomputed = old.model_copy(deep=True)
    recomputed.evidence[0].artifact = event['payload']['geo_artifact']
    assert stale_prepared_landmarks([recomputed], [event])[0].status == 'computed'


def test_only_latest_geometry_event_can_validate_a_recalculated_landmark():
    witness = fact('landmark_view', {'available': True}, landmark_id='sea_terminal')
    early = {'id': 'early', 'kind': 'osm_building_new', 'affected_hotel_ids': ['h'],
             'detected_at': '2026-10-26T10:00:00Z', 'payload': {'geo_artifact': '/data/runtime/geo/early.json'}}
    latest = {**early, 'id': 'latest', 'detected_at': '2026-10-27T10:00:00Z',
              'payload': {'geo_artifact': '/data/runtime/geo/latest.json'}}
    witness.evidence[0].inputs['geo_artifact'] = early['payload']['geo_artifact']
    assert stale_prepared_landmarks([witness], [latest, early])[0].status == 'stale'
    witness.evidence[0].inputs['geo_artifact'] = latest['payload']['geo_artifact']
    assert stale_prepared_landmarks([witness], [latest, early])[0].status == 'computed'


def test_studio_text_emits_whitelisted_target_without_height_or_values():
    class Repo:
        def get(self, _table, _key):
            return None

        def all(self, _table):
            return []

    class Registry:
        specs = {'viewshed_facade': None}

        def validate(self, method):
            assert method.tool == 'viewshed_facade'
            assert method.params == {'target': 'landmark', 'landmark_id': 'sea_terminal'}

    spec, mode, _ = compile_spec('вид на морской вокзал', None, Repo(), Registry())
    assert mode == 'offline' and spec.scope == 'floor_range'
    assert 'height' not in spec.methods[0].params
    spec.status = 'active'
    parsed = parse_query(SearchRequest(text='вид на морской вокзал'), [spec])
    assert parsed.must[0].scope.landmark_id == 'sea_terminal'


def test_new_yaml_landmark_and_aliases_work_without_parser_changes(tmp_path, monkeypatch):
    path = tmp_path / 'sochi.yaml'
    row = {'id': 'curated_tower', 'name': 'Проверенная башня Сочи (верх контура)', 'aliases': ['башня Надежды']}
    path.write_text(yaml.safe_dump({'landmarks': [row]}, allow_unicode=True))
    monkeypatch.setattr(query_parser, 'LANDMARK_CATALOGUE', path)
    for text in ('вид на проверенную башню', 'вид на башню Надежды'):
        parsed = parse_query(SearchRequest(text=text), catalogue())
        criterion = next(c for c in parsed.must if c.characteristic_id == 'landmark_view')
        assert criterion.scope.landmark_id == 'curated_tower'
        assert assess_hotel([], parsed).matches[0].status == 'unknown'

    class Repo:
        def all(self, _table):
            return []

        def get(self, _table, _key):
            return None

    class Registry:
        specs = {'viewshed_facade': None}

        def validate(self, method):
            assert method.params == {'target': 'landmark', 'landmark_id': 'curated_tower'}

    spec, _, _ = compile_spec('вид на башню Надежды', None, Repo(), Registry())
    assert spec.scope == 'floor_range' and 'height' not in spec.methods[0].params

    row['aliases'] = ['башня Аврора']
    path.write_text(yaml.safe_dump({'landmarks': [row]}, allow_unicode=True))
    parsed = parse_query(SearchRequest(text='вид на башню Аврора'), catalogue())
    assert parsed.must[0].scope.landmark_id == 'curated_tower'
    assert not query_parser.landmark_mentions('башня надежды')


def test_group_landmarks_retain_the_target_with_an_explicit_room_facade():
    parsed = parse_query(SearchRequest(text='Два номера: нам вид на морской вокзал с западной стороны; маме вид на Зимний театр'), catalogue())
    assert parsed.rooms
    targets = {c.scope.landmark_id for room in parsed.rooms for c in room.must
               if c.characteristic_id == 'landmark_view' and c.scope}
    assert targets == {'sea_terminal', 'winter_theatre'}
