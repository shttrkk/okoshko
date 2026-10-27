"""Authored known answers for the new TZ language and factual boundaries.

These are extraction/consistency tests, not field validation of hotels.
"""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from backend.agents.explainer import verdict
from backend.agents.query_match import assess_hotel
from backend.agents.query_parser import parse_query
from backend.agents.query_rules import _valid_shape
from backend.agents.ranker import rank_hotels
from backend.agents.resolver import resolve_claims
from backend.contracts import (
    CharacteristicSpec,
    Claim,
    Criterion,
    Evidence,
    Fact,
    Hotel,
    ParsedQuery,
    Scope,
    SearchRequest,
    SearchSpec,
    ToolCall,
)

ROOT = Path(__file__).resolve().parents[1]
AT = datetime.now(UTC)


def catalogue():
    specs = {s.id: s for path in sorted((ROOT / 'data/characteristics').glob('*.yaml'))
             if (s := CharacteristicSpec.model_validate(yaml.safe_load(path.read_text())))}
    for path in (ROOT / 'data/characteristics/tz_draft').glob('*.yaml'):
        spec = CharacteristicSpec.model_validate(yaml.safe_load(path.read_text()))
        specs.setdefault(spec.id, spec.model_copy(update={'status': 'active'}))
    for cid in ('location_access', 'hotel_parking', 'mobile_internet', 'photo_features', 'events_nearby'):
        specs.setdefault(cid, CharacteristicSpec(id=cid, name_ru=cid, description='Known-answer test catalogue',
            value_schema={'type': 'object', 'properties': {'radio_generation': {'type': ['string', 'null']}}},
            methods=[ToolCall(tool='prepared_observation', params={'kind': cid})],
            search=SearchSpec(field='download_mbps', default_op='gte', default_value=20) if cid == 'mobile_internet' else SearchSpec(),
            status='active'))
    return list(specs.values())


def parse(text):
    return parse_query(SearchRequest(text=text), catalogue())


def fact(cid, value, *, identity='witness', confidence=.85, status='computed', **scope):
    return Fact(id=identity, hotel_id='h', characteristic_id=cid, value=value, confidence=confidence,
                status=status, scope=Scope(**scope), valid_until=AT + timedelta(days=30),
                supporting_claim_ids=['claim-' + identity],
                evidence=[Evidence(id='e-' + identity, kind='calculation', label='Saved observation',
                                   source_ids=['tz_osm_geofabrik'], artifact='/data/seed/tz_environment_observations.json.gz')])


@pytest.mark.parametrize('text,cid,field,op,value', [
    ('далеко от аэропорта', 'airport_noise', 'runway_distance_m', 'gte', 3000),
    ('рядом с аэропортом', 'airport_noise', 'runway_distance_m', 'lte', 3000),
    ('из аэропорта без пересадок', 'airport_transfer', 'transfers', 'lte', 0),
    ('из аэропорта на транспорте ночью', 'airport_transfer', 'night_service', 'eq', True),
    ('круглосуточный магазин рядом', 'night_open', 'shop.available', 'eq', True),
    ('где поесть ночью', 'night_open', 'food.available', 'eq', True),
    ('магазин ночью до 7 минут', 'night_open', 'shop.minutes', 'lte', 7),
    ('детская больница рядом', 'family_nearby', 'walk_children_hospital.available', 'eq', True),
    ('подъехать к входу', 'car_arrival', 'available', 'eq', True),
    ('своя парковка', 'hotel_parking', 'private_parking', 'eq', True),
    ('мачты связи рядом', 'mobile_masts', 'mapped_count', 'gte', 1),
    ('мобильный интернет 40 Мбит/с', 'mobile_internet', 'download_mbps', 'gte', 40),
    ('нужен 5G', 'mobile_internet', 'radio_generation', 'eq', '5G'),
    ('без стройки рядом', 'construction_nearby', 'available', 'eq', False),
    ('мероприятия рядом', 'events_nearby', 'available', 'eq', True),
    ('терраса на крыше', 'photo_features', 'terrace', 'eq', True),
    ('темно в номере', 'soundproofing', 'blackout', 'eq', True),
    ('затемнение номера', 'soundproofing', 'blackout', 'eq', True),
    ('без плотных штор', 'soundproofing', 'blackout', 'eq', False),
    ('площадка для выгула рядом', 'dog_walk', 'dog_park.available', 'eq', True),
    ('ветклиника рядом до 10 минут', 'veterinary_nearby', 'minutes', 'lte', 10),
    ('зоомагазин рядом', 'pet_shop_nearby', 'available', 'eq', True),
    ('без дождей в октябре', 'climate_days', 'wet_month_proxy', 'eq', False),
    ('солнечные дни', 'climate_days', 'sunny_days', 'gte', 1),
    ('у метро', 'location_access', 'metro.available', 'eq', True),
    ('станция рядом', 'location_access', 'station_route.available', 'eq', True),
    ('до центра 10 минут пешком', 'location_access', 'center_route.minutes', 'lte', 10),
    ('до станции 15 минут', 'location_access', 'station_route.minutes', 'lte', 15),
    ('до остановки 5 минут', 'location_access', 'bus_stop_route.minutes', 'lte', 5),
    ('в центре', 'location_access', 'in_central_sochi', 'eq', True),
    ('private parking', 'hotel_parking', 'private_parking', 'eq', True),
    ('24-hour shop nearby', 'night_open', 'shop.available', 'eq', True),
    ('dog park nearby', 'dog_walk', 'dog_park.available', 'eq', True),
    ('from the airport without transfers', 'airport_transfer', 'transfers', 'lte', 0),
])
def test_explicit_conditions_are_catalogue_typed(text, cid, field, op, value):
    parsed = parse(text)
    selected = [c for c in parsed.must if (c.characteristic_id, c.field, c.op, c.value) == (cid, field, op, value)]
    assert selected, parsed.model_dump()
    spec = next(s for s in catalogue() if s.id == cid)
    assert _valid_shape(selected[0], spec)


@pytest.mark.parametrize('text', [
    'Переведи фразу «вид на Фишт»',
    'Подпись к картине «Морской вокзал»',
    'Blackout poetry book',
    'Сосед рассказал про стройку рядом',
    'Не нужна своя парковка',
    'Круглосуточный магазин не нужен',
])
def test_new_homonyms_reports_and_waivers_do_not_create_requirements(text):
    parsed = parse(text)
    assert not parsed.must and not parsed.nice


def test_sleep_preserves_old_intent_and_offers_explicit_optional_blackout():
    parsed = parse('хочу выспаться')
    assert [c.characteristic_id for c in parsed.must] == ['quiet_night']
    assert not parsed.nice
    assert parsed.suggestions[0].criterion.field == 'blackout'
    assert 'soundproofing' not in {c.characteristic_id for c in [*parsed.must, *parsed.nice]}


@pytest.mark.parametrize('text', ['нужен 5G', 'хорошая мобильная связь 4G', 'мобильный интернет 40 Мбит/с'])
def test_mobile_requirements_do_not_infer_wifi_speed(text):
    parsed = parse(text)
    assert any(c.characteristic_id == 'mobile_internet' for c in parsed.must)
    assert not any(c.characteristic_id == 'connectivity' for c in [*parsed.must, *parsed.nice])


@pytest.mark.parametrize('text', ['не слышно самолетов', 'без шума самолетов', 'no aircraft noise'])
def test_inaudible_planes_is_unknown_hard_condition_and_separate_geometric_preference(text):
    parsed = parse(text)
    acoustic = next(c for c in parsed.must if c.field == 'noise_db')
    assert any(c.field == 'runway_distance_m' for c in parsed.nice)
    observation = fact('airport_noise', {'available': False, 'runway_distance_m': 20000, 'noise_db': None})
    match = assess_hotel([observation], ParsedQuery(text=parsed.text, must=[acoustic])).matches[0]
    assert match.status == 'unknown' and 'не измерялась' in match.reason


def test_near_airport_only_requests_runway_distance():
    parsed = parse('близко к аэропорту')
    assert [(c.characteristic_id, c.field, c.op, c.value) for c in parsed.must] == [
        ('airport_noise', 'runway_distance_m', 'lte', 3000)]


@pytest.mark.parametrize('cid,value,field,target,expected', [
    ('night_open', {'available': True, 'shop': None, 'food': {'available': True, 'minutes': 3}}, 'shop.available', True, 'unknown'),
    ('night_open', {'available': True, 'shop': None, 'food': {'available': True, 'minutes': 3}}, 'food.minutes', 5, 'yes'),
    ('dog_walk', {'available': None, 'dog_park': None, 'ordinary_park_fallback': {'available': True, 'minutes': 2}}, 'dog_park.available', True, 'unknown'),
    ('family_nearby', {'available': False, 'walk_hospital': {'available': True, 'minutes': 2}}, 'walk_children_hospital.available', True, 'unknown'),
    ('airport_transfer', {'available': None, 'transfers': None, 'primary_unknown_reason': 'no_mapped_zero_or_one_transfer_join'}, 'transfers', 0, 'unknown'),
    ('airport_transfer', {'available': True, 'transfers': 0, 'night_service': None}, 'night_service', True, 'unknown'),
    ('construction_nearby', {'available': None, 'mapped_count': 9, 'current_eligible_count': 0}, 'available', False, 'unknown'),
    ('mobile_masts', {'available': True, 'mapped_count': 10, 'quality_unknown': True}, 'available', True, 'unknown'),
    ('mobile_internet', {'available': True, 'download_mbps': 100, 'radio_generation': None}, 'radio_generation', '5G', 'unknown'),
    ('building_history', {'available': True, 'history_information': True, 'inception': '2020', 'historic_designation': None}, 'available', True, 'unknown'),
    ('hotel_parking', {'private_parking': None, 'public_parking_distance_m': 2}, 'private_parking', True, 'unknown'),
    ('location_access', {'metro': {'available': False}, 'station_route': {'available': True, 'minutes': 5}}, 'metro.available', True, 'no'),
])
def test_nested_fields_and_missing_evidence_are_not_promoted(cid, value, field, target, expected):
    criterion = Criterion(characteristic_id=cid, label='Known answer', field=field,
                          op='lte' if isinstance(target, int) and not isinstance(target, bool) else 'eq', value=target)
    parsed = ParsedQuery(text='историческое здание' if cid == 'building_history' else '', must=[criterion])
    match = assess_hotel([fact(cid, value)], parsed).matches[0]
    assert match.status == expected
    rendered = verdict(criterion, match, True, parsed)
    assert '%' not in rendered.text and 'osm-' not in rendered.text


def test_airport_exposure_only_softly_changes_a_requested_quiet_score():
    hotels = [Hotel(id='h', name='Known answer', lat=43.5, lon=39.7, floors=3)]
    quiet = fact('quiet_night', {'available': True})
    risk = fact('airport_noise', {'available': True, 'runway_distance_m': 1000}, identity='airport')
    parsed = ParsedQuery(text='тихо', must=[Criterion(characteristic_id='quiet_night', label='Тишина', field='available')])
    plain = rank_hotels(hotels, [quiet], parsed, catalogue())[0]
    exposed = rank_hotels(hotels, [quiet, risk], parsed, catalogue())[0]
    assert exposed.group == plain.group == 'all'
    assert exposed.score < plain.score
    assert exposed.verdicts[0].fact_ids == [quiet.id]
    assert exposed.verdicts[0].context_fact_ids == [risk.id]
    assert exposed.verdicts[0].context_evidence_refs == [e.id for e in risk.evidence]
    assert exposed.verdicts[0].confidence == plain.verdicts[0].confidence
    assert any('Слышимость самолётов' in note and 'неизвестны' in note for note in exposed.disappointments)
    distant = risk.model_copy(update={'value': {'available': False, 'runway_distance_m': 20000}})
    assert rank_hotels(hotels, [quiet, distant], parsed, catalogue())[0].score == plain.score


@pytest.mark.parametrize('kind,status', [('hotelier', 'computed'), ('calculation', 'stale')])
def test_airport_quiet_penalty_requires_current_geometry(kind, status):
    quiet = fact('quiet_night', {'available': True})
    risk = fact('airport_noise', {'available': True, 'runway_distance_m': 1000}, identity='airport', status=status)
    risk.evidence[0].kind = kind
    criterion = Criterion(characteristic_id='quiet_night', label='Тишина', field='available')
    parsed = ParsedQuery(text='тихо', must=[criterion])
    assert assess_hotel([quiet, risk], parsed).matches[0].utility == assess_hotel([quiet], parsed).matches[0].utility


def test_additive_routes_keep_their_own_confidence_and_override_straight_line_for_matching():
    ev = Evidence(kind='calculation', label='Known path', source_ids=['tz_osm_geofabrik'], artifact='/data/seed/tz_routes_claims.jsonl.gz',
                  inputs={'supplementary_observation': True})
    base = Claim(id='base', hotel_id='h', characteristic_id='family_nearby', value={'available': True, 'playground_distance_m': 100},
        source='baseline', method='baseline@1', confidence=.95, evidence=[ev], observed_at=AT, valid_until=AT+timedelta(days=30))
    route = base.model_copy(update={'id': 'route', 'source': 'tz_osm_geofabrik', 'method': 'tz_supplement:family_nearby@1',
        'value': {'walk_playground': {'available': True, 'distance_m': 700, 'minutes': 12}}, 'confidence': .65})
    facts, conflicts = resolve_claims('h', [base, route], catalogue(), [], at=AT)
    related = [f for f in facts if f.characteristic_id == 'family_nearby']
    assert len(related) == 2 and not conflicts
    assert {f.confidence for f in related} == {.95, .65}
    criterion = Criterion(characteristic_id='family_nearby', label='Площадка', field='playground_distance_m', op='lte', value=500)
    match = assess_hotel(facts, ParsedQuery(text='', must=[criterion])).matches[0]
    assert match.status == 'no' and match.value == 700 and match.confidence == .65


def test_real_hotelier_partial_does_not_receive_supplemental_marker_semantics():
    from backend.agents.resolver import supplemental_observation
    row = Claim(id='owner', hotel_id='h', characteristic_id='building_history', value={'architecture': 'модерн'},
        source='hotelier:1', method='answer@1', confidence=.9,
        evidence=[Evidence(kind='hotelier', label='Answer', source_ids=['hotelier:1'], quote='Модерн', message_id='m')],
        observed_at=AT, valid_until=AT+timedelta(days=30))
    assert not supplemental_observation(row)


def test_history_status_metadata_does_not_hide_independent_architecture_evidence():
    architecture = fact('building_history', {'available': True, 'architecture': 'Art Nouveau'}, confidence=.9)
    metadata = fact('building_history', {'available': None, 'history_information': True, 'historic_designation': None},
                    identity='metadata', confidence=.6)
    criterion = Criterion(characteristic_id='building_history', label='Модерн', field='architecture', value='art_nouveau')
    match = assess_hotel([architecture, metadata], ParsedQuery(text='', must=[criterion])).matches[0]
    assert match.status == 'yes' and match.confidence == .9
    assert match.facts == [architecture]
