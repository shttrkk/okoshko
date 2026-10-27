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


@dataclass(frozen=True)
class Fragment:
    label: str
    text: str
    shared: bool = False


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
    body = re.sub(r'^\s*[-–—:]?\s*', '', after)
    # Explicit facts about people and correspondence do not request a position.
    return not re.match(r'(?:\d+\s*(?:лет|года?|годиков)\b|'
                        r'(?:сказал\w*|говор\w*|сообщ\w*|ответил\w*|написал\w*|написали|'
                        r'позвон\w*|звон\w*|отправ\w*|прислал\w*|там\s+был\w*)\b|'
                        r'не\s+(?:нуж\w*|треб\w*)\s+(?:отдельн\w*\s+)?(?:номер\w*|комнат\w*)\b)', body)


def _position_request(after: str, *, count: int | None, numbered: bool) -> bool:
    body = re.sub(r'^\s*(?:номер\w*|комнат\w*)?\s*[-–—:]?\s*', '', after)
    if re.match(r'(?:из|раз|этаж|на\s+(?:план\w*|фото\w*|снимк\w*)|в\s+(?:рейтинг\w*|списк\w*))\b', body):
        return False
    return bool(numbered or count or re.match(r'(?:с\b|без\b|тих\w*|тишин\w*|не\b|вид\w*|балкон\w*|море\b|горы\b|во\s+двор)', body))


def _fragments(text: str) -> tuple[list[Fragment], int | None]:
    plain = search_context_text(_unquoted(text).casefold().replace('ё', 'е'))
    count = _room_count(plain)
    markers = []
    for match in MARKERS.finditer(plain):
        if not _at_clause_start(plain, match.start()):
            continue
        after = plain[match.end():]
        if not _recipient_request(after):
            continue
        if (match['ordinal'] or match['numbered']) and not _position_request(
                after, count=count, numbered=bool(match['numbered'])):
            continue
        markers.append(match)
    personal = [match for match in markers if not match['shared']]
    distinct_recipients = {plain[match.start():match.end()] for match in personal}
    # One recipient is a single request; a headcount never establishes rooms.
    if len(distinct_recipients) < 2 and not (count is not None and count >= 2):
        return [], None
    fragments = []
    if markers:
        fragments.append(Fragment('', text[:markers[0].start()], True))
        for index, match in enumerate(markers):
            end = markers[index+1].start() if index+1 < len(markers) else len(text)
            label = text[match.start():match.end()].strip()
            label = label[:1].upper() + label[1:]
            fragments.append(Fragment(label, text[match.end():end], bool(match['shared'])))
    else:
        fragments.append(Fragment('', text, True))
    return fragments, count


def _clean_context(text: str) -> str:
    text = ROOM_COUNT.sub(' ', text.casefold().replace('ё', 'е'))
    text = re.sub(r'\b(?:семья|компания|группа)(?:\s+из)?\s+\d+\s*(?:человек|людей|гостей)?', ' ', text)
    text = re.sub(r'\b(?:нас|мы)\s+\d+\s*(?:человек|людей|гостей)?', ' ', text)
    text = re.sub(r'\bбез\s+(?:особых\s+)?(?:пожеланий|требований)\b', ' ', text)
    text = re.sub(r'\bне\s*принципиально\b|\bневажно\b', ' ', text)
    text = re.sub(r'\bнами\b', ' ', text)
    text = re.sub(r'\b(?:всей|большой|нашей|дружной)\s+семьей\b', ' ', text)
    text = re.sub(r'\b(?:ищем|подыскиваем|забронировать|забронируйте|бронируем|бронировать)\b', ' ', text)
    text = re.sub(r'\bкомнат[а-я]*\b', 'номер', text)
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
    metadata_text = shared_text if re.search(r'\b(?:' + MONTH + r')\w*\b|\d{4}-\d{2}-\d{2}', shared_text.casefold()) else request.text
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
