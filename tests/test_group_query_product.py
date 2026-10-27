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
