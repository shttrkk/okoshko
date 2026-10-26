"""Conservative text normalization and context for catalogue intent rules.

No fuzzy dictionary or online language model is used. Spelling repairs and unit
conversion are explicit; ordinary homonyms stay outside hotel-search criteria.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache

_NUMBER_WORDS = {
    0: r'ноль|нуля|нулю|нулем',
    1: r'один|одна|одну|одного|одной',
    2: r'два|две|двух', 3: r'три|трех', 4: r'четыре|четырех',
    5: r'пять|пяти', 6: r'шесть|шести', 7: r'семь|семи', 8: r'восемь|восьми',
    9: r'девять|девяти', 10: r'десять|десяти', 11: r'одиннадцать|одиннадцати',
    12: r'двенадцать|двенадцати', 13: r'тринадцать|тринадцати',
    14: r'четырнадцать|четырнадцати', 15: r'пятнадцать|пятнадцати',
    16: r'шестнадцать|шестнадцати', 17: r'семнадцать|семнадцати',
    18: r'восемнадцать|восемнадцати', 19: r'девятнадцать|девятнадцати',
    20: r'двадцать|двадцати', 30: r'тридцать|тридцати', 40: r'сорок|сорока',
    50: r'пятьдесят|пятидесяти', 60: r'шестьдесят|шестидесяти',
    70: r'семьдесят|семидесяти', 80: r'восемьдесят|восьмидесяти',
    90: r'девяносто|девяноста', 100: r'сто|ста',
    200: r'двести|двухсот|двумстам|двумястами|двухстах',
    300: r'триста|трехсот|тремстам|тремястами|трехстах',
    400: r'четыреста|четырехсот|четыремстам|четырьмястами|четырехстах',
    500: r'пятьсот|пятисот|пятистам|пятьюстами|пятистах',
    600: r'шестьсот|шестисот|шестистам|шестьюстами|шестистах',
    700: r'семьсот|семисот|семистам|семьюстами|семистах',
    800: r'восемьсот|восьмисот|восьмистам|восемьюстами|восьмистах',
    900: r'девятьсот|девятисот|девятистам|девятьюстами|девятистах',
    1000: r'тысяча|тысячи|тысячу|тысяче|тысячей',
}
_ORDINAL_WORDS = {
    1: r'перв\w*', 2: r'втор\w*', 3: r'трет\w*', 4: r'четверт\w*',
    5: r'пят\w*', 6: r'шест\w*', 7: r'седьм\w*', 8: r'восьм\w*',
    9: r'девят\w*', 10: r'десят\w*', 11: r'одиннадцат\w*', 12: r'двенадцат\w*',
    13: r'тринадцат\w*', 14: r'четырнадцат\w*', 15: r'пятнадцат\w*',
    16: r'шестнадцат\w*', 17: r'семнадцат\w*', 18: r'восемнадцат\w*', 19: r'девятнадцат\w*',
    20: r'двадцат\w*', 30: r'тридцат\w*', 40: r'сороков\w*',
    50: r'пятидесят\w*', 60: r'шестидесят\w*', 70: r'семидесят\w*',
    80: r'восьмидесят\w*', 90: r'девяност\w*', 100: r'сот\w*', 0: r'нулев\w*',
}
_CARDINAL_VALUES = {word: number for number, forms in _NUMBER_WORDS.items() for word in forms.split('|')}
_CARDINAL_TOKEN = '(?:' + '|'.join(sorted(_CARDINAL_VALUES, key=len, reverse=True)) + ')'
_QUANTITY_UNIT = r'(?:мин(?:ут\w*)?\b|метр\w*|м\b|километр\w*|км\b|час\w*|кг\b|килограмм\w*|градус\w*|мбит|mbps|этаж\w*)'


def _cardinal_number(words: list[str]) -> int | None:
    values = [_CARDINAL_VALUES[word] for word in words]
    if len(values) == 1:
        return values[0]
    total = 0
    if values and 100 <= values[0] <= 900 and values[0] % 100 == 0:
        total = values.pop(0)
    if len(values) == 1 and values[0] < 100:
        return total + values[0]
    if len(values) == 2 and 20 <= values[0] <= 90 and values[0] % 10 == 0 and 1 <= values[1] <= 9:
        return total + values[0] + values[1]
    return None


def _normalize_cardinals(text: str) -> str:
    pattern = r'\b(?P<words>' + _CARDINAL_TOKEN + r'(?:\s+' + _CARDINAL_TOKEN + r'){0,2})\b(?=\s+' + _QUANTITY_UNIT + ')'

    def replacement(match):
        value = _cardinal_number(match['words'].split())
        return str(value) if value is not None else match[0]

    return re.sub(pattern, replacement, text)
_TRANSLATIONS = (
    (r'\bno need (?:for )?(?:wi[ -]?fi|internet)\b', 'интернет не нужен'),
    (r'\bsea view\b', 'вид на море'),
    (r'\bmountain view\b', 'вид на горы'),
    (r'\bsunset view\b', 'вид на закат'),
    (r'\bquiet (?:room|hotel)\b', 'тихий номер'),
    (r'\bpet[- ]friendly\b', 'с питомцем'),
    (r'\bair conditioning\b', 'кондиционер'),
    (r'\bblackout curtains\b', 'плотные шторы'),
    (r'\bbalcony\b', 'балкон'),
    (r'\bparking\b', 'парковка'),
    (r'\bbeach\b', 'пляж'),
    (r'\bat least\b', 'не менее'),
    (r'\bwithin\b', 'не дальше'),
    (r'\bminutes?\b', 'минут'),
    (r'\bmet(?:er|re)s?\b', 'метров'),
    (r'\brequired\b', 'обязательно'),
    (r'\bpreferred\b', 'желательно'),
    (r'\bno (?=вид на море)', 'без '),
    (r'\bwith\b', 'с'), (r'\band\b', 'и'),
    (r'\bplease\b', 'пожалуйста'), (r'\bhotel\b', 'отель'),
    (r'\bwalk\b', 'пешком'), (r'\bin\b', 'в'),
)
_ENGLISH_MONTHS = ('january', 'february', 'march', 'april', 'may', 'june',
                   'july', 'august', 'september', 'october', 'november', 'december')
_RUSSIAN_MONTHS = ('январе', 'феврале', 'марте', 'апреле', 'мае', 'июне',
                   'июле', 'августе', 'сентябре', 'октябре', 'ноябре', 'декабре')


def normalize_query_text(value: str) -> str:
    value = re.sub(r'\b(?:МБ/с|MB/s)\b', 'мегабайт/с', value)
    text = value.casefold().replace('ё', 'е')
    text = re.sub(r'\bвай[ -]?фай\b', 'wi-fi', text)
    text = re.sub(r'\bтишын([ауые])\b', r'тишин\1', text)
    text = re.sub(r'\bшумоизляц(\w*)\b', r'шумоизоляц\1', text)
    for pattern, replacement in _TRANSLATIONS:
        text = re.sub(pattern, replacement, text)
    for english, russian in zip(_ENGLISH_MONTHS, _RUSSIAN_MONTHS, strict=True):
        text = re.sub(r'\b' + english + r'\b', russian, text)
    text = re.sub(r'\bполчаса\b', '30 минут', text)
    text = re.sub(r'\bчетверт[ьи]\s+часа\b', '15 минут', text)
    text = re.sub(r'\bполукилометр\w*\b', '0,5 км', text)
    text = _normalize_cardinals(text)
    for number, words in sorted(_ORDINAL_WORDS.items(), reverse=True):
        suffixes = r'(?:ый|ой|ого|ому|ом|ая|ую|ое|ые|ых|ыми)'
        if number == 3:
            suffixes = r'(?:ий|ьего|ьему|ьем|ья|ью)'
        words = words.removesuffix(r'\w*') + suffixes
        text = re.sub(r'\b(?:' + words + r')\b(?=\s+этаж)', str(number), text)
    return text


def clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    # Decimal points/commas and numeric dates are not clause separators.
    separators = r'(?<!\d)[,.]|[,.](?!\d)|[;!?]|\b(?:но|а)\b'
    left = max((match.end() for match in re.finditer(separators, text[:start])), default=0)
    right = next((end + match.start() for match in re.finditer(separators, text[end:])), len(text))
    return left, right


def facade_is_explicit(text: str, match: re.Match) -> bool:
    left, right = clause_bounds(text, match.start(), match.end())
    before, after = text[max(left, match.start()-35):match.start()], text[match.end():min(right, match.end()+35)]
    return bool(re.search(r'\b(?:сторон\w*|фасад\w*)\b', match[0])
                or re.search(r'(?:окн\w*|номер\w*|сторон\w*|фасад\w*|этаж\w*)[^,;]{0,30}$', before)
                or re.match(r'\w*\s+(?:сторон\w*|фасад\w*|окн\w*)\b', after))


def valid_intent_match(cid: str, text: str, match: re.Match) -> bool:
    left, right = clause_bounds(text, match.start(), match.end())
    clause = text[left:right]
    after = text[match.end():right]
    quoted = any(m.start() < match.start() and m.end() > match.end()
                 for m in re.finditer(r'«[^»]+»|"[^"]+"|“[^”]+”', text))
    if quoted and re.search(r'\b(?:названи\w*|называ\w*|марк\w*|бренд\w*|слово|термин\w*)\b', clause):
        return False
    operative_search = bool(re.search(r'\b(?:ищу|ищем|найди\w*|подбер\w*|хочу|хотим|нужен|нужна)\s+(?:\w+\s+){0,3}(?:отел\w*|номер\w*|гостиниц\w*)\b', text))
    if not operative_search and re.search(r'\b(?:слово|термин\w*|фраз\w*)\b', text) and re.search(
            r'означа\w*|обознача\w*|договор\w*|определени\w*|перевод\w*', text):
        return False
    if not operative_search and re.search(r'\bчто\s+(?:означает|значит)\b', text):
        return False
    if re.search(r'\b(?:песн\w*|стихотворени\w*|цитат\w*)\b', clause):
        if quoted or not operative_search and re.search(r'автор\w*|назов\w*|названи\w*', clause):
            return False
    if cid == 'car_access' and re.search(r'\b(?:площад\w*|периметр\w*)\b', clause) and re.search(
            r'посчита\w*|расчет\w*|рассчит\w*|вычисл\w*|чертеж\w*', clause):
        return False
    if cid == 'connectivity' and re.search(r'\bинтернет[- ](?:сервис\w*|магазин\w*|портал\w*|площадк\w*)', clause):
        if re.search(r'интернет', match[0]):
            return False
    if cid == 'green_nearby' and re.search(r'\bскверн\w*', match[0]):
        return False
    if cid in {'sea_view', 'sunset_view', 'mountain_view', 'room_orientation', 'family_nearby', 'pets'} and re.search(
            r'\b(?:картин\w*|рисун(?:ок|к\w*)|открытк\w*|фотограф\w*)\b', clause):
        return False
    if cid == 'quiet_night' and re.search(r'тих\w*\s+океан|тишин\w*\s+в\s+чат\w*', clause):
        return False
    if cid == 'sunset_view' and re.match(r'\s+(?:эпох\w*|эр[аыуе]|импер\w*|цивилиз\w*)\b', after):
        return False
    if cid == 'nightlife' and re.search(r'(?:мини[ -]?бар|\bбар\s+(?:в\s+(?:номер\w*|отел\w*|гостиниц\w*)|на\s+крыш\w*))', clause):
        return False
    if cid == 'nightlife' and re.search(r'\bклуб\w*\s+(?:любител\w*|шахмат\w*|настольн\w*)', clause):
        return False
    if cid == 'car_access' and re.search(r'заряд\w*', match[0]) and not re.search(
            r'электромобил\w*|электрокар\w*|электроавто\w*|зарядн\w*\s+станц\w*', clause):
        return False
    if cid == 'family_nearby' and re.search(r'площадк\w*\s+для\s+(?:вертолет\w*|переговор\w*|строитель\w*|мусор\w*)', clause):
        return False
    if cid == 'family_nearby' and re.search(r'инвалидн\w*\s+коляск\w*|кресл\w*[ -]коляск\w*', clause) and not re.search(
            r'\bдет\w*|ребен\w*|малыш\w*', clause):
        return False
    if cid == 'family_nearby' and re.search(r'\b(?:не\s+(?:едем|поедем|берем|возьмем)|без\s+дет\w*)\b|дет\w*\s+не\s+(?:берем|едут|поедут)', clause):
        return False
    if cid == 'pets' and (re.search(r'\b(?:дома|родител\w*|остав\w*|остает\w*)\b', clause)
                         or re.search(r'\bне\s+(?:берем|возьмем|едем|поедем)|\bне\s+с\s+(?:собак|кошк|питом)\w*', clause)
                         or re.search(r'без\s+(?:собак|кошк|питом)\w*.*\bед[уеи]\w*', clause)):
        return False
    if cid == 'connectivity' and re.search(r'работ\w*\s+(?:художник\w*|рецепц\w*|ресепш\w*|над\s+ошибк\w*)', clause):
        return False
    if cid == 'connectivity' and re.search(r'удален\w*\s+(?:от|фотограф\w*)', clause):
        return False
    if cid == 'building_history' and not re.search(r'здани\w*|особняк\w*|отел\w*|гостиниц\w*|архитектур\w*', clause):
        return False
    if cid == 'climate' and re.search(r'погод\w*\s+в\s+дом\w*', clause):
        return False
    if cid == 'beach_walk' and re.search(r'\bне\s+(?:хочу|хотим|буду|будем|планир\w*)\s+(?:(?:вообще|совсем)\s+)?(?:ходить|идти)\s+пешком', clause):
        return False
    return True


@lru_cache(maxsize=1024)
def _catalogue_words(phrases: tuple[str, ...]) -> frozenset[str]:
    return frozenset(word for phrase in phrases for word in re.findall(r'[а-яa-z]{5,}', normalize_query_text(phrase)))


def catalogue_suggestions(text: str, unknown: list[str], catalog: list,
                          excluded_ids: set[str] | None = None) -> list[str]:
    """Suggest at most two close catalogue names; never create a criterion.

    This requires a long, near-identical word. Related amenities, short words and
    numeric guesses are unsuitable spelling hints.
    """
    tokens = [token for fragment in unknown for token in re.findall(r'[а-яa-z]{5,}', fragment) if len(token) <= 40][:12]
    excluded_ids = excluded_ids or set()
    scored = []
    for spec in catalog:
        if spec.status != 'active' or spec.id in excluded_ids:
            continue
        words = _catalogue_words((spec.name_ru, *spec.synonyms))
        score = 0.0
        for token in tokens:
            matches = list(re.finditer(r'\b' + re.escape(token) + r'\b', text))
            if not any(valid_intent_match(spec.id, text, match) for match in matches):
                continue
            score = max(score, max((SequenceMatcher(None, token, word).ratio() for word in words), default=0))
        if .86 <= score < 1:
            scored.append((-score, spec.id, spec.name_ru))
    return [name for _, _, name in sorted(scored)[:2]]
