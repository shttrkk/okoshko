"""Intent/context invariants with fresh wording, separate from frozen corpora."""
from datetime import UTC, datetime, timedelta

import pytest

from backend.agents.query_parser import parse_query
from backend.agents.ranker import rank_hotels
from backend.contracts import Evidence, Fact, Hotel, Scope, SearchRequest
from scripts.parser_eval import catalogue


@pytest.fixture(scope='module')
def catalog():
    return catalogue()


@pytest.mark.parametrize('text', [
    'Исправь грамматику в выражении «солнце утром».',
    'Для школьного доклада нарисовали детскую площадку.',
    'У беспроводных наушников двойная шумоизоляция.',
    'Обновляю прошивку роутера с Wi-Fi.',
    'Поясните кнопки хостинга для парковки домена.',
    'Посчитай буквы в подписи «Ночной закат у моря».',
    'Translate "mountain view" for a vocabulary lesson.',
    'На этикетке корма написано «питание для кошек».',
    'Знакомый написал «нужна тишина»; как ответить?',
    'Вспоминаю отпуск: раньше в отеле была парковка.',
    'В прошлом обсуждали шум поездов и вид на море.',
    'Раньше в командировках было важно жить с парковкой.',
    'Сегодня куча работы; нужно разложить документы.',
])
def test_mention_in_another_task_or_report_is_not_a_hotel_requirement(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert not parsed.must and not parsed.nice


@pytest.mark.parametrize(('text', 'ids'), [
    ('парковка рядом', {'car_access'}),
    ('с собакой 25 кг', {'pets'}),
    ('Маша, нужен тихий номер', {'quiet_night'}),
    ('Хочу «море из окна»', {'sea_view'}),
    ('Слово «балкон» обсуждали; нужна парковка рядом', {'car_access'}),
    ('У наушников шумоизоляция, нужен тихий номер', {'quiet_night'}),
    ('Раньше обсуждали закат, теперь хочу номер с видом на горы', {'mountain_view'}),
    ('Вспоминаю закат и хочу номер с балконом', {'room_orientation'}),
    ('Буду работать удалённо, нужен стабильный интернет', {'connectivity'}),
])
def test_real_requests_survive_bare_fragments_quotes_and_mixed_discourse(catalog, text, ids):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert {c.characteristic_id for c in parsed.must} == ids


def test_discussed_floor_and_month_do_not_become_trip_scope(catalog):
    parsed = parse_query(SearchRequest(text='Разбери грамматику «с седьмого этажа в январе»; нужна парковка'), catalog)
    assert parsed.min_floor is None and parsed.month == 9
    assert [c.characteristic_id for c in parsed.must] == ['car_access']


@pytest.mark.parametrize(('text', 'field', 'op', 'value'), [
    ('Прямое солнце по утрам хотя бы сорок минут', 'visible_minutes', 'gte', 40),
    ('Индекс вечерней экспозиции нужен не выше 0,27', 'exposure_index', 'lte', .27),
    ('Железнодорожный шум не 55 дБ, а 44 дБ', 'night_db_max', 'lte', 44),
    ('Насыщенная вечерняя жизнь: хотя бы сорок заведений', 'venues_500m', 'gte', 40),
    ('До пляжа не длиннее одной тысячи метров', 'distance_m', 'lte', 1000),
    ('Хочу гулять в саду; он должен находиться не дальше 1,5 км от отеля', 'distance_m', 'lte', 1500),
    ('A park within 280 m is required', 'distance_m', 'lte', 280),
])
def test_explicit_typed_measurements_keep_their_field_and_bound(catalog, text, field, op, value):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert [(c.field, c.op, c.value) for c in parsed.must] == [(field, op, value)]


@pytest.mark.parametrize('text', ['парк для прогулок был бы приятным дополнением',
                                 'прямому солнцу утром буду рад', 'балкон — приятный бонус'])
def test_suffix_desire_is_a_preference(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert not parsed.must and len(parsed.nice) == 1


def test_own_connectivity_does_not_request_hotel_connectivity(catalog):
    parsed = parse_query(SearchRequest(text='Буду раздавать интернет сам, гостиница нужна тихая ночью'), catalog)
    assert [c.characteristic_id for c in parsed.must] == ['quiet_night']


def test_accessible_route_does_not_invent_walk_time_or_child_requirement(catalog):
    parsed = parse_query(SearchRequest(text='Проезд коляски по маршруту к морю должен быть подтверждён тегами доступности'), catalog)
    assert [(c.characteristic_id, c.field, c.value) for c in parsed.must] == [('accessible_route', 'available', True)]


def test_scoped_view_needs_no_independent_orientation_fact(catalog):
    parsed = parse_query(SearchRequest(text='вид на море с западного фасада со седьмого этажа'), catalog)
    assert parsed.facade == 'W' and parsed.min_floor == 7
    assert [c.characteristic_id for c in parsed.must] == ['sea_view']
    hotel = Hotel(id='context-hotel', name='Synthetic context', lat=43.58, lon=39.72, floors=8)
    fact = Fact(id='context-sea', hotel_id=hotel.id, characteristic_id='sea_view',
                value={'available': True}, scope=Scope(facade='W', floor_min=7, floor_max=8),
                confidence=.95, status='computed', supporting_claim_ids=['context-claim'],
                evidence=[Evidence(id='context-proof', kind='simulation', label='Synthetic scope fixture', source_ids=['fixture'], quote='Synthetic scoped sea-view observation')],
                valid_until=datetime.now(UTC)+timedelta(days=1))
    hit = rank_hotels([hotel], [fact], parsed, catalog)[0]
    assert hit.group == 'all' and hit.must_satisfied == hit.must_total == 1


@pytest.mark.parametrize('text', ['окна на запад со второго этажа', 'балкон с западной стороны'])
def test_separate_orientation_or_balcony_remains_required(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert len(parsed.must) == 1 and parsed.must[0].characteristic_id == 'room_orientation'


def test_spelled_floor_pair_scopes_the_view_without_extra_condition(catalog):
    parsed = parse_query(SearchRequest(text='Вид на горы из восточного окна, этаж допустим с четвертого по восьмой'), catalog)
    assert len(parsed.must) == 1 and parsed.min_floor == 4
    assert parsed.must[0].scope == Scope(facade='E', floor_min=4, floor_max=8)


@pytest.mark.parametrize('text', ['Подберите варианты с видом на море, пожалуйста',
                                 'Покажите тихие номера', 'Порекомендуйте варианты с балконом'])
def test_neutral_selection_words_do_not_require_clarification(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert parsed.must and not parsed.unmapped and not parsed.clarifications


@pytest.mark.parametrize(('text', 'fragment'), [
    ('Предложите варианты с бассейном', 'бассейном'),
    ('Найдите варианты по цене до 9000 рублей', 'цене'),
    ('Покажи варианты с сауной', 'сауной'),
    ('Подбери варианты с room-service', 'room-service'),
])
def test_neutral_words_do_not_hide_unsupported_wishes(catalog, text, fragment):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert any(fragment in value for value in parsed.unmapped)
    assert not parsed.must and not parsed.nice


@pytest.mark.parametrize('text', ['Наш коллега держит кошку', 'Подруга обычно любит детские площадки',
                                 'В аптеке работает моя сестра', 'Она сейчас работает в ночной аптеке',
                                 'Он любит вид на море', 'Сосед говорит «мне нужна тишина»'])
def test_present_third_person_report_requires_adoption_as_a_trip_condition(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert not parsed.must and not parsed.nice
    assert parsed.unmapped and parsed.clarifications


@pytest.mark.parametrize(('text', 'ids'), [
    ('Сосед любит собак, но мне нужен номер с видом на море', {'sea_view'}),
    ('Нужен балкон, брат живет рядом с аптекой', {'room_orientation'}),
    ('Сестра работает в аптеке и нам нужна парковка рядом', {'car_access'}),
    ('Сестра работает в аптеке и нам нужна аптека рядом', {'night_pharmacy'}),
    ('Сестра работает в аптеке, мне важна тишина ночью', {'quiet_night'}),
    ('Сестра любит собак и поедет с ребёнком', {'family_nearby'}),
    ('Сестра едет с собакой', {'pets'}),
    ('Едем с другом и собакой 25 кг', {'pets'}),
    ('Маша едет с собакой 25 кг', {'pets'}),
])
def test_trip_action_or_separate_desire_keeps_only_adopted_conditions(catalog, text, ids):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert {c.characteristic_id for c in parsed.must} == ids


def test_separate_preference_after_a_present_report_stays_optional(catalog):
    parsed = parse_query(SearchRequest(text='Сосед любит собак, желательно море из окна'), catalog)
    assert not parsed.must and [c.characteristic_id for c in parsed.nice] == ['sea_view']


@pytest.mark.parametrize('text', ['Мне нужен вид на горы для фотографий', 'Хочу вид на море для фотографии',
                                 'Хочу увидеть закат для фотографии', 'Для картины нужен вид на море',
                                 'Нужен балкон для съёмки', 'Для фотографии нужен балкон'])
def test_guest_purpose_does_not_replace_the_property_being_requested(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert len(parsed.must) == 1 and parsed.must[0].value is True


@pytest.mark.parametrize('text', ['Нужен рисунок с видом на горы', 'Нужен балкон на открытке',
                                 'Хочу фотографию балкона', 'Фотография морского вида',
                                 'Хочу вид на горы в красивой картине',
                                 'Нужна открытка с песчаным пляжем', 'Хочу фотографию парковки',
                                 'Жук на балконе фотографии'])
def test_a_property_depicted_on_the_requested_artifact_is_not_a_hotel_requirement(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert not parsed.must and not parsed.nice and parsed.unmapped


def test_requested_nearby_photos_and_a_separate_property_keep_their_own_heads(catalog):
    parsed = parse_query(SearchRequest(text='Нужны фотографии окрестностей; парковка рядом'), catalog)
    assert {c.characteristic_id for c in parsed.must} == {'nearby_photos', 'car_access'}
