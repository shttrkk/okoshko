"""Additional authored grammar cases, frozen before the second parser implementation.

This is an open synthetic development set, not blind or human guest data.
"""
from backend.contracts import RoomScope


def criterion(cid, field='available', op='eq', value=True, **extra):
    return dict(characteristic_id=cid, field=field, op=op, value=value, **extra)


def room(label, *must, scope=None, **extra):
    return dict(label=label, must=list(must), scope=scope or {}, **extra)


QUIET = criterion('quiet_night')
SEA = criterion('sea_view')
MOUNTAINS = criterion('mountain_view')
SUNSET = criterion('sunset_view')
BALCONY = criterion('room_orientation', 'balcony')
BEACH = criterion('beach_walk', 'minutes', 'lte', 10)
PLAYGROUND = criterion('family_nearby', 'playground_distance_m', 'lte', 500)
COURTYARD = criterion('room_orientation', 'courtyard')


def case(number, text, rooms, must=(), nice=()):
    return dict(id=f'grammar-{number:03}', text=text,
                expected=dict(rooms=rooms, must=list(must), nice=list(nice)))


GRAMMAR_CASES = [
    case(1, 'Едем в Сочи всей семьёй нам с мужем вид на море, маме тихо.',
         [room('Нам с мужем', SEA), room('Маме', QUIET)]),
    case(2, 'Ищем отель для мамы тихий номер, а для нас вид на закат.',
         [room('Для мамы', QUIET), room('Для нас', SUNSET)]),
    case(3, 'Хотим забронировать номера родителям тихо, нам с другом вид на горы.',
         [room('Родителям', QUIET), room('Нам с другом', MOUNTAINS)]),
    case(4, 'Номер 1 — тихий; номер 2 — с видом на море.',
         [room('Номер 1', QUIET), room('Номер 2', SEA)]),
    case(5, 'Два номера: номер 1 с балконом, номер 2 с видом на горы.',
         [room('Номер 1', BALCONY), room('Номер 2', MOUNTAINS)]),
    case(6, 'Комната 1: вид на море. Комната 2: тихо.',
         [room('Комната 1', SEA), room('Комната 2', QUIET)]),
    case(7, 'Нужны две комнаты: одна тихая, другая с видом на море.',
         [room('Одна', QUIET), room('Другая', SEA)]),
    case(8, 'Три комнаты: первая с видом на море, вторая тихая, третья с балконом.',
         [room('Первая', SEA), room('Вторая', QUIET), room('Третья', BALCONY)]),
    case(9, 'Нужны две комнаты без особых пожеланий.',
         [room('Номер 1'), room('Номер 2')]),
    case(10, 'Две комнаты: в одной тихо, в другой вид на закат.',
         [room('В одной', QUIET), room('В другой', SUNSET)]),
    case(11, 'Для каждого из родителей нужен тихий номер, для нас вид на море.',
         [room('Для каждого из родителей', QUIET), room('Для нас', SEA)]),
    case(12, 'Для каждой семьи вид на море; для нашей пары тихо.',
         [room('Для каждой семьи', SEA), room('Для нашей пары', QUIET)]),
    case(13, 'Для Анны тихий номер; для Олега вид на горы.',
         [room('Для Анны', QUIET), room('Для Олега', MOUNTAINS)]),
    case(14, 'Для Иры и Саши вид на море, для Пети балкон.',
         [room('Для Иры и Саши', SEA), room('Для Пети', BALCONY)]),
    case(15, 'Для анны тихо; для олега вид на закат.',
         [room('Для анны', QUIET), room('Для олега', SUNSET)]),
    case(16, 'Тёще тихий номер; свекрови вид на море.',
         [room('Тёще', QUIET), room('Свекрови', SEA)]),
    case(17, 'Сыну вид на горы, дочери тихо.',
         [room('Сыну', MOUNTAINS), room('Дочери', QUIET)]),
    case(18, 'Коллегам тихо; друзьям вид на закат.',
         [room('Коллегам', QUIET), room('Друзьям', SUNSET)]),
    case(19, 'Для коллег тихий номер, для друзей вид на море; всем до пляжа 10 минут.',
         [room('Для коллег', QUIET), room('Для друзей', SEA)], [BEACH]),
    case(20, 'Маме — тихо / нам — вид на море.',
         [room('Маме', QUIET), room('Нам', SEA)]),
    case(21, 'Подберите отель\n• маме тихо\n• нам вид на закат',
         [room('Маме', QUIET), room('Нам', SUNSET)]),
    case(22, 'Для моей мамы тихо; для пожилых родителей не выше 3 этажа; для нас вид на море.',
         [room('Для моей мамы', QUIET), room('Для пожилых родителей', scope={'floor_max': 3}), room('Для нас', SEA)]),
    case(23, 'Для бабушки с дедушкой тихо, для нас с мужем вид на море.',
         [room('Для бабушки с дедушкой', QUIET), room('Для нас с мужем', SEA)]),
    case(24, 'Два номера: №1 тихий; №2 с балконом.',
         [room('№1', QUIET), room('№2', BALCONY)]),
    case(25, 'Нужны две комнаты, для каждого тихо и вид на море.',
         [room('Номер 1'), room('Номер 2')], [QUIET, SEA]),
    case(26, 'Для Ани окна во двор; для Максима тихо.',
         [room('Для Ани', no_data=[COURTYARD], no_data_must=[COURTYARD]), room('Для Максима', QUIET)]),
    case(27, 'Ищем два номера для мамы не выше 3 этажа, для нас вид на море, детям площадка рядом.',
         [room('Для мамы', scope={'floor_max': 3}), room('Для нас', SEA), room('Детям')], [PLAYGROUND]),
    case(28, 'Брату вид на море — сестре тихо.',
         [room('Брату', SEA), room('Сестре', QUIET)]),
    case(29, 'Мне тихо, супруге вид на горы, для всех до пляжа 10 минут.',
         [room('Мне', QUIET), room('Супруге', MOUNTAINS)], [BEACH]),
    case(30, 'Для одного коллеги тихо, для другого коллеги вид на море.',
         [room('Для одного коллеги', QUIET), room('Для другого коллеги', SEA)]),
]

GRAMMAR_TRAPS = [
    dict(id=f'grammar-trap-{i:03}', text=text, expected={'rooms': []})
    for i, text in enumerate([
        'Маме 60 лет, мне 30 лет, едем вместе.',
        'Маме позвонили из отеля, мне написали адрес.',
        'Мама сказала, что для Анны там тихо, для Олега вид на море.',
        'Мама сказала: «номер 1 тихий, номер 2 с балконом».',
        'Переведи фразу «для мамы тихо, для нас море».',
        'Для работы нужен интернет, для сна тишина.',
        'Для прогулок нужен парк, для купания тёплая вода.',
        'Для проверки напиши два номера телефона отеля.',
        'Нужны два номера паспортов.',
        'Два номера журнала лежат у мамы.',
        'В программе два номера: первый танец, второй песня.',
        'Две комнаты в квартире уже покрасили, отель не нужен.',
        'Номер 1 в рейтинге, номер 2 в списке.',
        'Комната 1 на плане, комната 2 на фотографии.',
        'Ищем один номер для Анны и Олега с видом на море.',
        'Мы едем втроём: мама, я и Олег, нужен тихий номер.',
        'Рассказ называется «Для мамы тихо, для нас закат».',
        'В отзыве «Нужны две комнаты: одна тихая, другая с балконом».',
        'Для Анны не нужен отдельный номер, едем вдвоём в один номер.',
        'Нам нужна тишина, нам нужен вид на море.',
    ], 1)
]


def test_authored_grammar_fixture_has_valid_domains_and_unique_ids():
    rows = [*GRAMMAR_CASES, *GRAMMAR_TRAPS]
    assert len({row['id'] for row in rows}) == len(rows)
    assert len(GRAMMAR_CASES) == 30 and len(GRAMMAR_TRAPS) == 20
    for row in GRAMMAR_CASES:
        assert len(row['expected']['rooms']) >= 2
        for request in row['expected']['rooms']:
            RoomScope.model_validate(request['scope'])
