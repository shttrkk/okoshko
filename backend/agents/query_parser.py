"""Russian intent extraction against the current characteristic catalogue.

The deterministic path is deliberately small and inspectable. Optional structured
LLM extraction may add language coverage, but cannot add catalogue IDs or facts.
"""
from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache

from backend.agents.query_rules import validate_query_plan
from backend.agents.query_text import (
    clause_bounds,
    facade_is_explicit,
    normalize_query_text,
    valid_intent_match,
)
from backend.contracts import CharacteristicSpec, Criterion, ParsedQuery, Scope, SearchRequest
from backend.llm.client import UnsafeOutput
from backend.security import contains_runtime_secret
from backend.tools.registry import EV_CHARGING_NEAR_M

MONTHS = ("январ", "феврал", "март", "апрел", "ма[йяе]", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр")
SIDE_PATTERNS = {
    "NW": r"северо[ -]?запад", "NE": r"северо[ -]?восто(?:ч|к)",
    "SW": r"юго[ -]?запад", "SE": r"юго[ -]?восто(?:ч|к)",
    "W": r"запад", "E": r"восто(?:ч|к)", "N": r"север", "S": r"южн|на юг\b",
}
RULES = {
    "quiet_night": r"\bтих\w*|\bтишин\w*|выспаться|сплю чутко|чутк\w* сон|без (?:посторонн\w* )?шума|не (?:было |будет )?шумн\w*",
    "sunset_view": r"\bзакат\w*",
    "sea_view": r"(?:вид\w* (?:на |из окна на )?море|море (?:из окна|за окном|долж\w* быть видн\w* из номер\w*)|морск\w* вид|морем за окном|(?:вид\w*|горы)\s+и\s+на море|море\s+по возможности)",
    "mountain_view": r"\bгор(?:ы|ный|ного|ным|ам|ами)\b|вид\w* на гор(?!од)\w*",
    "beach_walk": r"(?:недалеко|близко|рядом) (?:с |от |до )?(?:моря|морем|пляжа)|до (?:моря|пляжа)|пешком (?:к морю|до моря)|(?:дорог\w*|путь) к морю|у (?:моря|пляжа)|близк\w* пляж|пляж\w* (?:за|в|не дальше|\d)",
    "pets": r"\bсобак\w*|\bкошк\w*|\bкот(?:ом|енк\w*)\b|\bпитом\w*|\bлабрадор\w*|\bживотн\w*",
    "family_nearby": r"\bдет(?:и|ьми|ей)\b|\bребен(?:ок|ка|ку|ком|ке|очек|очком)\b|\bмалыш\w*|\bколяск\w*|\bплощадк\w*",
    "connectivity": r"(?:хорош\w*|быстр\w*|стабильн\w*|нормальн\w*)\s+(?:интернет\w*|связ\w*|wi[ -]?fi)|\bинтернет\w*|\bwi[ -]?fi\b|\bудаленк\w*|\bудаленн?\w*\s+работ\w*|\bработ(?:а|ы|у|ать|аю|аем)\b|для работы|\bсвязь\b|\bвидеозвон\w*|\bвидеоконференц\w*|\bзум\b|\bсозвон\w*",
    "soundproofing": r"\b(?:шумо|звуко)изоляц\w*|двойн\w* (?:ок\w*|стеклопакет\w*)|\bблэкаут\w*|плотн\w* штор\w*|штор\w*[^,;.]{0,30}затемня\w*|\bкондиционер\w*",
    "room_orientation": r"высок\w* этаж\w*|\bбалкон\w*|(?:запад|восточ|север|южн)\w* сторон\w*",
    "climate": r"тепл\w* (?:вода|воды|море)|\bкупаться\b|\bпогод\w*|\bвода\b[^,;.!?]*?(?:градус|°|\d\s*[cс]\b)|вод\w* (?:от|минимум|не ниже|не холоднее|выше)",
    "car_access": r"\bпарковк\w*|на машин\w*|на автомобил\w*|на авто\b|\bподъезд\w*|\bэлектромобил\w*|\bэлектрокар\w*|\bзарядк\w*|\bзарядн\w* станц\w*",
    "night_pharmacy": r"\bаптек\w*",
    "nightlife": r"в гуще событий|ночн\w* жизн\w*|\bбар(?:ы|ов|ам|ами|ах)?\b|\bпаб(?:ы|ов)?\b|ночн\w* клуб\w*|\bклуб(?:ы|ов)\b|\bтусов\w*|\bдвиж\b|\bдвижух\w*|оживл[её]нн\w*\b(?!\s+(?:дорог|трасс|шоссе|магистрал|перекр[её]ст|проезж|улиц|движени))|\bкафе и ресторан\w*",
    'morning_sun': r'утренн\w* солнц\w*|солнц\w* (?:по утрам|утром)|солнечн\w* утр\w*',
    'evening_heat': r'без вечерн\w* жары|не жарк\w* (?:в номере )?вечером|вечерн\w* перегрев\w*',
    'railway_noise': r'шум\w* (?:от )?(?:железн\w* дорог\w*|поезд\w*)|без шум\w* поезд\w*|не слыш\w* поезд\w*',
    'beach_surface': r'песчан\w* пляж\w*|галечн\w* пляж\w*|пляж\w* (?:с )?(?:песк\w*|гальк\w*|грави\w*)',
    'accessible_route': r'доступн\w* (?:путь|маршрут) (?:к морю|до пляжа)|(?:к пляжу|к морю|до пляжа) на инвалидн\w* коляск\w*',
    'green_nearby': r'(?:парк\w*|сквер\w*|зелен\w* зон\w*) (?:рядом|поблизости)|рядом (?:парк\w*|сквер\w*)',
}
# "подальше от баров" asks for a calm area: the same measurement, opposite threshold.
AWAY = r"(?:подальше|вдали|далеко|подальш\w*)\s+от\s+$"
CALM_VENUES_MAX = 10
OUTSIDE = r"москв\w*|саратов\w*|санкт[ -]?петербург\w*|петербург\w*|спб|казан[ьиь]\w*|анап\w*|геленджик\w*|краснодар\w*|стамбул\w*|париж\w*|дуба[ий]\w*|калининград\w*|екатеринбург\w*|новосибирск\w*|ялт\w*|туапсе|крым\w*|ростов\w*|нижн\w* новгород\w*|самар\w*|твер[ьиь]\w*|минск\w*|батум\w*|красн\w* полян\w*|воронеж\w*|волгоград\w*|омск\w*|томск\w*|перм[ьиь]\w*|уф[аеуы]\w*|челябинск\w*|красноярск\w*|тюмен[ьиь]\w*|иркутск\w*|хабаровск\w*|владивосток\w*|сургут\w*|сочи-парк|тбилиси|ереван\w*|ташкент\w*|белград\w*|антал[ьи]\w*|бишкек\w*|алмат[ыи]|астан\w*|псков\w*|суздал\w*|ярославл\w*|костром\w*|владимир\w*|иванов\w*|смоленск\w*|брянск\w*|ор[её]л\w*|курск\w*|белгород\w*|рязан\w*|тул[аеуы]\w*|тамбов\w*|липецк\w*|пенза|пензе|ижевск\w*|киров\w*|чебоксар\w*|саранск\w*|йошкар\w*|ульяновск\w*|оренбург\w*|барнаул\w*|кемеров\w*|новокузнецк\w*|абакан\w*|чита|чите|улан-удэ|якутск\w*|магадан\w*|мурманск\w*|архангельск\w*|петрозаводск\w*|вологд\w*|сыктывкар\w*|грозн\w*|махачкал\w*|владикавказ\w*|ставропол\w*|пятигорск\w*|кисловодск\w*|ессентук\w*|нальчик\w*|майкоп\w*"
SOFT = r"желатель\w*|хорошо бы|по возможности|необязатель\w*|не обязател\w*|не принципиал\w*|будет плюсом"
HARD = r"(?<!не )обязатель\w*|только|важн\w*|нуж(?:ен|на|но|ны)|долж\w*|необходим\w*"
NEARBY = {
    "beach_walk": (r"\b(?:пляж\w*|мор[еяю]|морем)\b", "minutes", 10, "До пляжа", "мин пешком"),
    "family_nearby": (r"\bплощадк\w*", "playground_distance_m", 500, "Детская площадка", "м"),
    "car_access": (r"\bпарков\w*", "parking_distance_m", 500, "Парковка", "м"),
    "night_pharmacy": (r"\bаптек\w*", "distance_m", 500, "Ночная аптека", "м"),
}
# Labels for numeric criteria, so the threshold the search applies is visible in the chip.
FIELD_LABELS = {(cid, field): (name, unit) for cid, (_, field, _, name, unit) in NEARBY.items()}
FIELD_LABELS.update({("car_access", "ev_distance_m"): ("Зарядка для электромобиля", "м")})
CAR_PARKING = r"\bпарков\w*|\bприпарков\w*|на машин\w*|на авто\w*|\bподъезд\w*|\bподъехать\b"
CAR_CHARGING = r"\bзарядк\w*\s+для\s+(?:электромобил|электрокар)\w*|\bэлектромобил\w*|\bэлектрокар\w*|\bзарядк\w*|\bзарядн\w* станц\w*"


def normalized(text: str) -> str:
    return normalize_query_text(text)


@lru_cache(maxsize=1024)
def _phrase_pattern(phrase: str) -> str:
    # Inflect long Russian words, retaining short words to avoid false positives.
    endings = r"(?:иями|ами|ого|ему|ому|ыми|ими|ая|яя|ое|ее|ые|ий|ый|ой|ам|ям|ах|ях|ом|ем|ов|ев|ы|и|а|я|у|ю|е|о)$"
    words = re.findall(r"[\w]+", normalized(phrase))
    pieces = []
    for word in words:
        stem = re.sub(endings, "", word) if len(word) > 4 else word
        pieces.append(re.escape(stem) + (r"\w*" if len(word) > 4 and len(stem) >= 3 else ""))
    return r"\b" + r"[\s-]+".join(pieces) + r"\b" if pieces else r"(?!)"


def _dates(text: str, request: SearchRequest) -> tuple[date | None, date | None, int, list[str]]:
    dates = []
    warnings = []
    for match in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b|\b(\d{1,2})\.(\d{2})(?:\.(\d{4}))?\b", text):
        parts = match.groups()
        try:
            dates.append(date(int(parts[0] or parts[5] or date.today().year), int(parts[1] or parts[4]), int(parts[2] or parts[3])))
        except ValueError:
            warnings.append("Проверьте даты поездки: такой даты в календаре нет.")
    month_matches = [(m.start(), i) for i, stem in enumerate(MONTHS, 1) for m in re.finditer(r"\b" + stem, text)]
    month = min(month_matches)[1] if month_matches else 9
    compact = re.search(r"\b(\d{1,2})\s*[-–—]\s*(\d{1,2})\.(\d{2})(?:\.(20\d{2}))?\b", text)
    if compact:
        try:
            dates = [date(int(compact[4] or date.today().year), int(compact[3]), int(compact[i])) for i in (1, 2)]
        except ValueError:
            warnings.append("Проверьте даты поездки: такой даты в календаре нет.")
    natural = list(re.finditer(r"\b(\d{1,2})\s*(?:(?:[-–—]|по)\s*(\d{1,2})\s*)?(" + "|".join(MONTHS) + r")\w*(?:\s+(20\d{2}))?", text))
    if natural and not dates:
        try:
            shared_year = next((int(match[4]) for match in reversed(natural) if match[4]), date.today().year)
            for match in natural:
                natural_month = next(i for i, stem in enumerate(MONTHS, 1) if re.fullmatch(stem, match[3]))
                year = int(match[4]) if match[4] else shared_year
                dates.append(date(year, natural_month, int(match[1])))
                if match[2]:
                    dates.append(date(year, natural_month, int(match[2])))
        except ValueError:
            dates = []
            warnings.append("Проверьте даты поездки: такой даты в календаре нет.")
    if warnings:
        # A second valid date must not silently replace an invalid arrival date.
        dates = []
    check_in = request.check_in or (dates[0] if dates else None)
    check_out = request.check_out or (dates[1] if len(dates) > 1 else None)
    if check_in:
        month = check_in.month
    if check_in and check_out and check_out <= check_in:
        warnings.append("Дата выезда должна быть позже даты заезда.")
        check_out = None
    return check_in, check_out, month, warnings


def _importance(text: str, start: int, end: int) -> tuple[bool, bool, bool]:
    left, right = clause_bounds(text, start, end)
    before, after = text[left:start], text[end:right]
    if re.search(r"\bне (?:нуж\w*|важ\w*|интерес\w*|треб\w*|ищ\w*)\s*(?:мне\s*)?(?:вид\w*\s*(?:на\s*)?)?$", before) or re.match(r"\s*(?:мне )?не (?:важ\w*|нуж\w*|интерес\w*|треб\w*|ищ\w*)", after):
        return False, False, True
    markers = [(m.start(), True) for m in re.finditer(SOFT, before)] + [(m.start(), False) for m in re.finditer(HARD, before)]
    soft = max(markers, default=(-1, False))[1]
    if re.match(r"\s*(?:необязатель\w*|не обязател\w*|желатель\w*|по возможности|не принципиал\w*|(?:будет )?плюсом)", after):
        soft = True
    if re.search(r'по возможности|желатель\w*|будет плюсом', text[start:end]):
        soft = True
    if re.search(r"(?:^|[,;])\s*можно(?: бы)?\s*$", before) and not re.search(r"\b(?:собак|кошк|котом|питом)\w*", text[start:end]):
        soft = True
    negative = bool(re.search(r"(?:\bбез|\bне)\s*(?:вид\w*\s*(?:на\s*)?)?$", before[-35:]))
    # Negated noise is a positive request for quiet; "не обязательно" is a preference.
    if re.search(r"не обязател\w*\s*$", before) or re.match(r"без шума|не шумн", text[start:end]):
        negative = False
    return soft, negative, False


def _numeric(text: str, start: int, end: int, field: str, default_op: str, default_value):
    left, right = clause_bounds(text, start, end)
    offset = max(left, start - 65)
    window = text[offset:min(right, end + 90)]
    distance = r'(?:километр\w*|км\b|метр\w*|м\b)'
    units = {
        'minutes': r'(?:мин(?:ут\w*)?|час\w*)',
        'water_c': r'(?:°\s*[cс]?|градус\w*|[cс]\b)',
        'download_mbps': r'(?:мбит(?:/с)?|mbps|мб/с|мегабит\w*|мегабайт(?:/с)?|гбит(?:/с)?|gbps|гигабит\w*)',
        'european_aqi': r'(?:aqi|единиц\w*)', 'night_db_max': r'дб',
        'parking_distance_m': distance, 'playground_distance_m': distance,
        'distance_m': distance, 'ev_distance_m': distance,
    }
    unit = units.get(field)
    if not unit and field not in {'available', 'windows', 'blackout', 'balcony', 'air_conditioning'}:
        unit = r'(?:час\w*|метр\w*|м\b|%|балл\w*|люкс\w*|км\b)'
    if not unit:
        return default_op, default_value
    operators = r'не менее|не меньше|не ниже|не холоднее|хотя бы|минимум|больше|выше|от|≥|>=|не более|не дальше|не дольше|не выше|не больше|максимум|менее|меньше|ниже|до|≤|<='
    pattern = r'(?<![-\w.,])(?P<op>' + operators + r')?\s*(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>' + unit + ')'
    candidates = list(re.finditer(pattern, window))
    if field == 'european_aqi':
        candidates += list(re.finditer(r'(?P<unit>aqi)\s*(?P<op>' + operators + r')?\s*(?P<num>\d+(?:[.,]\d+)?)', window))

    def gap(match):
        return max(0, start-(offset+match.end()), offset+match.start()-end)

    # A distance belongs to the closest named POI, even within one clause.
    anchors = {
        'parking_distance_m': CAR_PARKING,
        'ev_distance_m': CAR_CHARGING,
        'playground_distance_m': r'\bплощадк\w*',
        'distance_m': r'\b(?:аптек\w*|пляж\w*|мор[еяю])\b',
    }
    if field in anchors:
        competitors = [m for name, pattern in anchors.items() if name != field
                       for m in re.finditer(pattern, text[left:right])]
        candidates = [candidate for candidate in candidates if not any(
            max(0, left+other.start()-(offset+candidate.end()), offset+candidate.start()-(left+other.end())) < gap(candidate)
            for other in competitors)]
    found = min(candidates, key=gap, default=None)
    if not found:
        return default_op, default_value
    op = 'gte' if found['op'] in {'не менее', 'не меньше', 'не ниже', 'не холоднее', 'хотя бы', 'минимум', 'больше', 'выше', 'от', '≥', '>='} else default_op
    if found['op'] in {'не более', 'не дальше', 'не дольше', 'не выше', 'не больше', 'максимум', 'менее', 'меньше', 'ниже', 'до', '≤', '<='}:
        op = 'lte'
    number = float(found['num'].replace(',', '.'))
    if field == 'minutes' and found['unit'].startswith('час'):
        number *= 60
    if field == 'download_mbps':
        if found['unit'].startswith('мегабайт'):
            number *= 8
        elif found['unit'].startswith(('гбит', 'gbps', 'гигабит')):
            number *= 1000
    if field.endswith('_distance_m') or field == 'distance_m':
        if found['unit'].startswith(('км', 'километр')):
            number *= 1000
    return op, number


def _nearby_intents(text: str) -> dict:
    """Attach proximity only to a known POI in the same clause."""
    targets = [(cid, match) for cid, (pattern, *_) in NEARBY.items() for match in re.finditer(pattern, text)]
    attached = {}
    for near in re.finditer(r"\b(?:рядом|поблизости)\b", text):
        candidates = [(cid, match) for cid, match in targets
                      if not re.search(r"[,;.!?]", text[min(near.end(), match.end()):max(near.start(), match.start())])
                      and min(abs(near.start() - match.end()), abs(match.start() - near.end())) <= 40]
        if candidates:
            cid, match = min(candidates, key=lambda item: min(abs(near.start() - item[1].end()), abs(item[1].start() - near.end())))
            attached.setdefault(cid, (match, []))[1].append(near)
    return attached


def _criterion_label(criterion: Criterion, fallback: str, unit: str | None = None) -> str:
    cid = criterion.characteristic_id
    if (cid, criterion.field) in FIELD_LABELS or unit and criterion.op in {"lte", "gte"}:
        # A spec name may carry its default threshold in parentheses; the chip shows the actual one.
        name, unit = FIELD_LABELS.get((cid, criterion.field), (re.sub(r"\s*\([^)]*\)$", "", fallback), unit))
        sign = {"lte": "≤", "gte": "≥", "eq": "="}.get(criterion.op, criterion.op)
        threshold = f"{criterion.value:g}".replace(".", ",") if isinstance(criterion.value, int | float) else str(criterion.value)
        fallback = f"{name} {sign} {threshold} {unit}"
    flags = [label for active, label in ((criterion.no_stairs, "без лестниц"), (criterion.no_crossings, "без переходов")) if active]
    return ", ".join([fallback, *flags])


def _fallback(request: SearchRequest, catalog: list[CharacteristicSpec]) -> ParsedQuery:
    text = normalized(request.text)
    check_in, check_out, month, warnings = _dates(text, request)
    parsed = ParsedQuery(text=request.text, check_in=check_in, check_out=check_out, month=month, clarifications=warnings[:2])
    outside = list(re.finditer(r"\b(?:" + OUTSIDE + r")\b", text))
    outside = [m for m in outside if not re.search(r"(?:из|от|вылет из)\s+$", text[max(0, m.start()-15):m.start()])]
    # Unknown capitalised place names are caught without an online gazetteer.
    for place in re.finditer(r"\b(?:[Вв](?:о)?|город(?:е)?)\s+([А-ЯЁ][а-яё-]{2,})", request.text):
        name = normalized(place[1])
        if not re.fullmatch(r"сочи|адлер\w*|сириус\w*|имеретин\w*|номер\w*|отел\w*|гостиниц\w*|центре|" + "|".join(m + r"\w*" for m in MONTHS), name):
            outside.append(place)
    if outside:
        parsed.unmapped.append("region_out_of_scope")
        parsed.clarifications = ["Сейчас данные собраны для Сочи (центр, Адлер и Сириус). Укажите этот регион; для другого города нужен отдельный сбор данных."]
    facade_match = next(((side, match) for side, pattern in SIDE_PATTERNS.items()
                         for match in re.finditer(pattern + r'\w*', text) if facade_is_explicit(text, match)), None)
    if facade_match and not _importance(text, facade_match[1].start(), facade_match[1].end())[0]:
        parsed.facade = facade_match[0]
    floor = re.search(r"(?:с|от|не ниже)\s*(\d{1,2})(?:\s*[-–]?(?:го|ого))?\s*(?:этажа?|эт\b)|\b(\d{1,2})\s*\+\s*этаж", text)
    reversed_floor = re.search(r'этаж\w*\s*(?:не ниже|от)\s*(\d{1,2})\b', text)
    exact_floor = re.search(r"на\s+(\d{1,2})(?:[- ]?(?:м|ом))?\s*этаже", text)
    if not exact_floor:
        exact_floor = re.search(r'\b(\d{1,2})\s*этаж\b', text)
    floor_range = re.search(r"(?:с|от)\s*(\d{1,2})\s*(?:по|до|[-–—])\s*(\d{1,2})(?:\s*[-–]?(?:го|ого))?\s*этаж", text)
    floor_ceiling = re.search(r"(?:не выше|до)\s*(\d{1,2})(?:\s*[-–]?(?:го|ого))?\s*этаж", text)
    floor_match = floor_range or floor or reversed_floor or exact_floor or floor_ceiling
    requested_floor = int(floor_range[1] if floor_range else (floor[1] or floor[2]) if floor else reversed_floor[1] if reversed_floor else exact_floor[1]) if floor_range or floor or reversed_floor or exact_floor else None
    requested_max = int(floor_range[2]) if floor_range else int(exact_floor[1]) if exact_floor else int(floor_ceiling[1]) if floor_ceiling else None
    if requested_floor and requested_max and requested_floor > requested_max:
        warnings.append("Минимальный этаж не может быть выше максимального.")
        requested_floor, requested_max = None, None
    floor_is_soft = bool(floor_match and _importance(text, floor_match.start(), floor_match.end())[0])
    if not floor_is_soft:
        parsed.min_floor = requested_floor or None
    hard_floor_max = None if floor_is_soft else requested_max
    weight = re.search(r"(?:собак\w*|питом\w*|лабрадор\w*|кошк\w*|кот(?:ом|енк\w*))[^,;.]{0,35}?(\d+(?:[.,]\d+)?)\s*(?:кг|килограмм\w*)", text)
    if weight and float(weight[1].replace(",", ".")) <= 100:
        parsed.pet_weight_kg = float(weight[1].replace(",", "."))
    parsed.persona = next((name for name, pattern in [
        ("семья с детьми", r"дет(?:и|ьми|ей)|ребен|коляск|малыш"),
        ("путешествие с питомцем", RULES["pets"]), ("удалённая работа", RULES["connectivity"]),
        ("чуткий сон", RULES["quiet_night"]), ("романтическая поездка", r"годовщин|романти|двоих|пары")]
        if re.search(pattern, text)), None)
    consumed = []
    positions = {}
    nearby = _nearby_intents(text)
    for spec in catalog:
        if spec.status != "active":
            continue
        matches = [m for phrase in [spec.name_ru, *spec.synonyms] for m in re.finditer(_phrase_pattern(phrase), text)]
        if spec.id in RULES:
            matches += list(re.finditer(RULES[spec.id], text))
        if spec.id in nearby:
            matches.append(nearby[spec.id][0])
            consumed.extend((m.start(), m.end()) for m in nearby[spec.id][1])
        if spec.id == "room_orientation" and (facade_match or floor_match):
            matches += [facade_match[1]] if facade_match else [floor_match]
        matches = [m for m in matches if valid_intent_match(spec.id, text, m)]
        if not matches:
            continue
        consumed.extend((m.start(), m.end()) for m in matches)
        matches = [m for m in matches if not _importance(text, m.start(), m.end())[2]]
        if not matches:
            continue
        # Longest phrase is most useful for negation ("без вида на море").
        match = min(matches, key=lambda m: (m.start(), -(m.end()-m.start())))
        positions[spec.id] = match.start()
        soft, negative, skip = _importance(text, match.start(), match.end())
        if skip:
            continue
        field, op, value = spec.search.field, spec.search.default_op, spec.search.default_value
        numeric_match = match
        explicit_measurement = False
        if spec.id == "quiet_night" and re.search(r"\d+\s*дб", text):
            field, op, value = "night_db_max", "lte", 45
        elif spec.id == "car_access" and re.search(CAR_CHARGING, text) and not re.search(CAR_PARKING, text):
            field, op, value = "ev_distance_m", "lte", EV_CHARGING_NEAR_M
        anchors = {
            'car_access': CAR_CHARGING if field == 'ev_distance_m' else CAR_PARKING,
            'family_nearby': r'\bплощадк\w*', 'night_pharmacy': r'\bаптек\w*',
            'beach_walk': r'\b(?:пляж\w*|мор[еяю])\b',
            'climate': r'\bвод\w*',
        }
        if spec.id in anchors:
            target_matches = [m for m in re.finditer(anchors[spec.id], text)
                              if valid_intent_match(spec.id, text, m)]
            if target_matches:
                numeric_match = target_matches[0]
                numeric_field = {'beach_walk': 'distance_m', 'family_nearby': 'playground_distance_m',
                                 'night_pharmacy': 'distance_m'}.get(spec.id, field)
                explicit = [(m, _numeric(text, m.start(), m.end(), numeric_field, 'lte' if spec.id in NEARBY else op, None))
                            for m in target_matches]
                measured = next(((m, comparison) for m, comparison in explicit if comparison[1] is not None), None)
                if measured is None and spec.id == 'beach_walk':
                    numeric_field = 'minutes'
                    measured = next(((m, comparison) for m in target_matches
                                     if (comparison := _numeric(text, m.start(), m.end(), numeric_field, 'lte', None))[1] is not None), None)
                if measured:
                    numeric_match, (op, value) = measured
                    field = numeric_field
                    explicit_measurement = True
        nearby_preference = None
        if spec.id in nearby:
            if not explicit_measurement:
                numeric_match = nearby[spec.id][0]
            _, nearby_field, nearby_value, _, _ = NEARBY[spec.id]
            nearby_soft, _, nearby_skip = _importance(text, numeric_match.start(), numeric_match.end())
            if not nearby_skip and nearby_soft == soft and not explicit_measurement:
                field, op, value = nearby_field, "lte", nearby_value
            elif not nearby_skip and nearby_soft != soft:
                nearby_op, nearby_value = _numeric(text, numeric_match.start(), numeric_match.end(), nearby_field, "lte", nearby_value)
                nearby_preference = (nearby_soft, Criterion(characteristic_id=spec.id, label=spec.name_ru,
                                                          field=nearby_field, op=nearby_op, value=nearby_value))
        op, value = _numeric(text, numeric_match.start(), numeric_match.end(), field, op, value)
        if negative and isinstance(value, bool):
            value = not value
        away = spec.id == "nightlife" and (negative or re.search(AWAY, text[max(0, match.start()-25):match.start()]))
        if away:
            op, value = "lte", CALM_VENUES_MAX
        if spec.id == "nightlife":
            # "рядом бары" / "кафе рядом": the proximity is part of the 500 m density measure.
            consumed.extend((near.start(), near.end()) for near in re.finditer(r"\bрядом\b|\bподальше\b|\bвдали\b", text)
                            if any(min(abs(near.start() - m.end()), abs(m.start() - near.end())) <= 25
                                   and not re.search(r"[,;.!?]", text[min(near.end(), m.end()):max(near.start(), m.start())]) for m in matches))
        if spec.id == "soundproofing":
            matched_text = match[0]
            if re.search(r"двойн\w* (?:ок|стеклопакет)", matched_text):
                field, value = "windows", "double"
            elif re.search(r"блэкаут|плотн\w* штор|штор\w*.*затемня", matched_text):
                field, value = "blackout", not negative
            elif re.search(r"кондиционер", matched_text):
                field, value = "air_conditioning", not negative
        if spec.id == "room_orientation" and re.search(r"балкон", match[0]):
            field, value = "balcony", not negative
        if spec.id == 'beach_surface':
            surface = next((surface for surface, pattern in (
                ('pebblestone', r'галечн\w*|гальк\w*'), ('gravel', r'грави\w*'), ('sand', r'песчан\w*|песк\w*'))
                            if re.search(pattern, match[0])), None)
            if surface is not None:
                field, op, value = 'surface', 'eq', surface
        scope = None
        if spec.scope != "hotel" and (parsed.facade or parsed.min_floor or hard_floor_max):
            scope = Scope(facade=parsed.facade, floor_min=parsed.min_floor,
                          floor_max=hard_floor_max)
        if spec.id == "room_orientation" and soft and (facade_match or floor_match):
            scope = Scope(facade=facade_match[0] if facade_match else parsed.facade,
                          floor_min=requested_floor, floor_max=requested_max)
        criterion = Criterion(characteristic_id=spec.id, label=spec.name_ru, field=field, op=op, value=value, scope=scope)
        (parsed.nice if soft else parsed.must).append(criterion)
        if nearby_preference:
            nearby_soft, nearby_criterion = nearby_preference
            nearby_criterion.label = _criterion_label(nearby_criterion, spec.name_ru)
            (parsed.nice if nearby_soft else parsed.must).append(nearby_criterion)
        if spec.id == "soundproofing":
            for extra_field, pattern, extra_value in [("windows", r"двойн\w* (?:ок\w*|стеклопакет\w*)", "double"), ("blackout", r"блэкаут\w*|плотн\w* штор\w*|штор\w*[^,;.]{0,30}затемня\w*", True), ("air_conditioning", r"кондиционер\w*", True)]:
                extra = re.search(pattern, text)
                if extra and extra_field != field:
                    extra_soft, extra_negative, extra_skip = _importance(text, extra.start(), extra.end())
                    if not extra_skip:
                        (parsed.nice if extra_soft else parsed.must).append(Criterion(characteristic_id=spec.id, label=spec.name_ru, field=extra_field,
                            value=not extra_negative if isinstance(extra_value, bool) else extra_value, scope=scope))
        if spec.id == "car_access" and field != "ev_distance_m":
            charging = re.search(CAR_CHARGING, text)
            if charging:
                charging_soft, _, charging_skip = _importance(text, charging.start(), charging.end())
                if not charging_skip:
                    charging_op, charging_value = _numeric(text, charging.start(), charging.end(), 'ev_distance_m', 'lte', EV_CHARGING_NEAR_M)
                    extra = Criterion(characteristic_id=spec.id, label=spec.name_ru, field="ev_distance_m", op=charging_op, value=charging_value)
                    extra.label = _criterion_label(extra, spec.name_ru)
                    (parsed.nice if charging_soft else parsed.must).append(extra)
        if spec.id == "beach_walk":
            for flag, extra_field, label, pattern, extra_value in [
                ("no_stairs", "has_steps", "Без лестниц до пляжа", r"без (?:лестниц\w*|ступен\w*)|\bколяск\w*", False),
                ("no_crossings", "crossings", "Без переходов до пляжа", r"(?:без (?:переход\w*|пересечени\w*)(?:\s+(?:и\s+)?переход\w*)?|без лестниц\w*\s+и\s+переход\w*)(?:\s+(?:через\s+)?(?:трасс\w*|дорог\w*|шоссе|железн\w*\s+дорог\w*))?|переход\w*\s+через\s+дорог\w*\s+исключен\w*", []),
            ]:
                for extra in re.finditer(pattern, text):
                    consumed.append((extra.start(), extra.end()))
                    extra_soft = _importance(text, extra.start(), extra.end())[0]
                    if extra_soft == soft:
                        setattr(criterion, flag, True)
                    else:
                        # Preserve an explicitly softer route preference as a preference.
                        group = parsed.nice if extra_soft else parsed.must
                        if not any(c.characteristic_id == spec.id and c.field == extra_field and c.value == extra_value for c in group):
                            group.append(Criterion(characteristic_id=spec.id, label=label, field=extra_field, value=extra_value))
        criterion.label = _criterion_label(criterion, "Спокойный район" if spec.id == "nightlife" and away else spec.name_ru, spec.search.unit)
    residual = list(text)
    for start, end in consumed:
        residual[start:end] = " " * (end-start)
    remaining = "".join(residual)
    remaining = re.sub(r'оставить\s+(?:ее|его|машин\w*|автомобил\w*)?\s*рядом', ' ', remaining)
    # These clauses describe who is staying home, not hotel restrictions.
    remaining = re.sub(r'\bне\s+с\s+(?:собак|кошк|питом)\w*|\bдет\w*\s+не\s+берем|\bс\s+дет\w*\s+не\s+поедем', ' ', remaining)
    remaining = re.sub(r"\b(?:" + OUTSIDE + "|" + "|".join(MONTHS) + r")\w*\b", " ", remaining)
    ignored = r"сочи|адлер\w*|сириус\w*|имеретин\w*|центр\w*|номер\w*|отел\w*|гостиниц\w*|вид\w*|сторон\w*|этаж\w*|окн\w*|ноч\w*|днем|летом|утром|пешком|мин\w*|кг|градус\w*|мбит|мб|с|в|во|на|за|к|от|до|и|но|а|не|без|для|из|по|у|со|же|бы|или|нам|мне|мы|я|нас|над|около|это|его|ее|их|чтобы|где|ищу|найти|хочу|хотим|жить|живем|нуж\w*|обязател\w*|важ\w*|обязатель\w*|желатель\w*|необязатель\w*|возможности|хорошо|только|более|менее|ниже|выше|дальше|больше|дольше|максимум|сентябр\w*|пожалуйста|спасибо|ехать|едем|еду|отдых\w*|поездк\w*|пару|двоих|пары|романти\w*|годовщин\w*|муж\w*|жена|спит|сплю|чутко|чуткий|требуется|разрешен\w*|принима\w*|допуска\w*|принципиаль\w*|размер\w*|вес\w*|был\w*|будет|есть|быть|метр\w*|море|моря|пляж\w*|вод\w*|тепл\w*"
    ignored += r'|найди\w*|главн\w*|отпуск\w*|отдохн\w*|смотреть|любоват\w*|именно|остальн\w*|неважн\w*|принципиал\w*|доступност\w*|пеш\w*|ходьб\w*|поблизости|плюс\w*|можно|хотел\w*|путешеств\w*|один|одна|двое|двумя|хотя|минимум|секунд\w*|скорост\w*|хорош\w*|быстр\w*|стабильн\w*|нормальн\w*|стар\w*|весит|весом|оставить|устраива\w*|меня|мегабит\w*|mbps|aqi|километр\w*|даты|числ\w*|сво\w*|полностью|очень|берем|поедем|интерес\w*|кажд\w*|фасад\w*|вечером|пределах|радиусе|час\w*|гулять|спокойно|штор\w*|кило\w*|станц\w*|зарядн\w*|исключен\w*'
    if re.search(r"вид\w* на город\w*|городск\w* (?:вид|пейзаж)\w*|панорам\w* город\w*", text):
        # No city-view characteristic yet: say so instead of guessing «горы».
        warnings.append("Вид на город пока не рассчитываем: доступны вид на море, на горы и закат из окна.")
        remaining = re.sub(r"\bгород\w*|\bгородск\w*|\bпанорам\w*", " ", remaining)
    tokens = [word for word in re.findall(r"[а-яa-z][а-яa-z-]*", remaining) if len(word) > 2 and not re.fullmatch(ignored, word)]
    if tokens:
        parsed.unmapped.append(" ".join(dict.fromkeys(tokens)))
    parsed.clarifications = list(dict.fromkeys([*parsed.clarifications, *warnings]))[:2]
    if not parsed.must and not parsed.nice and not outside:
        parsed.clarifications.append("Что важнее для поездки: тишина, вид, дорога к морю или другие условия?")
    if re.search(r"высок\w* этаж", text) and not parsed.min_floor:
        parsed.clarifications.append("Какой минимальный этаж вам подходит?")
    if parsed.unmapped and not outside and len(parsed.clarifications) < 2:
        parsed.clarifications.append("Не удалось сопоставить с каталогом: " + parsed.unmapped[-1] + ". Уточните, что здесь обязательно.")
    parsed.clarifications = list(dict.fromkeys(parsed.clarifications))[:2]
    parsed.must.sort(key=lambda c: positions[c.characteristic_id])
    parsed.nice.sort(key=lambda c: positions[c.characteristic_id])
    return parsed


def parse_query(request: SearchRequest, catalog: list[CharacteristicSpec], llm=None) -> ParsedQuery:
    """Extract catalogue-backed conditions; offline and user-edited chips are first class."""
    baseline = _fallback(request, catalog)
    if request.parsed:
        result = request.parsed.model_copy(deep=True)
        result.text = request.text or result.text
        if request.check_in:
            result.check_in, result.month = request.check_in, request.check_in.month
        if request.check_out:
            result.check_out = request.check_out
    elif llm is not None and "region_out_of_scope" not in baseline.unmapped:
        class QueryPlan(ParsedQuery):
            def grounded(self):
                # Check the original decoded provider fields before normalization
                # can discard text; escaped protected values must still be caught.
                if contains_runtime_secret(self.model_dump(mode='json'), (getattr(llm, 'api_key', ''),)):
                    raise UnsafeOutput('Query plan contains a protected value')
                validated = validate_query_plan(self, baseline, catalog)
                for field in type(self).model_fields:
                    setattr(self, field, getattr(validated, field))
                return self

            @classmethod
            def model_validate_json(cls, json_data, **kwargs):
                # Semantic refusal is outside Pydantic's ValueError wrapping:
                # UnsafeOutput must stop transport retries and JSON repair.
                return super().model_validate_json(json_data, **kwargs).grounded()

            @classmethod
            def model_validate(cls, obj, **kwargs):
                return super().model_validate(obj, **kwargs).grounded()

        result, mode = llm.structured(QueryPlan, [
            {"role": "system", "content": "Разбери русский запрос только по каталогу. Прямые требования обязательны; желательно — nice. Не выдумывай данные отелей. Сохрани отрицания, числа, даты и неизвестные слова. Не более двух уточнений. Каталог: " + json.dumps([{ "id": s.id, "name": s.name_ru, "synonyms": s.synonyms, "search": s.search.model_dump()} for s in catalog if s.status == "active"], ensure_ascii=False)},
            {"role": "user", "content": request.text}], fallback=lambda: baseline.model_dump(), task="parse_query", budget_s=2.2)
        try:
            # Custom local clients must obey the same boundary as the transport.
            if not isinstance(result, QueryPlan):
                result = validate_query_plan(result, baseline, catalog)
        except UnsafeOutput:
            result, mode = baseline, 'offline'
        result.mode = mode
        result.text = request.text
    else:
        result = baseline
    active = {s.id for s in catalog if s.status == "active"}
    for group in (result.must, result.nice):
        invalid = [c for c in group if c.characteristic_id not in active]
        result.unmapped.extend(c.label for c in invalid)
        group[:] = [c for c in group if c.characteristic_id in active]
    if "region_out_of_scope" in baseline.unmapped:
        result.unmapped = list(dict.fromkeys([*result.unmapped, "region_out_of_scope"]))
        result.clarifications = baseline.clarifications
    return result
