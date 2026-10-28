"""Round-three known answers, separate from the frozen evaluation labels."""
from datetime import timedelta

import pytest

from backend.agents.query_match import assess_hotel
from backend.agents.query_parser import parse_query
from backend.agents.ranker import rank_hotels
from backend.agents.similarity import similar_hotels
from backend.contracts import (
    Criterion,
    Evidence,
    Fact,
    Hotel,
    ParsedQuery,
    RoomRequest,
    Scope,
    SearchRequest,
    now,
)
from scripts.parser_eval import catalogue


@pytest.fixture(scope='module')
def specs():
    return catalogue()


def hotel(identifier='h'):
    return Hotel(id=identifier, name=identifier, lat=43.58, lon=39.72, floors=5)


def fact(cid, value, *, hotel_id='h', kind='calculation', confidence=.66, **updates):
    identity = hotel_id + '-' + cid
    return Fact(id=identity, hotel_id=hotel_id, characteristic_id=cid, value=value,
                status='confirmed' if kind == 'hotelier' else 'computed', confidence=confidence,
                supporting_claim_ids=['claim-' + identity], valid_until=now() + timedelta(days=1),
                evidence=[Evidence(id='ev-' + identity, kind=kind, label='Known-answer test fixture',
                                   source_ids=['test'], quote='Да, подтверждаем' if kind == 'hotelier' else None,
                                   artifact='/tests/known-answer.json')]).model_copy(update=updates)


def criterion(cid, field='available', op='eq', value=True):
    return Criterion(characteristic_id=cid, label=cid, field=field, op=op, value=value)


def evaluate(facts, required, *, text=''):
    query = ParsedQuery(text=text, must=required)
    return rank_hotels([hotel()], facts, query, [])[0]


@pytest.mark.parametrize('text', [
    'Младший в коляске, море недалеко', 'Едем с коляской',
    'С ребёнком и коляской до пляжа без лестниц и перехода трассы',
])
def test_stated_stroller_trip_adds_mandatory_accessibility(text, specs):
    parsed = parse_query(SearchRequest(text=text), specs)
    assert [(c.field, c.value) for c in parsed.must if c.characteristic_id == 'accessible_route'] == [('available', True)]
    assert not any(c.characteristic_id == 'accessible_route' for c in parsed.nice)


@pytest.mark.parametrize('text', [
    'Едем без коляски', 'Коляску не берём', 'Что значит «в коляске»?',
    'Слово «коляска» в названии бренда', 'Если поедем с коляской',
])
def test_other_stroller_mentions_do_not_create_a_requirement(text, specs):
    parsed = parse_query(SearchRequest(text=text), specs)
    assert 'accessible_route' not in {c.characteristic_id for c in [*parsed.must, *parsed.nice]}


@pytest.mark.parametrize('text', ['Прокат коляски рядом', 'Аренда инвалидной коляски',
                                  'Пересказываю: «Едем с коляской»', 'Цитирую рассказ про поездку в коляске'])
def test_rental_and_reported_speech_do_not_state_our_stroller_trip(text, specs):
    parsed = parse_query(SearchRequest(text=text), specs)
    assert not parsed.must and not parsed.nice


@pytest.mark.parametrize('text', ['центр', 'в центре города', 'центральный район'])
def test_central_district_is_one_visible_criterion_without_residual_words(text, specs):
    parsed = parse_query(SearchRequest(text=text), specs)
    assert [(c.characteristic_id, c.field, c.value) for c in parsed.must] == [('location_access', 'in_central_sochi', True)]
    assert not parsed.unmapped


@pytest.mark.parametrize('text', [
    'Едем втроём с подругами и с собакой (15 кг) на неделю. Хотим жить в центре, чтобы рядом были бары и кафе. Мне нужен быстрый интернет, я работаю удалённо.',
    'Втроём с собакой, центр, хороший вайфай',
])
def test_dinas_verbatim_queries_keep_the_center_visible(text, specs):
    parsed = parse_query(SearchRequest(text=text), specs)
    center = [c for c in parsed.must if c.characteristic_id == 'location_access']
    assert len(center) == 1 and center[0].field == 'in_central_sochi'


def test_relative_center_without_a_threshold_is_explicitly_unmapped(specs):
    parsed = parse_query(SearchRequest(text='Ближе к центру'), specs)
    assert not parsed.must and not parsed.nice
    assert any('ближе к центру' in fragment.casefold() for fragment in parsed.unmapped)
    assert any('измеримый порог' in note for note in parsed.clarifications)


def test_numeric_center_route_does_not_duplicate_the_central_district(specs):
    parsed = parse_query(SearchRequest(text='Ближе к центру, до центра не больше 15 минут'), specs)
    center = [c for c in parsed.must if c.characteristic_id == 'location_access']
    assert len(center) == 1 and center[0].field == 'center_route.minutes' and center[0].value == 15


def test_missing_location_spec_never_silently_discards_center(specs):
    parsed = parse_query(SearchRequest(text='центр'), [s for s in specs if s.id != 'location_access'])
    assert not parsed.must and parsed.unmapped == ['центр']
    assert parsed.clarifications


@pytest.mark.parametrize('text', ['спокойный номер', 'спокойный район', 'тихий район'])
def test_unambiguous_calm_synonyms_are_quiet(text, specs):
    parsed = parse_query(SearchRequest(text=text), specs)
    assert [(c.characteristic_id, c.value) for c in parsed.must] == [('quiet_night', True)]
    assert not parsed.unmapped


def test_real_stairs_reject_accessibility_with_separate_route_context():
    route = fact('beach_walk', {'available': True, 'minutes': 7, 'distance_m': 386, 'has_steps': True, 'return_ascent_m': 37})
    before = route.model_dump()
    hit = evaluate([route], [criterion('accessible_route')])
    [v] = hit.verdicts
    assert v.status == 'no' and 'лестницы' in v.text and hit.group != 'all'
    assert not v.fact_ids and not v.evidence_refs
    assert v.context_fact_ids == [route.id] and v.context_evidence_refs == [route.evidence[0].id]
    assert v.confidence == route.confidence and not v.hotelier_confirmed
    assert route.model_dump() == before


def test_stairs_and_accessibility_cannot_disagree_on_primorskaya_shape():
    route = fact('beach_walk', {'available': True, 'minutes': 7, 'distance_m': 386, 'has_steps': True, 'return_ascent_m': 37})
    walking = criterion('beach_walk', 'minutes', 'lte', 10)
    walking.no_stairs = True
    hit = evaluate([route], [walking, criterion('accessible_route')])
    assert [v.status for v in hit.verdicts] == ['no', 'no']


def test_route_without_stairs_is_partial_and_never_proves_complete_access():
    route = fact('beach_walk', {'available': True, 'distance_m': 196, 'has_steps': False, 'return_ascent_m': 5, 'crossings': []})
    query = ParsedQuery(text='с коляской', must=[criterion('accessible_route')])
    [match] = assess_hotel([route], query, hotel=hotel()).matches
    assert match.status == 'partial' and not match.supported and not match.condition_met
    [v] = evaluate([route], query.must).verdicts
    assert v.status == 'partial' and v.confidence == route.confidence
    assert 'подъём обратно 5 м' in v.text and 'переходов не отмечено' in v.text
    assert 'Бордюры, ширина' in v.text and not v.hotelier_confirmed and not v.fact_ids
    assert v.context_fact_ids == [route.id]


@pytest.mark.parametrize('missing', ['has_steps', 'return_ascent_m', 'crossings'])
def test_incomplete_route_keeps_the_missing_barrier_visible(missing):
    values = {'available': True, 'distance_m': 196, 'has_steps': False, 'return_ascent_m': 5, 'crossings': []}
    del values[missing]
    route = fact('beach_walk', values)
    [v] = evaluate([route], [criterion('accessible_route')]).verdicts
    assert v.status == 'unknown' and 'не хватает сведений' in v.text
    assert v.context_fact_ids == [route.id]


@pytest.mark.parametrize('updates', [
    {'status': 'disputed'}, {'status': 'stale'}, {'synthetic': True},
    {'evidence': []}, {'supporting_claim_ids': []}, {'valid_until': now() - timedelta(seconds=1)},
])
def test_unusable_route_cannot_supply_accessibility(updates):
    route = fact('beach_walk', {'available': True, 'has_steps': True}, **updates)
    [v] = evaluate([route], [criterion('accessible_route')]).verdicts
    assert v.status == 'unknown' and not v.context_fact_ids


def test_real_accessibility_answer_takes_precedence_over_route_fallback():
    route = fact('beach_walk', {'available': True, 'has_steps': True})
    answer = fact('accessible_route', {'available': True}, kind='hotelier', confidence=.9)
    [v] = evaluate([route, answer], [criterion('accessible_route')]).verdicts
    assert v.status == 'yes' and v.hotelier_confirmed and v.fact_ids == [answer.id]


def test_map_wheelchair_tags_alone_do_not_prove_the_whole_accessible_route():
    item = fact('accessible_route', {'available': True, 'wheelchair_suitable': True}, confidence=.9)
    hit = evaluate([item], [criterion('accessible_route')])
    assert hit.verdicts[0].status == 'partial' and hit.group != 'all'


@pytest.mark.parametrize('confidence,status', [(.9, 'yes'), (.65, 'partial')])
def test_explicit_simulated_hotel_accessibility_answer_keeps_demo_semantics(confidence, status):
    item = fact('accessible_route', {'available': True, 'has_steps': False, 'wheelchair_suitable': True},
                kind='simulation', synthetic=True, confidence=confidence)
    item.evidence[0] = item.evidence[0].model_copy(update={
        'source_ids': ['hotelier_simulator'], 'quote': 'До пляжа без ступенек, с коляской проходят свободно.'})
    [v] = evaluate([item], [criterion('accessible_route')]).verdicts
    assert v.status == status and v.synthetic and not v.hotelier_confirmed
    assert v.fact_ids == [item.id] and 'Ответ отеля · демо' in v.caveat


@pytest.mark.parametrize('kind,sources,quote', [
    ('calculation', ['geo_engine'], None),
    ('simulation', ['geo_engine'], 'Геометрический расчёт'),
    ('simulation', ['hotelier_simulator'], None),
    ('simulation', ['hotelier_simulator'], '  '),
    ('mock', ['hotelier_simulator'], 'Да, доступно'),
    ('calculation', ['hotelier_simulator'], 'Да, доступно'),
])
def test_synthetic_map_or_missing_hotel_quote_cannot_prove_accessibility(kind, sources, quote):
    item = fact('accessible_route', {'available': True, 'wheelchair_suitable': True},
                kind=kind, synthetic=True, confidence=.9)
    item.evidence[0] = item.evidence[0].model_copy(update={'source_ids': sources, 'quote': quote})
    hit = evaluate([item], [criterion('accessible_route')])
    assert hit.verdicts[0].status == 'partial' and hit.group != 'all'
    assert not hit.verdicts[0].hotelier_confirmed


@pytest.mark.parametrize('updates', [{'supporting_claim_ids': []}, {'status': 'disputed'}])
def test_simulated_hotel_quote_cannot_bypass_missing_provenance_or_dispute(updates):
    item = fact('accessible_route', {'available': True}, kind='simulation', synthetic=True, confidence=.9, **updates)
    item.evidence[0] = item.evidence[0].model_copy(update={
        'source_ids': ['hotelier_simulator'], 'quote': 'До пляжа с коляской проходят свободно.'})
    [v] = evaluate([item], [criterion('accessible_route')]).verdicts
    assert v.status == 'unknown' and not v.hotelier_confirmed


def test_explicit_wheelchair_barrier_is_not_overridden_by_a_step_free_route():
    item = fact('accessible_route', {'available': False, 'wheelchair_suitable': False}, confidence=.55)
    route = fact('beach_walk', {'available': True, 'has_steps': False, 'return_ascent_m': 5, 'crossings': []})
    [v] = evaluate([item, route], [criterion('accessible_route')]).verdicts
    assert v.status == 'no' and v.fact_ids == [item.id]


@pytest.mark.parametrize('kind', ['mobile', 'fixed'])
def test_regional_speed_tests_are_not_a_measurement_of_hotel_wifi(kind):
    item = fact('connectivity', {'kind': kind, 'download_mbps': 100}, confidence=.9)
    hit = evaluate([item], [criterion('connectivity', 'download_mbps', 'gte', 30)], text='Нужен быстрый Wi-Fi')
    [v] = hit.verdicts
    assert v.status == 'unknown' and 'не подтверждают Wi-Fi' in v.text
    assert '100' not in v.text and '30' not in v.text and hit.group != 'all'


def test_wifi_presence_does_not_present_the_requested_speed_as_a_fact():
    item = fact('connectivity', {'wifi_reported': True, 'download_mbps': None}, kind='open_data', confidence=.55)
    item.evidence[0].source_ids = ['osm_geofabrik']
    [v] = evaluate([item], [criterion('connectivity', 'download_mbps', 'gte', 30)]).verdicts
    assert v.status == 'partial' and v.text == 'Wi-Fi есть по карте · скорость неизвестна'
    assert '30' not in v.text and v.confidence == .55


@pytest.mark.parametrize('name', ['пляж', 'beach', 'OSM osm-way-53086451'])
def test_generic_beach_names_are_human_readable_without_changing_raw_evidence(name):
    route = fact('beach_walk', {'minutes': 2, 'beach_name': name})
    before = route.model_dump()
    [v] = evaluate([route], [criterion('beach_walk', 'minutes', 'lte', 10)]).verdicts
    assert 'до ближайшего пляжа' in v.text and '«' not in v.text and 'osm' not in v.text.casefold()
    assert v.fact_ids == [route.id] and v.evidence_refs == [route.evidence[0].id]
    assert route.model_dump() == before


def test_nearby_bar_is_a_quiet_risk_without_becoming_its_primary_proof():
    quiet = fact('quiet_night', {'available': True}, scope=Scope(facade='W'), confidence=.9)
    bar = fact('nightlife', {'nearest_bar_m': 94}, kind='open_data')
    before = quiet.model_dump(), bar.model_dump()
    hit = evaluate([quiet, bar], [criterion('quiet_night')])
    [v] = hit.verdicts
    assert v.status == 'yes' and v.fact_ids == [quiet.id] and v.evidence_refs == [quiet.evidence[0].id]
    assert v.context_fact_ids == [bar.id] and v.context_evidence_refs == [bar.evidence[0].id]
    assert '94 м' in v.caveat and 'Ночью может быть шумно' in v.caveat
    assert any('Рядом бар' in note for note in hit.disappointments)
    assert (quiet.model_dump(), bar.model_dump()) == before


@pytest.mark.parametrize('updates', [{'status': 'disputed'}, {'synthetic': True}, {'evidence': []},
                                   {'supporting_claim_ids': []}, {'kind': 'simulation'}, {'kind': 'mock'}])
def test_unusable_bar_does_not_create_a_quiet_risk(updates):
    quiet = fact('quiet_night', {'available': True}, confidence=.9)
    bar = fact('nightlife', {'nearest_bar_m': 94}, **updates)
    [v] = evaluate([quiet, bar], [criterion('quiet_night')]).verdicts
    assert not v.context_fact_ids


def test_a_bar_at_the_boundary_and_a_query_without_quiet_do_not_create_the_risk():
    quiet = fact('quiet_night', {'available': True}, confidence=.9)
    bar = fact('nightlife', {'nearest_bar_m': 100}, kind='open_data')
    [v] = evaluate([quiet, bar], [criterion('quiet_night')]).verdicts
    assert not v.context_fact_ids
    hit = evaluate([bar], [criterion('pets')])
    assert not any('бар' in note for note in hit.disappointments)


@pytest.mark.parametrize('group', [False, True])
def test_mandatory_known_failure_is_below_unknown_despite_other_support(group):
    query = ParsedQuery(text='', must=[criterion('pets'), criterion('nightlife', 'venues_500m', 'gte', 30)])
    if group:
        query.rooms = [RoomRequest(label='Для нас', must=[criterion('quiet_night')])]
    items = [fact('pets', {'available': True}, hotel_id='bad', confidence=.9),
             fact('nightlife', {'venues_500m': 9}, hotel_id='bad', confidence=.9)]
    hits = rank_hotels([hotel('bad'), hotel('unknown')], items, query, [])
    assert [h.hotel.id for h in hits] == ['unknown', 'bad']
    assert hits[1].score > hits[0].score


@pytest.mark.parametrize('group', [False, True])
def test_genuine_hotel_reply_breaks_equal_support_before_demo_confidence(group):
    query = ParsedQuery(text='', must=[criterion('pets')])
    if group:
        query.rooms = [RoomRequest(label='Для нас')]
    real = fact('pets', {'available': True}, hotel_id='real', kind='hotelier', confidence=.8)
    demo = fact('pets', {'available': True}, hotel_id='demo', kind='hotelier', confidence=.99, synthetic=True)
    hits = rank_hotels([hotel('demo'), hotel('real')], [demo, real], query, [])
    assert hits[0].hotel.id == 'real' and hits[1].score > hits[0].score
    assert hits[1].verdicts[0].synthetic and not hits[1].verdicts[0].hotelier_confirmed


def test_demo_can_still_improve_rank_and_the_causal_explanation_names_it():
    query = ParsedQuery(text='', must=[criterion('pets')])
    demo = fact('pets', {'available': True}, hotel_id='z-demo', kind='hotelier', confidence=.9, synthetic=True)
    hits = rank_hotels([hotel('a-unknown'), hotel('z-demo')], [demo], query, [])
    assert hits[0].hotel.id == 'z-demo'
    assert hits[0].why_higher == 'Выше в выдаче из-за ответа отеля про размещение с питомцем — это демо-ответ'
    assert 'Ответ отеля · демо' in hits[0].summary or 'ответ отеля · демо' in hits[0].summary


def test_noncausal_demo_does_not_get_credit_for_a_position_it_did_not_change():
    query = ParsedQuery(text='', must=[criterion('pets'), criterion('nightlife', 'venues_500m', 'gte', 30)])
    demo = fact('pets', {'available': True}, hotel_id='a-first', kind='hotelier', confidence=.9, synthetic=True)
    failure = fact('nightlife', {'venues_500m': 9}, hotel_id='z-last', confidence=.9)
    hits = rank_hotels([hotel('a-first'), hotel('z-last')], [demo, failure], query, [])
    assert hits[0].hotel.id == 'a-first' and hits[0].why_higher is None


@pytest.mark.parametrize('kind', ['calculation', 'open_data'])
def test_mixed_demo_and_genuine_evidence_do_not_get_a_false_causal_explanation(kind):
    query = ParsedQuery(text='', must=[criterion('quiet_night')])
    demo = fact('quiet_night', {'available': True}, hotel_id='z-demo', kind='hotelier', confidence=.95, synthetic=True)
    independent = fact('quiet_night', {'available': True}, hotel_id='z-demo', kind=kind, confidence=.9, id='independent')
    demo.evidence.append(independent.evidence[0])
    before = demo.model_dump()
    hits = rank_hotels([hotel('a-unknown'), hotel('z-demo')], [demo, independent], query, [])
    reason = hits[0].why_higher
    assert hits[0].hotel.id == 'z-demo' and 'ответ отеля · демо вместе с' in reason
    assert 'из-за' not in reason and 'только' not in reason
    relative = ' '.join(hits[1].why_not_higher)
    assert 'демо вместе с другими источниками' in relative and 'из-за' not in relative
    assert demo.model_dump() == before


def test_an_independent_real_alternative_prevents_a_false_demo_causal_reason():
    query = ParsedQuery(text='', must=[criterion('quiet_night')])
    demo = fact('quiet_night', {'available': True}, hotel_id='z-demo', kind='hotelier', confidence=.95, synthetic=True)
    independent = fact('quiet_night', {'available': True}, hotel_id='z-demo', confidence=.9, id='independent')
    hits = rank_hotels([hotel('a-unknown'), hotel('z-demo')], [demo, independent], query, [])
    assert hits[0].hotel.id == 'z-demo' and hits[0].why_higher is None


def test_demo_leader_reason_is_independent_of_the_explanation_page_size():
    query = ParsedQuery(text='', must=[criterion('pets')])
    demo = fact('pets', {'available': True}, hotel_id='z-demo', kind='hotelier', confidence=.9, synthetic=True)
    hotels = [hotel('a-unknown'), hotel('z-demo')]
    full = rank_hotels(hotels, [demo], query, [])
    page = rank_hotels(hotels, [demo], query, [], explain_limit=1)
    assert page[0].model_dump() == full[0].model_dump()


def test_constrained_similarity_keeps_unresolved_answerable_conditions(specs):
    hotels = [hotel('reference'), hotel('candidate')]
    facts = [fact('beach_walk', {'minutes': 2}, hotel_id=h.id) for h in hotels]
    query = ParsedQuery(text='', must=[criterion('connectivity', 'download_mbps', 'gte', 30)])
    [hit] = similar_hotels('reference', hotels, facts, specs, parsed=query)
    assert hit.hotel.id == 'candidate' and hit.group == 'unknown'
    assert hit.must_satisfied == 0 and hit.must_total == 1
    assert hit.verdicts[0].characteristic_id == 'connectivity' and hit.verdicts[0].status == 'unknown'
