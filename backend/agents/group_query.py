"""Offline grouping of explicitly separate wishes; no inventory or capacity inference.

Segmentation precedes the existing single-request extractor. Each fragment is
parsed independently, with hotel conditions collected once at the top level.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from backend.agents.query_match import ROOM_CHARACTERISTICS
from backend.agents.query_text import normalize_query_text, search_context_text
from backend.contracts import (
    CharacteristicSpec,
    Criterion,
    ParsedQuery,
    RoomRequest,
    RoomScope,
    SearchRequest,
)

SingleParser = Callable[[SearchRequest, list[CharacteristicSpec]], ParsedQuery]
# Recipient phrases are syntax, not hotel facts. Unknown proper names after
# «для» are accepted, while purpose phrases such as «для работы» are shared.
WORD = r'[а-яa-z][а-яa-z-]{1,40}'
PURPOSE = (r'работ\w*|сна|сон|прогул\w*|купани\w*|плавани\w*|проверк\w*|сравнени\w*|поиск\w*|'
           r'фото\w*|съемк\w*|загар\w*|отдых\w*|лечени\w*|развлечени\w*|продаж\w*|аренд\w*|'
           r'завтрак\w*|выезда|заезда|гостиниц\w*|отел\w*|номер\w*|комнат\w*|вид\w*')
WISH_HEAD = (r'тихо|тихий|тихая|тихие|нужен|нужна|нужно|нужны|хочется|важно|желательно|обязательно|'
             r'вид\w*|балкон\w*|кондиционер\w*|моря|море|пляж\w*|окн\w*|бассейн\w*')
RECIPIENT_NOUN = r'(?!(?:' + PURPOSE + '|' + WISH_HEAD + r')\b)' + WORD
MODIFIER = (r'каждого|каждой|каждых|одного|одной|другого|другой|двоих|двух|трех|'
            r'моего|моей|моих|нашего|нашей|наших|своего|своей|своих|вашего|вашей|ваших|'
            r'пожилых|пожилой|пожилого|старших|младших|молодой|молодого|молодых')
COMPANION = (r'мужем|женой|супругом|супругой|другом|подругой|друзьями|коллегой|коллегами|'
             r'ребенком|детьми|родителями|мамой|папой|бабушкой|дедушкой|братом|сестрой')
FOR_RECIPIENT = (r'для\s+(?:(?:каждого|каждой|каждых)\s+из\s+)?(?:(?:' + MODIFIER + r')\s+){0,3}'
                 + RECIPIENT_NOUN + r'(?:\s+(?:и\s+' + RECIPIENT_NOUN + r'|с\s+(?:' + COMPANION + r'))){0,2}')
RECIPIENT = (
    r'(?:нам|мне|себе)(?:\s+(?:с\s+(?:' + COMPANION + r')|и\s+(?:мужу|жене|другу|подруге)))?'
    r'|маме|папе|родителям|детям|ребенку|сыну|сыновьям|дочери|дочке|дочерям|внуку|внучке|внукам'
    r'|бабушке|дедушке|теще|тестю|свекрови|свекру|другу|подруге|друзьям|подругам|коллеге|коллегам'
    r'|сестре|брату|мужу|жене|супругу|супруге|остальным'
)
ORDINAL = (r'один|одна|одной|одном|одному|другой|другая|другом|другому|'
           r'первый|первая|первой|первом|первому|второй|вторая|втором|второму|'
           r'третий|третья|третьей|третьем|третьему|четвертый|четвертая|пятый|пятая|'
           r'шестой|шестая|седьмой|седьмая|восьмой|восьмая|девятый|девятая|'
           r'десятый|десятая|одиннадцатый|одиннадцатая|двенадцатый|двенадцатая')
DISTRIBUTIVE = (r'(?:для\s+каждого(?:\s+номера)?|для\s+каждой\s+комнаты|каждому\s+номеру|каждой\s+комнате)'
                r'(?=\s*[-–—:]?\s*(?:нуж\w*|хоч\w*|важ\w*|желатель\w*|'
                r'обязатель\w*|тих\w*|вид\w*|балкон\w*|с\b|без\b|не\b))')
SHARED = r'для\s+всех|всем|у\s+всех|общие\s+(?:требования|пожелания)|' + DISTRIBUTIVE
NUMBERED = r'(?:в\s+)?(?:номер(?:е|у|а)?|комнат[аеуы])\s*[№#]?\s*\d+|[№#]\s*\d+'
MARKERS = re.compile(r'\b(?P<shared>' + SHARED + r')\b|(?P<numbered>\b(?:' + NUMBERED
                     + r')(?=\s|[-–—:,;.]|$)|[№#]\s*\d+\b)|\b(?P<for_person>' + FOR_RECIPIENT
                     + r')\b|\b(?P<person>' + RECIPIENT + r')\b|\b(?P<ordinal>(?:(?:в|во)\s+)?(?:' + ORDINAL + r'))\b')
COUNT_WORDS = {'два': 2, 'две': 2, 'двух': 2, 'три': 3, 'трех': 3, 'четыре': 4, 'четырех': 4,
               'пять': 5, 'шесть': 6, 'семь': 7, 'восемь': 8, 'девять': 9, 'десять': 10,
               'одиннадцать': 11, 'двенадцать': 12, 'тринадцать': 13, 'четырнадцать': 14,
               'пятнадцать': 15, 'шестнадцать': 16, 'семнадцать': 17, 'восемнадцать': 18,
               'девятнадцать': 19, 'двадцать': 20, 'тридцать': 30, 'сорок': 40,
               'пятьдесят': 50, 'шестьдесят': 60, 'семьдесят': 70, 'восемьдесят': 80,
               'девяносто': 90, 'сто': 100}
ROOM_COUNT = re.compile(r'\b(?P<count>\d+|(?:' + '|'.join(COUNT_WORDS)
                        + r')(?:\s+(?:' + '|'.join(COUNT_WORDS) + r')){0,2})\s+(?:номер(?:а|ов)?|комнат(?:а|ы|у)?|комнат)\b')
MONTH = r'январ|феврал|март|апрел|ма[йяе]|июн|июл|август|сентябр|октябр|ноябр|декабр'
LIMIT_MESSAGE = 'Поддерживается до 12 отдельных пожеланий к номерам. Уточните состав запросов; наличие номеров не подтверждено.'
WISH_VERBS = (r'хочет|хотят|просит|просят|предпочитает|предпочитают|желает|желают|'
              r'нужен|нужна|нужно|нужны|важен|важна|важно|важны|желательно|требуется')
CAPITAL_WORD = r'[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z-]{1,40}'
BARE_RECIPIENT = re.compile(r'\b(?P<name>' + CAPITAL_WORD + r'(?:\s+' + CAPITAL_WORD
                            + r'){0,2})\b(?=\s*(?::|[–—])|\s+(?i:' + WISH_VERBS + r')\b)')
PAST_ACTION = (r'сказал\w*|рассказал\w*|говор\w*|сообщ\w*|ответил\w*|написал\w*|написали|'
               r'позвон\w*|звон\w*|отправ\w*|прислал\w*|дал(?:и|а)?|выдал\w*|'
               r'заселил\w*|поселил\w*|предложил\w*|предоставил\w*|достал(?:ся|ась|ись)|получил\w*')
# This scan only identifies reported clauses to exclude; it never creates an
# active named request without the delimiter/wish syntax above.
REPORTED_NAMED = re.compile(r'\b(?P<name>' + CAPITAL_WORD + r'(?:\s+' + CAPITAL_WORD
    + r'){0,2})\b(?=\s+(?i:(?:не\s+)?(?:' + PAST_ACTION
    + r')|раньше|тогда|вчера|позавчера|уже|(?:в|на)\s+прошл\w*)\b)')
SERVICE_LABELS = frozenset(('я мы вы нам мне себе всем все для в во на от до по с а и но не ни '
    'да то это так вот если когда как чтобы при после перед без из у к об о над под за про между '
    'отель отели гостиница гостиницы номер номера комната комнаты тип '
    'условия требования пожелания запрос поездка даты дата месяц заезд выезд количество '
    'обязательно желательно важно нужно нужен нужна нужны ищем ищу хотим хочу подберите '
    'пожалуйста просьба пожелание хотелось можно нельзя пусть сегодня завтра вчера раньше тогда '
    'вид море горы закат тишина тихо балкон парковка аптека площадка климат солнце интернет '
    'сочи адлер сириус бюджет итого пример описание отзыв отзывы').split())
CALENDAR_LABEL = re.compile(r'(?:(?:январ|феврал|апрел|июн|июл|сентябр|октябр|ноябр|декабр)'
                            r'(?:ь|я|е|ю|ем)|(?:март|август)(?:а|е|у|ом)?|ма[йяею]|маем|'
                            r'понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)')



@dataclass(frozen=True)
class Fragment:
    label: str
    text: str
    shared: bool = False


@dataclass(frozen=True)
class Marker:
    start: int
    end: int
    kind: str


def _unquoted(text: str) -> str:
    # Preserve positions and original labels; quotations are never assignments.
    return re.sub(r'«[^»]*»|“[^”]*”|"[^"]*"', lambda m: ' '*len(m[0]), text)


def _at_clause_start(text: str, start: int) -> bool:
    previous = re.split(r'[,;:.!?\n/•]|\s[-–—]\s|\b(?:а|и)\s+', text[:start])[-1].strip()
    if not previous:
        return True
    # A normal search/travel introduction need not end in a colon. Its wishes
    # remain a shared fragment, rather than being assigned to the first person.
    return bool(re.match(r'^(?:пожалуйста\s+)?(?:едем|еду|поедем|планируем|ищем|ищу|подыскиваем|'
                         r'подберите|подбери|подобрать|хотим|хочу|нужно|нужны|нужен|нужна|'
                         r'забронируйте|забронировать|бронируем)\b', previous)
                or ROOM_COUNT.fullmatch(previous))


def _room_count(plain: str) -> int | None:
    for candidate in ROOM_COUNT.finditer(plain):
        before = plain[max(0, candidate.start()-65):candidate.start()]
        after = plain[candidate.end():candidate.end()+80]
        if (re.search(r'\b(?:не\s+(?:нуж\w*|хот\w*)|не|вместо)\s*$', before)
                or re.match(r'\s+(?:телефон\w*|паспорт\w*|документ\w*|журнал\w*|газет\w*|'
                            r'в\s+(?:рейтинг\w*|списк\w*|квартир\w*)|не\s+(?:нуж\w*|треб\w*))', after)
                or re.search(r'\b(?:в\s+программе|на\s+сцене|исполня\w*)[^,;.!?]*$', before)):
            continue
        return int(candidate['count']) if candidate['count'].isdigit() else sum(
            COUNT_WORDS[word] for word in candidate['count'].split())
    return None


def _recipient_request(after: str) -> bool:
    body = re.sub(r'^[\s:–—-]*', '', after)
    # Temporal narratives and accommodation already offered/assigned are facts
    # about the past, rather than a request for another modelled position.
    if re.match(r'(?:(?:в|на)\s+прошл\w*|прошл\w*\s+(?:раз|год|лето)|раньше|тогда|вчера|позавчера)\b', body):
        return False
    return not re.match(r'(?:\d+\s*(?:лет|года?|годиков)\b|'
                        r'(?:(?:уже|сначала|потом)\s+)?(?:не\s+)?'
                        r'(?:' + PAST_ACTION + r'|там\s+был\w*)\b|'
                        r'не\s+(?:нуж\w*|треб\w*)\s+(?:отдельн\w*\s+)?(?:номер\w*|комнат\w*)\b)', body)


def _position_request(after: str, *, count: int | None, numbered: bool) -> bool:
    from backend.agents.query_parser import HARD, SOFT

    tail = re.sub(r'^[\s:–—-]*', '', after)
    if re.match(r'(?:отел\w*|гостиниц\w*|корпус\w*|здани\w*|город\w*|район\w*|'
                r'семь\w*|поездк\w*|году|месяц\w*)\b', tail):
        return False
    body = re.sub(r'^\s*(?:номер\w*|комнат\w*)?\s*[-–—:]?\s*', '', after)
    if re.match(r'(?:из|раз|этаж|на\s+(?:план\w*|фото\w*|снимк\w*)|в\s+(?:рейтинг\w*|списк\w*))\b', body):
        return False
    # A room count sets the requested cardinality. It is never evidence that
    # every subsequent ordinal (e.g. «в одном отеле») identifies a room.
    return bool(numbered or re.match(r'(?:' + SOFT + '|' + HARD
        + r'|с\b|без\b|тих\w*|тишин\w*|не\b|вид\w*|балкон\w*|море\b|горы\b|во\s+двор)', body))


def _one_shared_room(plain: str, markers: list[Marker], count: int | None) -> bool:
    if count is not None and count >= 2:
        return False
    one = r'(?:один|одна|одну|одного|одной|одном|1|единственный|единственная)'
    common = r'(?:общ\w*|семейн\w*|совместн\w*|больш\w*)'
    unit = r'(?:номер\w*|комнат\w*)'
    explicit = bool(re.search(r'\b' + one + r'\s+(?:' + common + r'\s+){0,3}' + unit + r'\b', plain)
        or re.search(r'\b(?:общ\w*|семейн\w*)\s+' + unit + r'\s+(?:для|на)\s+всех\b', plain)
        or re.search(r'\b(?:один|одна)\s+(?:общий|общая|семейный|семейная)(?=\s*[,;:.!?]|$)', plain))
    if not explicit:
        return False
    if sum(marker.kind in {'ordinal', 'numbered'} for marker in markers) >= 2:
        return False
    for match in re.finditer(r'\b(?:отдельн\w*|собственн\w*|свой|своя)\s+' + unit + r'\b', plain):
        before, after = plain[max(0, match.start()-35):match.start()], plain[match.end():match.end()+35]
        if not (re.search(r'\bне\s+(?:нуж\w*|хот\w*|треб\w*)\s*$', before)
                or re.match(r'\s+не\s+(?:нуж\w*|треб\w*)', after)):
            return False
    return True


def _candidate_markers(text: str, plain: str) -> list[Marker]:
    candidates = [Marker(match.start(), match.end(), match.lastgroup or '') for match in MARKERS.finditer(plain)]
    # Capitalization supplies a syntactic label, without a dictionary of people.
    # Overlapping established recipient/shared syntax keeps its existing meaning.
    for pattern, kind in ((BARE_RECIPIENT, 'named'), (REPORTED_NAMED, 'reported_named')):
        for match in pattern.finditer(_unquoted(text)):
            if any(marker.start < match.end() and match.start() < marker.end for marker in candidates):
                continue
            label = match['name'].casefold().replace('ё', 'е')
            first = label.split()[0]
            if first in SERVICE_LABELS or first in COUNT_WORDS or CALENDAR_LABEL.fullmatch(first):
                continue
            if not plain[match.start():match.end()].strip():
                continue
            body = re.split(r'[;,.!?\n]', plain[match.end():], maxsplit=1)[0].strip(' :–—-')
            if not body:
                continue
            candidates.append(Marker(match.start(), match.end(), kind))
    return sorted(candidates, key=lambda marker: marker.start)


def _fragments(text: str) -> tuple[list[Fragment], int | None]:
    plain = search_context_text(_unquoted(text).casefold().replace('ё', 'е'))
    # Preserve adopted quoted wishes while removing the discourse that the
    # existing context helper has already classified as reported/narrative.
    operative, available = list(search_context_text(text.casefold().replace('ё', 'е'))), []
    candidates = _candidate_markers(text, plain)
    for marker in candidates:
        if not _at_clause_start(plain, marker.start):
            continue
        after = plain[marker.end:]
        if not _recipient_request(after):
            # Commas can enumerate features of the old room. Stop at a new
            # recipient/current request or a sentence, rather than leaking the
            # remainder of that list into today's shared requirements.
            boundary = re.search(r'(?<!\d)[.]|[.](?!\d)|[;!?\n]', after)
            end = marker.end + boundary.start() if boundary else len(text)
            for following in candidates:
                if (marker.end <= following.start < end and following.kind not in {'ordinal', 'numbered'}
                        and _at_clause_start(plain, following.start)):
                    end = following.start
                    break
            current = re.search(r'\b(?:теперь|сейчас|на\s+этот\s+раз)\b', plain[marker.end:end])
            if current:
                end = marker.end + current.start()
            operative[marker.start:end] = ' '*(end-marker.start)
            continue
        if marker.kind != 'reported_named':
            available.append(marker)
    operative_text = ''.join(operative)
    operative_plain = search_context_text(_unquoted(operative_text).casefold().replace('ё', 'е'))
    count = _room_count(operative_plain)
    markers = []
    for marker in available:
        if not operative_plain[marker.start:marker.end].strip():
            continue
        after = operative_plain[marker.end:]
        if marker.kind in {'ordinal', 'numbered'} and not _position_request(
                after, count=count, numbered=marker.kind == 'numbered'):
            continue
        markers.append(marker)
    if _one_shared_room(operative_plain, markers, count):
        return [], None
    personal = [marker for marker in markers if marker.kind != 'shared']
    distinct_recipients = {plain[marker.start:marker.end] for marker in personal}
    # One recipient is a single request; a headcount never establishes rooms.
    if len(distinct_recipients) < 2 and not (count is not None and count >= 2):
        return [], None
    fragments = []
    if markers:
        fragments.append(Fragment('', operative_text[:markers[0].start], True))
        for index, marker in enumerate(markers):
            end = markers[index+1].start if index+1 < len(markers) else len(text)
            label = text[marker.start:marker.end].strip()
            label = label[:1].upper() + label[1:]
            fragments.append(Fragment(label, operative_text[marker.end:end], marker.kind == 'shared'))
    else:
        fragments.append(Fragment('', operative_text, True))
    return fragments, count


def _clean_context(text: str) -> str:
    text = ROOM_COUNT.sub(' ', text.casefold().replace('ё', 'е'))
    text = re.sub(r'^\s*(?:едем|еду|поедем|путешествуем|отдыхаем)\s+(?:компанией|группой)\b', ' ', text)
    text = re.sub(r'\b(?:семья|компания|группа)(?:\s+из)?\s+\d+\s*(?:человек|людей|гостей)?', ' ', text)
    text = re.sub(r'\b(?:нас|мы)\s+\d+\s*(?:человек|людей|гостей)?', ' ', text)
    text = re.sub(r'\bбез\s+(?:особых\s+)?(?:пожеланий|требований)\b', ' ', text)
    text = re.sub(r'\bне\s*принципиально\b|\bневажно\b', ' ', text)
    text = re.sub(r'\bнами\b', ' ', text)
    text = re.sub(r'\b(?:всей|большой|нашей|дружной)\s+семьей\b', ' ', text)
    text = re.sub(r'\b(?:ищем|подыскиваем|забронировать|забронируйте|бронируем|бронировать)\b', ' ', text)
    text = re.sub(r'\bкомнат[а-я]*\b', 'номер', text)
    text = re.sub(r'\bв\s+одн(?:ом|ой)\s+(?:отел\w*|гостиниц\w*)\b', ' ', text)
    text = re.sub(r'\b(?:хочет|хотят|просит|просят|предпочитает|предпочитают|желает|желают)\b',
                  lambda match: match[0] if re.search(r'\bне\s+$', text[:match.start()]) else ' ', text)
    text = re.sub(r'^\s*(?:номер\w*\s*)?(?:[-–—:/•]\s*)+', '', text)
    return re.sub(r'^\s*(?:номер\w*\s*)?[-–—:]\s*', '', text).strip(' ,;:.!?')


def _scope(text: str) -> tuple[str, RoomScope, list[str], list[str]]:
    from backend.agents.query_parser import _importance

    text = search_context_text(normalize_query_text(text))
    scope = RoomScope()
    unmapped, warnings = [], []
    patterns = [
        (r'\b(?:с|со|от)\s*(-?\d+(?:[.,]\d+)?(?![\d.,]))\s*(?:по|до|[-–—])\s*(-?\d+(?:[.,]\d+)?(?![\d.,]))(?:[- ]?(?:го|ого))?\s*этаж\w*', ('floor_min', 'floor_max')),
        (r'\b(?:этаж\w*\s+)?не\s+выше\s*(-?\d+(?:[.,]\d+)?(?![\d.,]))(?:[- ]?(?:го|ого))?(?:\s*этаж\w*)?', ('floor_max',)),
        (r'\bдо\s*(-?\d+(?:[.,]\d+)?(?![\d.,]))(?:[- ]?(?:го|ого))?\s*этаж\w*', ('floor_max',)),
        (r'\b(?:этаж\w*\s+)?(?:не ниже|от|со?)\s*(-?\d+(?:[.,]\d+)?(?![\d.,]))(?:[- ]?(?:го|ого))?\s*этаж\w*', ('floor_min',)),
        (r'\bна\s*(-?\d+(?:[.,]\d+)?(?![\d.,]))(?:[- ]?(?:м|ом))?\s*этаж\w*|\b(-?\d+(?:[.,]\d+)?(?![\d.,]))\s*этаж\b', ('exact',)),
    ]
    for pattern, keys in patterns:
        for match in list(re.finditer(pattern, text)):
            tokens = [value for value in match.groups() if value is not None]
            values = [int(value) for value in tokens if not re.search(r'[.,]', value)]
            soft, negative, skip = _importance(text, match.start(), match.end())
            if soft or negative or skip:
                unmapped.append(match[0])
                warnings.append('Уточните обязательные границы этажей: желательный диапазон или исключение этажа пока не применяется.')
            elif len(values) != len(tokens) or any(not 1 <= value <= 100 for value in values) or len(values) == 2 and values[0] > values[1]:
                unmapped.append(match[0])
                warnings.append('Укажите допустимые целые этажи от 1 до 100 и проверьте границы диапазона.')
            else:
                for key, value in zip(keys, values, strict=True):
                    if key == 'exact':
                        scope.floor_min = scope.floor_max = value
                    else:
                        setattr(scope, key, value)
            text = text[:match.start()] + ' '*(match.end()-match.start()) + text[match.end():]
    if scope.floor_min and scope.floor_max and scope.floor_min > scope.floor_max:
        unmapped.append('Противоречивые границы этажей')
        warnings.append('Минимальный этаж выше максимального. Уточните границы.')
        scope.floor_min = scope.floor_max = None
    for match in re.finditer(r'\b(?:низк\w*|нижн\w*|высок\w*|верхн\w*)\s+этаж\w*', text):
        unmapped.append(match[0])
        warnings.append('Укажите желаемый номер этажа или диапазон: «низкий/высокий» не задаёт точной границы.')
        text = text[:match.start()] + ' '*(match.end()-match.start()) + text[match.end():]
    return text, scope, unmapped, warnings


def _deduplicate(parsed: ParsedQuery | RoomRequest) -> None:
    def key(criterion):
        return json.dumps(criterion.model_dump(exclude={'label', 'weight'}, mode='json'), ensure_ascii=False, sort_keys=True)
    for name in ('must', 'nice', 'no_data', 'no_data_must'):
        setattr(parsed, name, list({key(c): c for c in getattr(parsed, name)}.values()))
    hard = {key(c) for c in parsed.must}
    parsed.nice[:] = [c for c in parsed.nice if key(c) not in hard]
    parsed.unmapped = list(dict.fromkeys(parsed.unmapped))
    parsed.clarifications = list(dict.fromkeys(parsed.clarifications))[:2]


def parse_group_query(request: SearchRequest, catalog: list[CharacteristicSpec],
                      single_parser: SingleParser) -> ParsedQuery | None:
    """Return None to retain the legacy path byte-for-byte when no split is explicit."""
    from backend.agents.query_parser import RULES, _dates, _importance

    fragments, requested_count = _fragments(request.text)
    if not fragments:
        return None
    people = [fragment for fragment in fragments if not fragment.shared]
    count = max(len(people), requested_count or 0)
    shared_text = ' '.join(fragment.text for fragment in fragments if fragment.shared)
    # Travel dates mentioned at the end still govern the whole party. A local
    # explicit month can additionally narrow the request through RoomScope.
    metadata_text = shared_text if re.search(r'\b(?:' + MONTH + r')\w*\b|\d{4}-\d{2}-\d{2}', shared_text.casefold()) else ' '.join(fragment.text for fragment in fragments)
    check_in, check_out, month, warnings = _dates(normalize_query_text(metadata_text), request)
    result = ParsedQuery(text=request.text, check_in=check_in, check_out=check_out, month=month,
                         clarifications=warnings[:2], mode='offline')
    if count > 12:
        # No silent first-twelve projection and no invented confirmation of stock.
        result.unmapped = [request.text]
        result.clarifications = [LIMIT_MESSAGE]
        return result
    shared_floor_max = None
    for fragment in fragments:
        cleaned, scope, unmapped, clarifications = _scope(_clean_context(fragment.text))
        courtyard = []
        for match in re.finditer(r'\b(?:во?\s+двор|окн\w*\s+(?:выход\w*\s+)?в\s+двор)', cleaned):
            soft, negative, skip = _importance(cleaned, match.start(), match.end())
            if not skip:
                courtyard.append((Criterion(characteristic_id='room_orientation', field='courtyard',
                                             label='Окна во двор', value=not negative), not soft))
            cleaned = cleaned[:match.start()] + ' '*(match.end()-match.start()) + cleaned[match.end():]
        single = single_parser(SearchRequest(text=cleaned, check_in=check_in, check_out=check_out), catalog)
        if any(c.characteristic_id == 'railway_noise' for c in [*single.must, *single.nice]):
            other_text = re.sub(RULES['railway_noise'], ' ', cleaned)
            if not re.search(RULES['quiet_night'], other_text):
                single.must[:] = [c for c in single.must if c.characteristic_id != 'quiet_night']
                single.nice[:] = [c for c in single.nice if c.characteristic_id != 'quiet_night']
        # Direction and floor are requests for a domain, not an amenity available=True.
        if single.facade:
            scope.facade = single.facade
            for name in ('must', 'nice'):
                values = getattr(single, name)
                values[:] = [c for c in values if not (c.characteristic_id == 'room_orientation' and c.field == 'available')]
        if re.search(r'\b(?:' + MONTH + r')\w*\b|\b\d{4}-\d{2}-\d{2}\b', cleaned):
            local_in, _, local_month, _ = _dates(cleaned, SearchRequest(text=cleaned))
            scope.month = local_in.month if local_in else local_month
        if single.pet_weight_kg is not None:
            result.pet_weight_kg = single.pet_weight_kg
            if any(c.characteristic_id == 'pets' and c.value is True for c in single.must):
                single.must.append(Criterion(characteristic_id='pets', label='Допустимый вес питомца',
                                             field='max_weight_kg', op='gte', value=single.pet_weight_kg))
            elif any(c.characteristic_id == 'pets' and c.value is True for c in single.nice):
                single.nice.append(Criterion(characteristic_id='pets', label='Допустимый вес питомца',
                                             field='max_weight_kg', op='gte', value=single.pet_weight_kg))
        if fragment.shared:
            target = result
            result.facade = scope.facade or result.facade
            result.min_floor = scope.floor_min or result.min_floor
            if scope.floor_max is not None:
                shared_floor_max = min(scope.floor_max, shared_floor_max or scope.floor_max)
        else:
            label = re.sub(r'\s+', ' ', fragment.label)
            if len(label) > 80:
                unmapped.append('Адресат: ' + fragment.label)
                clarifications = ['Сократите подпись группы до 80 символов; полный адресат сохранён.',
                                  *clarifications]
            target = RoomRequest(label=label[:80].rstrip(), scope=scope, unmapped=unmapped,
                                 clarifications=clarifications[:2])
            result.rooms.append(target)
        for name in ('must', 'nice', 'no_data', 'no_data_must'):
            for criterion in getattr(single, name):
                destination = target if criterion.characteristic_id in ROOM_CHARACTERISTICS else result
                # Local explicit domain belongs to RoomRequest.scope. Avoid enforcing
                # a fact-Scope facade requirement for a floor-only wish.
                redundant_scope = criterion.scope and single.facade and criterion.scope.facade == single.facade
                getattr(destination, name).append(criterion.model_copy(update={'scope': None}) if redundant_scope else criterion)
        for criterion, required in courtyard:
            target.no_data.append(criterion)
            if required:
                target.no_data_must.append(criterion)
        target.unmapped = list(dict.fromkeys([*target.unmapped, *single.unmapped]))
        meaningful = [message for message in single.clarifications if not message.startswith(
            ('Не нашли в запросе условий из каталога', 'Что важнее для поездки'))]
        target.clarifications = list(dict.fromkeys([*target.clarifications, *meaningful]))[:2]
        if fragment.shared:
            result.unmapped = list(dict.fromkeys([*result.unmapped, *unmapped]))
            result.clarifications = list(dict.fromkeys([*result.clarifications, *clarifications]))[:2]
    if not people:
        result.rooms = [RoomRequest(label=f'Номер {index+1}') for index in range(count)]
    elif requested_count and requested_count > len(result.rooms):
        result.rooms.extend(RoomRequest(label=f'Номер {index+1}') for index in range(len(result.rooms), requested_count))
    if shared_floor_max is not None:
        for room in result.rooms:
            maximum = min(room.scope.floor_max or shared_floor_max, shared_floor_max)
            if room.scope.floor_min and room.scope.floor_min > maximum:
                room.unmapped.append('Общий предел этажа противоречит личному диапазону')
                room.clarifications = ['Уточните общие и личные границы этажей.', *room.clarifications][:2]
            else:
                room.scope.floor_max = maximum
    for room in result.rooms:
        _deduplicate(room)
    _deduplicate(result)
    return result


def room_query(room: RoomRequest, parsed: ParsedQuery) -> ParsedQuery:
    """Flatten one request for legacy evaluators; shared conditions remain explicit.

    Matching must additionally honor floor_max/room_type in room.scope. This
    adapter carries conditions and common metadata, and never manufactures an
    orientation criterion for an otherwise empty request.
    """
    result, local = parsed.model_copy(deep=True), room.model_copy(deep=True)
    result.rooms = []
    for name in ('must', 'nice', 'no_data', 'no_data_must'):
        getattr(result, name).extend(getattr(local, name))
    result.unmapped = list(dict.fromkeys([*result.unmapped, *local.unmapped]))
    result.facade = local.scope.facade or result.facade
    result.min_floor = local.scope.floor_min or result.min_floor
    result.month = local.scope.month or result.month
    return result
