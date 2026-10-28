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
_QUANTITY_UNIT = r'(?:мин(?:ут\w*)?\b|метр\w*|м\b|километр\w*|км\b|час\w*|кг\b|килограмм\w*|градус\w*|мбит|mbps|этаж\w*|заведени\w*)'


def _cardinal_number(words: list[str]) -> int | None:
    values = [_CARDINAL_VALUES[word] for word in words]
    if len(values) == 1:
        return values[0]
    if values == [1, 1000]:
        return 1000
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
    (r'\bview of (?:the )?winter theat(?:re|er)\b', 'вид на зимний театр'),
    (r'\bview of (?:the )?olympic park\b', 'вид на олимпийский парк'),
    (r'\bview of (?:the )?bolshoy(?: palace)?\b', 'вид на большой'),
    (r'\b(?:convenient transport|easy to get) from (?:the )?airport\b', 'удобно добираться из аэропорта'),
    (r'\b(?:shop|store) open at night\b', 'магазин ночью'),
    (r'\b(?:old hotel|historic mansion)\b', 'старинный отель'),
    (r'\bnear (?:the )?train station\b', 'рядом со станцией'),
    (r'\bnear (?:the )?bus stop\b', 'рядом с остановкой'),
    (r'\b(?:in (?:the )?)?city cent(?:re|er)\b', 'в центре'),
    (r'\bcell towers? nearby\b', 'мачты связи рядом'),
    (r'\bmobile internet\b', 'мобильный интернет'),
    (r'\bevents nearby\b', 'мероприятия рядом'),
    (r'\brooftop terrace\b', 'терраса на крыше'),
    (r'\bdark room\b', 'темно в номере'),
    (r'\bsleep well\b', 'выспаться'),
    (r'\brainy season\b', 'сезон дождей'),
    (r'\bnot needed\b', 'не нужна'),
    (r'\bfar from (?:the )?airport\b', 'далеко от аэропорта'),
    (r'\b(?:no aircraft noise|no airplane noise)\b', 'без шума самолетов'),
    (r'\bnear (?:the )?airport\b', 'рядом с аэропортом'),
    (r'\bfrom (?:the )?airport without transfers\b', 'из аэропорта без пересадок'),
    (r'\bview of (?:the )?sea terminal\b', 'вид на морской вокзал'),
    (r'\bview of (?:the )?fisht\b', 'вид на фишт'),
    (r'\bview of (?:a )?landmark\b', 'вид на достопримечательность'),
    (r'\b(?:24/7|24-hour) (?:shop|store)\b', 'круглосуточный магазин'),
    (r'\bfood at night\b', 'поесть ночью'),
    (r'\bprivate parking\b', 'своя парковка'),
    (r'\bdrive to (?:the )?entrance\b', 'подъехать к входу'),
    (r'\bhistoric(?:al)? building\b', 'историческое здание'),
    (r'\bchildren.s hospital\b', 'детская больница'),
    (r'\bdog park\b', 'площадка для выгула'),
    (r'\bvet(?:erinary clinic)?\b', 'ветклиника'),
    (r'\bpet (?:shop|store)\b', 'зоомагазин'),
    (r'\bno construction nearby\b', 'без стройки рядом'),
    (r'\bsunny days\b', 'солнечные дни'),
    (r'\bno rain\b', 'без дождей'),
    (r'\bnear (?:the )?metro\b', 'у метро'),
    (r'\bno need (?:for )?(?:wi[ -]?fi|internet)\b', 'интернет не нужен'),
    (r'\b(?:sea|ocean) view\b', 'вид на море'),
    (r'\bmorning sun(?:shine)?\b', 'утреннее солнце'),
    (r'\bstep[- ]free\b', 'без лестниц'),
    (r'\bsandy beach\b', 'песчаный пляж'),
    (r'\ba park\b|\bpark(?=\s+(?:nearby|within))', 'парк'),
    (r'\bnearby\b', 'рядом'),
    (r'\bwould be (?:a bonus|nice)\b', 'желательно'),
    (r'\bessential\b', 'обязательно'),
    (r'\bremote work\b', 'удаленная работа'),
    (r'\b(?:i )?need\b', 'нужно'),
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
    value = re.sub(r'(?<![а-яА-ЯёЁa-zA-Z])(?:МБ/с|MB/s)\b', 'мегабайт/с', value)
    text = value.casefold().replace('ё', 'е')
    text = re.sub(r'\bвай[ -]?фай\b', 'wi-fi', text)
    text = re.sub(r'\bтишын([ауые])\b', r'тишин\1', text)
    text = re.sub(r'\bшумоизляц(\w*)\b', r'шумоизоляц\1', text)
    text = re.sub(r'\bпорковк(\w*)\b', r'парковк\1', text)
    text = re.sub(r'\bутренее\b', 'утреннее', text)
    text = re.sub(r'(?<=\d)\s*m\b', ' м', text)
    for pattern, replacement in _TRANSLATIONS:
        text = re.sub(pattern, replacement, text)
    for english, russian in zip(_ENGLISH_MONTHS, _RUSSIAN_MONTHS, strict=True):
        text = re.sub(r'\b' + english + r'\b', russian, text)
    text = re.sub(r'\bполчаса\b', '30 минут', text)
    text = re.sub(r'\bчетверт[ьи]\s+часа\b', '15 минут', text)
    text = re.sub(r'\bполукилометр\w*\b', '0,5 км', text)
    text = _normalize_cardinals(text)
    # An explicit correction replaces the rejected measurement, not its unit.
    units = r'(?:дб|метр\w*|м|км|минут\w*|мбит/с|mbps)'
    text = re.sub(r'\bне\s+\d+(?:[.,]\d+)?\s*(?P<unit>' + units + r')\s*,\s*а\s+(?P<correct>\d+(?:[.,]\d+)?\s*(?P=unit))\b',
                  lambda m: m['correct'], text)
    for number, words in sorted(_ORDINAL_WORDS.items(), reverse=True):
        suffixes = r'(?:ый|ой|ого|ому|ом|ая|ую|ое|ые|ых|ыми)'
        if number == 3:
            suffixes = r'(?:ий|ьего|ьему|ьем|ья|ью)'
        words = words.removesuffix(r'\w*') + suffixes
        text = re.sub(r'\b(?:' + words + r')\b(?=\s+этаж)', str(number), text)
    def floor_pair(match):
        bounds = []
        for word in (match['low'], match['high']):
            number = next((n for n, pattern in _ORDINAL_WORDS.items() if re.fullmatch(pattern, word)), None)
            if number is None:
                return match[0]
            bounds.append(number)
        return f'этаж с {bounds[0]} по {bounds[1]} этажа'
    text = re.sub(r'\bэтаж\w*\s+(?:допустим\w*\s+)?со?\s+(?P<low>[а-я]+)\s+по\s+(?P<high>[а-я]+)\b', floor_pair, text)
    return text


def clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    # Decimal points/commas and numeric dates are not clause separators.
    separators = r'(?<!\d)[,.]|[,.](?!\d)|[;!?]|\b(?:но|а)\b'
    left = max((match.end() for match in re.finditer(separators, text[:start])), default=0)
    right = next((end + match.start() for match in re.finditer(separators, text[end:])), len(text))
    return left, right


_QUOTED = r'«[^»]+»|"[^"\n]+"|“[^”]+”|(?<!\w)\'[^\'\n]+\'(?!\w)'
_HOTEL_REQUEST = (r'\b(?:ищ\w*|найди\w*|подбер\w*|выбира\w*|брониру\w*|хочу|хотим|нуж\w*)'
                  r'\s+(?:\w+\s+){0,5}(?:отел\w*|номер\w*|гостиниц\w*|жиль\w*)\b')
_LANGUAGE_TASK = (r'\b(?:словосочетани\w*|слов[аоеу]|словах|выражени\w*|грамматик\w*|'
                  r'орфограф\w*|пунктуац\w*|запят\w*|числительн\w*|аббревиатур\w*|'
                  r'перевести|перевед\w*|перевод\w*|расшифров\w*|библиограф\w*|алфавит\w*|'
                  r'vocabulary|translate|adjective|homework)\b')
_DOCUMENT_TASK = (r'\b(?:редактир\w*|перепиш\w*|сократ\w*|подпис\w*|заголов\w*|'
                  r'шрифт\w*|логотип\w*|эмблем\w*|этикет\w*|резюме|учебник\w*|'
                  r'урок\w*|реферат\w*|чертеж\w*|нарисова\w*|стенгазет\w*)\b')
_OTHER_OBJECT = (r'\b(?:наушник\w*|микрофон\w*|роутер\w*|прошивк\w*|терминал\w*|'
                 r'хостинг\w*|домен\w*|шина\w*|steam|робот\w*|игрушечн\w*)\b')
_CURRENT_REQUIREMENT = r'\b(?:хочу|хотим|нуж\w*|ищу|ищем|требу\w*|необходим\w*|долж\w*|хотелось|предпоч\w*|важн\w*|желатель\w*|обязатель\w*)\b'
_REPORTED_CONTEXT = r'\b(?:раньше|вспомина\w*|рассказ\w*|в прошлом|обсужда\w*|упомина\w*|работал[аи]?|прочита\w*)\b'
_THIRD_PERSON = r'(?:сосед(?:ка)?|сестра|брат|мама|папа|друг|подруга|коллега|знаком(?:ый|ая)|он|она|они)'
_PRESENT_STATE = r'(?:любит|любят|работает|работают|держит|держат|живет|живут|рассказывает|пишет|говорит|обсуждает)'
_THIRD_PERSON_REPORT = (r'\b' + _THIRD_PERSON + r'\s+(?:(?:часто|сейчас|обычно|давно)\s+)?' + _PRESENT_STATE + r'\b|'
                        r'\b(?:работает|работают|живет|живут)\s+(?:(?:моя|мой|наша|наш)\s+)?' + _THIRD_PERSON + r'\b')
_TRIP_ACTION = r'\b(?:еду|едем|едет|едут|поеду|поедем|поедет|поедут|приеду|приедем|заселимся|путешествуем)\b'
_ADOPTED_ACTION = _CURRENT_REQUIREMENT + '|' + _TRIP_ACTION
_PAST_EVENT = (r'\b(?:вчера|позавчера|тогда|потом|на прошлой неделе|в прошлом году)\b'
               r'[^,;.!?]{0,80}\b(?:[а-я]{3,}л(?:[аои])?|был(?:[аои])?)\b')
_DEVICE_TASK = (r'\b(?:поставь|положи|переставь|передвинь|подключи|отключи|заряди|забери|возьми)(?:те)?\b'
                r'[^,;.!?]{0,60}\b(?:телефон\w*|смартфон\w*|планшет\w*|ноутбук\w*)\b')


def _reported_context_match(text: str) -> re.Match | None:
    matches = [m for pattern in (_REPORTED_CONTEXT, _THIRD_PERSON_REPORT, _PAST_EVENT)
               if (m := re.search(pattern, text))]
    return min(matches, key=lambda m: m.start(), default=None)


def _adopted_action_match(text: str) -> re.Match | None:
    for match in re.finditer(_ADOPTED_ACTION, text):
        # Past importance/need is still a report, not a new request.
        if not re.search(r'\bбыл[аои]?\s+(?:\w+\s+)?$', text[max(0, match.start()-30):match.start()]):
            return match
    return None


def search_context_text(text: str) -> str:
    """Mask explicit non-search discourse while retaining offsets and bare queries.

    A quoted preference is evidence only when the writer adopts it as a hotel
    request. Language tasks, document editing, device properties and reported
    speech do not establish a hotel requirement. This deliberately does not
    demand a verb from short catalogue-shaped requests such as 'парковка рядом'.
    """
    result = list(text)
    separators = r'(?<!\d)\.(?!\d)|[;!?\n]'
    boundaries = [0, *[m.end() for m in re.finditer(separators, text)], len(text)]
    for left, right in zip(boundaries, boundaries[1:], strict=False):
        segment = text[left:right]
        quotes = list(re.finditer(_QUOTED, segment))
        unquoted = re.sub(_QUOTED, lambda m: ' ' * len(m[0]), segment)
        hotel_request = bool(re.search(_HOTEL_REQUEST, unquoted))
        language_task = bool(re.search(_LANGUAGE_TASK, unquoted))
        document_task = bool(re.search(_DOCUMENT_TASK, unquoted))
        document_task |= bool(quotes and re.search(r'\b(?:упаковк\w*|корм\w*|этикет\w*)\b', unquoted))
        other_object = bool(re.search(_OTHER_OBJECT, unquoted))
        reported = bool(re.search(r'\b(?:написал\w*|сказал\w*|обсужда\w*|цитир\w*|называ\w*)\b', unquoted))
        narrative = bool(_reported_context_match(unquoted))
        device_task = bool(re.search(_DEVICE_TASK, unquoted))
        current_requirement = bool(_adopted_action_match(unquoted))
        if not hotel_request and (language_task or document_task or other_object
                                  or (narrative or device_task) and not current_requirement):
            result[left:right] = ' ' * (right-left)
        elif quotes and (language_task or document_task or reported):
            for quoted in quotes:
                result[left+quoted.start():left+quoted.end()] = ' ' * len(quoted[0])
    # Mixed discourse can place a genuine request after a comma. Reject the
    # reported/device clause without suppressing the neighbouring requirement.
    masked = ''.join(result)
    boundaries = [0, *[m.end() for m in re.finditer(r'(?<!\d)[,.]|[,.](?!\d)|[;!?]', masked)], len(masked)]
    reported_context = False
    for left, right in zip(boundaries, boundaries[1:], strict=False):
        if left and text[left-1] in '.;!?\n':
            reported_context = False
        clause = masked[left:right]
        reported = _reported_context_match(clause)
        device_task = re.search(_DEVICE_TASK, clause)
        adopted = _adopted_action_match(re.sub(_QUOTED, lambda m: ' ' * len(m[0]), clause))
        prefix = reported or device_task
        if adopted and (reported_context or prefix and prefix.start() < adopted.start()):
            result[left:left+adopted.start()] = ' ' * adopted.start()
            reported_context = False
            continue
        if re.search(_OTHER_OBJECT, clause) and not re.search(_HOTEL_REQUEST, clause) or (
                (reported or device_task or reported_context) and not adopted):
            result[left:right] = ' ' * (right-left)
            reported_context = True
        elif adopted:
            reported_context = False
    return ''.join(result)


def _depicted_property(text: str, match: re.Match) -> bool:
    """The request's head object governs a depicted property, not its purpose.

    'Картина с видом' requests an image; 'вид для фотографии' requests a view.
    A later explicit request can start a new object in the same clause.
    """
    left, right = clause_bounds(text, match.start(), match.end())
    artifacts = re.finditer(r'\b(?:картин\w*|рисун(?:ок|к\w*)|открытк\w*|фотограф\w*|изображени\w*)\b', text[left:right])
    for artifact in artifacts:
        start, end = left+artifact.start(), left+artifact.end()
        if start < match.start():
            between = text[end:match.start()]
            if not re.search(_CURRENT_REQUIREMENT, between):
                return True
        elif start >= match.end():
            before_artifact = text[match.end():start]
            if re.search(r'\b(?:на|в)\s+(?:[а-я]+(?:ой|ом|ых)\s+)?$', before_artifact):
                return True
            purpose = bool(re.search(r'\bдля\s*$', before_artifact))
            adopted_property = _adopted_action_match(text[left:match.start()]) is not None
            if not purpose and not adopted_property:
                return True
    return False


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
    if cid == 'soundproofing' and re.search(r'blackout\s+poetry|poetry\s+book', clause):
        return False
    if cid == 'connectivity' and re.search(r'мобильн\w*\s+(?:интернет|связ|покрыти)', clause) and not re.search(r'wi[ -]?fi|удален|для\s+работ', clause):
        return False
    if cid == 'connectivity' and re.fullmatch(r'[45]g', match[0]):
        return False
    if cid == 'location_access' and re.search(r'центр\w*\s+(?:вечерн\w*\s+жизн\w*|внимани\w*|событи\w*)', clause):
        return False
    if cid == 'location_access' and re.search(r'(?:удален\w*|далеко|подальше|вдали)\s+от\s+центр\w*', clause):
        return False
    if cid == 'family_nearby' and re.search(r'площадк\w*\s+для\s+выгул\w*|собач\w*\s+площадк', clause) and not re.search(r'детск\w*\s+площадк', clause):
        return False
    if cid == 'car_access' and re.search(r'(?:сво\w*|собственн\w*|частн\w*)\s+парковк|парковк\w*\s+отел\w*|подъехать\s+(?:прямо\s+)?к\s+вход', clause) and not re.search(r'публичн\w*\s+парковк|общественн\w*\s+парковк', clause):
        return False
    quoted = any(m.start() < match.start() and m.end() > match.end()
                 for m in re.finditer(r'«[^»]+»|"[^"]+"|“[^”]+”', text))
    if quoted and re.search(r'\b(?:названи\w*|называ\w*|марк\w*|бренд\w*|слово|термин\w*|пересказ\w*|пересказыва\w*|цитир\w*)\b', clause):
        return False
    if cid in {'family_nearby', 'accessible_route'} and re.search(r'коляск\w*', match[0]):
        if re.search(r'\b(?:прокат\w*|аренд\w*|ремонт\w*|продаж\w*|пересказ\w*|пересказыва\w*|цитир\w*)\b', clause):
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
    if _depicted_property(text, match):
        return False
    if cid == 'nearby_photos':
        # Existing local photographs differ from asking someone to take/edit
        # photographs, or describing the purpose of a real view requirement.
        before = text[left:match.start()]
        if re.search(r'\bдля\s*$', before) or re.search(
                r'\b(?:сделать|сделай\w*|снять|сними\w*|снима\w*|сфотографиру\w*|нарису\w*|обработа\w*|отредактиру\w*)'
                r'\s+(?:(?:мне|нам|пожалуйста)\s+){0,2}$', before):
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
    if cid in {'family_nearby', 'beach_walk'} and re.search(r'проезд\s+коляск\w*|тег\w*\s+доступност\w*|инвалидн\w*\s+коляск\w*', clause):
        if cid == 'family_nearby' and not re.search(r'\bдет\w*|ребен\w*|малыш\w*', clause):
            return False
        if cid == 'beach_walk' and not re.search(r'\d+(?:[.,]\d+)?\s*(?:минут\w*|час\w*|метр\w*|км\b|м\b)', clause):
            return False
    if cid == 'family_nearby' and re.search(r'\b(?:не\s+(?:едем|поедем|берем|возьмем)|без\s+дет\w*)\b|дет\w*\s+не\s+(?:берем|едут|поедут)', clause):
        return False
    if cid == 'pets' and (re.search(r'\b(?:дома|родител\w*|остав\w*|остает\w*)\b', clause)
                         or re.search(r'\bне\s+(?:берем|возьмем|едем|поедем)|\bне\s+с\s+(?:собак|кошк|питом)\w*', clause)
                         or re.search(r'без\s+(?:собак|кошк|питом)\w*.*\bед[уеи]\w*', clause)):
        return False
    if cid == 'connectivity' and re.search(r'работ\w*\s+(?:художник\w*|рецепц\w*|ресепш\w*|над\s+ошибк\w*)', clause):
        return False
    if cid == 'connectivity' and re.search(r'\b(?:гора|куча|много)\s+работы\b', clause) and not re.search(r'интернет|wi[ -]?fi|удален', clause):
        return False
    if cid == 'connectivity' and re.search(r'удален\w*\s+(?:от|фотограф\w*)', clause):
        return False
    if cid == 'connectivity' and re.search(r'\b(?:разда\w*|сво[йяе]\w*)\b[^,;.!?]{0,35}(?:wi[ -]?fi|интернет)', clause):
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


def source_phrase(text: str, fragment: str) -> str:
    """The guest's own wording behind a token-bag fragment: «рядом бассейном» → «рядом с бассейном»."""
    words = fragment.split()
    if not words or ":" in fragment:
        return fragment
    short = r"[а-яёa-z]{1,2}"
    pattern = rf"(?:\b{short}\s+)?" + rf"\s+(?:{short}\s+)*".join(re.escape(w) for w in words) + r"\b"
    match = re.search(pattern, text, re.IGNORECASE)
    return match[0] if match else fragment
