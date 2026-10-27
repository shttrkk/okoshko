"""Authored product regression cases created after feedback, before implementation.

These examples are open known-answer development checks, not independent holdout.
"""
from backend.contracts import RoomScope


def criterion(cid, field='available', op='eq', value=True):
    return dict(characteristic_id=cid, field=field, op=op, value=value)


def room(label, *must, scope=None, **extra):
    return dict(label=label, must=list(must), scope=scope or {}, **extra)


QUIET = criterion('quiet_night')
SEA = criterion('sea_view')
MOUNTAINS = criterion('mountain_view')
SUNSET = criterion('sunset_view')
BALCONY = criterion('room_orientation', 'balcony')
BEACH = criterion('beach_walk', 'minutes', 'lte', 10)


def case(number, text, rooms, must=()):
    return dict(id=f'product-{number:03}', text=text,
                expected=dict(rooms=rooms, must=list(must), nice=[]))


PRODUCT_CASES = [
    case(1, 'Элина: тихий номер; Рустам: вид на море.',
         [room('Элина', QUIET), room('Рустам', SEA)]),
    case(2, 'Злата — вид на горы, Глеб — балкон.',
         [room('Злата', MOUNTAINS), room('Глеб', BALCONY)]),
    case(3, 'Никита хочет тихий номер, Таисия хочет вид на закат.',
         [room('Никита', QUIET), room('Таисия', SUNSET)]),
    case(4, 'Эмме нужен балкон; Платону важно тихо.',
         [room('Эмме', BALCONY), room('Платону', QUIET)]),
    case(5, 'Наталья Петровна: тихо, Денис Андреевич: вид на море.',
         [room('Наталья Петровна', QUIET), room('Денис Андреевич', SEA)]),
    case(6, 'Тихон: вид на море; Майя: тихо.',
         [room('Тихон', SEA), room('Майя', QUIET)]),
    case(7, 'КСЕНИЯ: балкон; БОРИС: вид на горы.',
         [room('КСЕНИЯ', BALCONY), room('БОРИС', MOUNTAINS)]),
    case(8, 'Мирослав просит тихий номер; Лия предпочитает вид на море.',
         [room('Мирослав', QUIET), room('Лия', SEA)]),
    case(9, 'Нужны два номера в одном отеле.',
         [room('Номер 1'), room('Номер 2')]),
    case(10, 'Нужны два номера в одном отеле: первый тихий, второй с видом на море.',
         [room('Первый', QUIET), room('Второй', SEA)]),
    case(11, 'Два номера в одной гостинице: один тихий, другой с балконом.',
         [room('Один', QUIET), room('Другой', BALCONY)]),
    case(12, 'Ищем отель: Элина хочет джакузи, Рустам хочет вид на море, всем до пляжа 10 минут.',
         [room('Элина', unmapped=['джакузи']), room('Рустам', SEA)], [BEACH]),
]

PRODUCT_TRAPS = [
    dict(id=f'product-trap-{i:03}', text=text, expected={'rooms': []})
    for i, text in enumerate([
        'Нужен один общий номер: маме тихо, нам вид на море.',
        'Берём одну общую комнату: маме тихо, детям вид на горы.',
        'Один семейный номер, родителям тихо, нам балкон.',
        'Один номер на всех: Элина хочет тихо, Рустам хочет вид на море.',
        'Живём в одном семейном номере: маме тихо, мне вид на закат.',
        'Семейный номер для всех: родителям тихо, нам вид на море.',
        'Отдельные номера не нужны, нужен один общий: маме тихо, нам вид на море.',
        'Нужен только один номер, для мамы тихо, для нас вид на море.',
        'Маме дали тихий номер, нам предложили вид на море.',
        'Маме заселили тихий номер, папе предложили номер с балконом.',
        'Родителям вчера предложили тихий номер, нам уже дали вид на море.',
        'Для мамы дали тихий номер, для нас предложили балкон.',
        'Эмме: дали тихий номер; Платону: предложили вид на море.',
        'Яна: в прошлом году был тихий номер; Михаил: раньше был вид на море.',
        'Маме не дали тихий номер, нам не предложили балкон.',
        'Мама сказала: «Элина: тихо, Рустам: вид на море».',
        'Переведи: «Никита хочет тихо, Таисия хочет вид на море».',
        'Сентябрь: тихо; Октябрь: вид на закат.',
        'Вид: на море; Тишина: обязательно.',
        'Обязательно: тихо; Желательно: вид на море.',
        'Сочи: море из окна; Адлер: горы из окна.',
        'Даты: июль; Пожелания: тихо.',
        'Море: обязательно; Балкон: желательно.',
        'Элина сказала, что было тихо, Рустам рассказал о виде на море.',
    ], 1)
]


def test_product_regression_fixture_has_unique_ids_and_valid_domains():
    rows = [*PRODUCT_CASES, *PRODUCT_TRAPS]
    assert len({row['id'] for row in rows}) == len(rows)
    assert len(PRODUCT_CASES) == 12 and len(PRODUCT_TRAPS) == 24
    for row in PRODUCT_CASES:
        for request in row['expected']['rooms']:
            RoomScope.model_validate(request['scope'])


# Assertions are enabled after the known-answer rows were frozen in 2bf4750.
import pytest  # noqa: E402

from backend.agents.query_parser import _fallback, parse_query  # noqa: E402
from backend.contracts import SearchRequest  # noqa: E402
from scripts.group_parser_eval import compare_case  # noqa: E402
from scripts.parser_eval import catalogue  # noqa: E402


@pytest.fixture(scope='module')
def product_catalog():
    return catalogue()


@pytest.mark.parametrize('row', PRODUCT_CASES, ids=lambda row: row['id'])
def test_product_group_semantics_and_labels(row, product_catalog):
    actual = parse_query(SearchRequest(text=row['text']), product_catalog).model_dump(mode='json')
    report = compare_case(row, actual, {spec.id: spec.search.field for spec in product_catalog})
    assert report['semantic_exact'] and report['labels_exact'], report


@pytest.mark.parametrize('row', PRODUCT_TRAPS, ids=lambda row: row['id'])
def test_product_single_room_reports_and_service_keys_keep_legacy_path(row, product_catalog):
    request = SearchRequest(text=row['text'])
    parsed = parse_query(request, product_catalog)
    assert not parsed.rooms
    assert parsed.model_dump() == _fallback(request, product_catalog).model_dump()


@pytest.mark.parametrize('suffix', ['в одном корпусе', 'в одной поездке', 'Первый завтрак в семь утра'])
def test_room_count_does_not_turn_global_ordinals_into_requests(suffix, product_catalog):
    parsed = parse_query(SearchRequest(text=f'Нужны два номера. {suffix}.'), product_catalog)
    assert [request.label for request in parsed.rooms] == ['Номер 1', 'Номер 2']
    assert all(not request.must and not request.nice and not request.unmapped for request in parsed.rooms)


def test_explicit_plural_request_takes_precedence_over_one_member_room_description(product_catalog):
    parsed = parse_query(SearchRequest(text='Нужны два номера. Элине нужен один семейный номер, Рустаму нужен тихий номер.'), product_catalog)
    assert [request.label for request in parsed.rooms] == ['Элине', 'Рустаму']


def test_explicit_separate_request_is_not_suppressed_by_another_common_room(product_catalog):
    parsed = parse_query(SearchRequest(text='Элине нужен один общий номер, Рустаму нужен отдельный номер с видом на море.'), product_catalog)
    assert [request.label for request in parsed.rooms] == ['Элине', 'Рустаму']


def test_count_heading_is_a_service_label_even_with_title_capitalization(product_catalog):
    parsed = parse_query(SearchRequest(text='Два Номера: первый тихий, второй с видом на море.'), product_catalog)
    assert [request.label for request in parsed.rooms] == ['Первый', 'Второй']


def test_past_assignment_cannot_become_a_shared_current_requirement(product_catalog):
    parsed = parse_query(SearchRequest(text='Нужны два номера: маме дали тихий номер. Для Элины вид на море, для Рустама балкон.'), product_catalog)
    assert [request.label for request in parsed.rooms] == ['Для Элины', 'Для Рустама']
    assert not parsed.must
    assert all(c.characteristic_id != 'quiet_night' for request in parsed.rooms for c in request.must)


def test_count_inside_a_past_assignment_is_not_a_new_room_request(product_catalog):
    parsed = parse_query(SearchRequest(text='Эмме дали два номера; Платону предложили три номера.'), product_catalog)
    assert not parsed.rooms


def test_person_label_does_not_supply_a_travel_month(product_catalog):
    parsed = parse_query(SearchRequest(text='Тихон: вид на море; Майя: тихо.'), product_catalog)
    assert len(parsed.rooms) == 2 and parsed.month == 9


def test_past_assignment_date_does_not_override_current_trip_month(product_catalog):
    parsed = parse_query(SearchRequest(text='Маме в прошлом июле дали тихий номер. Для Элины вид на море; для Рустама балкон.'), product_catalog)
    assert len(parsed.rooms) == 2 and parsed.month == 9


def test_reported_room_feature_list_does_not_leak_into_following_current_requests(product_catalog):
    parsed = parse_query(SearchRequest(text='Нужны два номера: маме дали тихий номер, с балконом, для Элины нужен вид на море, для Рустама нужно тихо.'), product_catalog)
    assert [request.label for request in parsed.rooms] == ['Для Элины', 'Для Рустама']
    assert not parsed.must
    assert [c.characteristic_id for c in parsed.rooms[0].must] == ['sea_view']
    assert [c.characteristic_id for c in parsed.rooms[1].must] == ['quiet_night']


def test_public_company_helper_produces_two_personal_editors_and_one_shared_route(product_catalog):
    parsed = parse_query(SearchRequest(text='Едем компанией: Маше — тихий номер, Игорю — вид на море. Для всех — до пляжа не больше 10 минут'), product_catalog)
    assert [request.label for request in parsed.rooms] == ['Маше', 'Игорю']
    assert [c.characteristic_id for c in parsed.rooms[0].must] == ['quiet_night']
    assert [c.characteristic_id for c in parsed.rooms[1].must] == ['sea_view']
    assert [(c.characteristic_id, c.field, c.op, c.value) for c in parsed.must] == [('beach_walk', 'minutes', 'lte', 10)]
    assert not parsed.unmapped
