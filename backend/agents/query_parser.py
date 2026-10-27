"""Russian intent extraction against the current characteristic catalogue.

The deterministic path is inspectable. Optional structured LLM suggestions must
match independently grounded local conditions before they can be cached or used.
"""
from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache

from backend.agents.query_rules import validate_query_plan
from backend.agents.query_text import (
    catalogue_suggestions,
    clause_bounds,
    facade_is_explicit,
    normalize_query_text,
    search_context_text,
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
VENUE_NOUN = (r'(?:кафе\b|ресторан(?:а|у|ом|е|ы|ов|ам|ами|ах)?\b|'
              r'бар(?:а|у|ом|е|ы|ов|ам|ами|ах)?\b|паб(?:а|у|ом|е|ы|ов|ам|ами|ах)?\b|'
              r'клуб(?:а|у|ом|е|ы|ов|ам|ами|ах)?\b)')
RULES = {
    "quiet_night": r"\bтих\w*|\bтишин\w*|\b(?:по)?тише\b|\b(?:по)?спокойнее\b|\bменее шумн\w*|выспаться|сплю чутко|чутк\w* сон|без (?:посторонн\w* )?шума|не (?:было |будет )?шумн\w*",
    "sunset_view": r"\bзакат\w*|\bзаходящ\w*\s+солнц\w*",
    "sea_view": r"(?:вид\w* (?:на |из окна на )?море|море (?:из окна|за окном|в окне|долж\w* быть видн\w* из номер\w*)|морск\w* вид|морем за окном|(?:вид\w*|горы)\s+и\s+на море|море\s+по возможности)",
    "mountain_view": r"\bгор(?:ы|ный|ного|ным|ам|ами)\b|вид\w* на гор(?!од)\w*",
    "beach_walk": r"(?:недалеко|близко|рядом) (?:с |от |до )?(?:моря|морем|пляжа)|(?:идти |дойти |ходить |добраться )?до (?:моря|пляжа)|пешком (?:к морю|до моря)|(?:дорог\w*|маршрут\w*|путь) (?:к морю|к пляжу|до пляжа)|к (?:морю|пляжу)(?:\s+\w+){0,8}\s+пешком|у (?:моря|пляжа)|близк\w* пляж|пляж\w* (?:за|в|не дальше|\d)",
    "pets": r"\bсобак\w*|\bкошк\w*|\bкот(?:ом|енк\w*)\b|\bпитом\w*|\bлабрадор\w*|\bживотн\w*",
    "family_nearby": r"\bдет(?:и|ьми|ей)\b|\bребен(?:ок|ка|ку|ком|ке|очек|очком)\b|\bмалыш\w*|\bколяск\w*|\bплощадк\w*",
    "connectivity": r"(?:хорош\w*|быстр\w*|стабильн\w*|нормальн\w*)\s+(?:интернет\w*|связ\w*|wi[ -]?fi)|\bинтернет\w*|\bwi[ -]?fi\b|\bудаленк\w*|\b(?:удаленн?\w*|дистанционн\w*)\s+работ\w*|\bработ(?:а|ы|у|ать|аю|аем)\b|для работы|\bсвязь\b|\bвидеозвон\w*|\bвидеоконференц\w*|\bзум\b|\bсозвон\w*",
    "soundproofing": r"\b(?:шумо|звуко)изоляц\w*|двойн\w* (?:ок\w*|стеклопакет\w*)|\bблэкаут\w*|(?:плотн\w*|светонепроницаем\w*) штор\w*|штор\w*[^,;.]{0,30}затемня\w*(?:\s+(?:комнат\w*|номер\w*))?|\bкондиционер\w*",
    "room_orientation": r"высок\w* этаж\w*|\bбалкон\w*|(?:запад|восточ|север|южн)\w* сторон\w*",
    "climate": r"тепл\w* (?:вода|воды|море)|\bкупаться\b|\bпогод\w*|\bвода\b[^,;.!?]*?(?:градус|°|\d\s*[cс]\b)|(?:вод\w*|море) (?:от|минимум|не ниже|не холоднее|выше)",
    'air_quality': r'\baqi\b|европейск\w* индекс качеств\w* воздух\w*',
    'nearby_photos': r'\b(?:фото(?:графи\w*)?|снимк\w*)\s+(?:окрестност\w*|вокруг|рядом|поблизости)',
    "car_access": r"\bпарковк\w*|на машин\w*|на автомобил\w*|на авто\b|\bподъезд\w*|\bэлектромобил\w*|\bэлектрокар\w*|\bзарядк\w*|\bзарядн\w* станц\w*",
    "night_pharmacy": r"\bаптек\w*",
    "nightlife": r"в гуще событий|ночн\w* жизн\w*|\bбар(?:ы|ов|ам|ами|ах)?\b|\bпаб(?:ы|ов)?\b|ночн\w* клуб\w*|\bклуб(?:ы|ов)\b|\bтусов\w*|\bдвиж\b|\bдвижух\w*|оживл[её]нн\w*\b(?!\s+(?:дорог|трасс|шоссе|магистрал|перекр[её]ст|проезж|улиц|движени))|\bкафе и ресторан\w*",
    'morning_sun': r'утренн\w* солнц\w*|солнц\w* (?:по утрам|утром)|солнечн\w* утр\w*',
    'evening_heat': r'без вечерн\w* жары|не жарк\w* (?:в номере )?вечером|вечерн\w* перегрев\w*',
    'railway_noise': r'шум\w* (?:от )?(?:железн\w* дорог\w*|поезд\w*)|без шум\w* поезд\w*|не слыш\w* поезд\w*',
    'beach_surface': r'песчан\w* пляж\w*|галечн\w* пляж\w*|пляж\w* (?:с )?(?:песк\w*|гальк\w*|грави\w*)',
    'accessible_route': r'доступн\w* (?:путь|маршрут) (?:к морю|до пляжа)|(?:к пляжу|к морю|до пляжа) на инвалидн\w* коляск\w*',
    'green_nearby': r'(?:\bпарк(?:а|у|ом|е|и|ов|ам|ами|ах)?\b|\bсквер\w*|\bзелен\w* зон\w*) (?:рядом|поблизости)|рядом (?:с )?(?:\bпарк(?:а|у|ом|е|и|ов|ам|ами|ах)?\b|\bсквер\w*)',
}
RULES['nightlife'] += r'|\b' + VENUE_NOUN + r'(?:\s*(?:,\s*(?:и\s*)?|и\s+)' + VENUE_NOUN + r')+'
RULES['nightlife'] += r'|\bнасыщенн\w*\s+(?:вечерн\w*|ночн\w*)\s+жизн\w*'
RULES['sea_view'] += r'|\bморе\s+из\s+номер\w*|\b(?:из\s+номер\w*\s+)?(?:долж\w*\s+быть\s+)?видно\s+море|\bморе\s+долж\w*\s+быть\s+видно'
RULES['morning_sun'] += r'|\bсолнц\w*\s+(?:по\s+утрам|утром)'
RULES['railway_noise'] += r'|\bжелезнодорожн\w*\s+шум(?:а|у|ом|е|ы|ов)?\b'
RULES['evening_heat'] += r'|\b(?:поменьше\s+вечерн\w*\s+солнц\w*|индекс\w*\s+вечерн\w*\s+экспозиц\w*)'
RULES['beach_surface'] += r'|\bпесок\b[^,;.!?]{0,30}\bна\s+пляж\w*'
RULES['nearby_photos'] += r'|\b(?:фотографи\w*|снимк\w*)\s+(?:его\s+)?(?:окрестност\w*|район\w*)'
# "подальше от баров" asks for a calm area: the same measurement, opposite threshold.
AWAY = r"(?:подальше|вдали|далеко|подальш\w*)\s+от\s+$"
CALM_VENUES_MAX = 10
OUTSIDE = r"москв\w*|саратов\w*|санкт[ -]?петербург\w*|петербург\w*|спб|казан[ьиь]\w*|анап\w*|геленджик\w*|краснодар\w*|стамбул\w*|париж\w*|дуба[ий]\w*|калининград\w*|екатеринбург\w*|новосибирск\w*|ялт\w*|туапсе|крым\w*|ростов\w*|нижн\w* новгород\w*|самар\w*|твер[ьиь]\w*|минск\w*|батум\w*|красн\w* полян\w*|воронеж\w*|волгоград\w*|омск\w*|томск\w*|перм[ьиь]\w*|уф[аеуы]\w*|челябинск\w*|красноярск\w*|тюмен[ьиь]\w*|иркутск\w*|хабаровск\w*|владивосток\w*|сургут\w*|сочи-парк|тбилиси|ереван\w*|ташкент\w*|белград\w*|антал[ьи]\w*|бишкек\w*|алмат[ыи]|астан\w*|псков\w*|суздал\w*|ярославл\w*|костром\w*|владимир\w*|иванов\w*|смоленск\w*|брянск\w*|ор[её]л\w*|курск\w*|белгород\w*|рязан\w*|тул[аеуы]\w*|тамбов\w*|липецк\w*|пенза|пензе|ижевск\w*|киров\w*|чебоксар\w*|саранск\w*|йошкар\w*|ульяновск\w*|оренбург\w*|барнаул\w*|кемеров\w*|новокузнецк\w*|абакан\w*|чита|чите|улан-удэ|якутск\w*|магадан\w*|мурманск\w*|архангельск\w*|петрозаводск\w*|вологд\w*|сыктывкар\w*|грозн\w*|махачкал\w*|владикавказ\w*|ставропол\w*|пятигорск\w*|кисловодск\w*|ессентук\w*|нальчик\w*|майкоп\w*"
SOFT = r"желатель\w*|хорошо бы|хотелось бы|буду рад\w*|по возможности|необязатель\w*|не обязател\w*|не\s*принципиал\w*|(?:будет\s+)?(?:приятн\w*\s+)?(?:плюсом|бонусом|бонус|дополнени\w*)"
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
FIELD_LABELS.update({('nightlife', 'bars_500m'): ('Бары и клубы в 500 м', 'заведений'),
                     ('beach_walk', 'distance_m'): ('До пляжа', 'м'),
                     ('night_pharmacy', 'minutes'): ('Ночная аптека', 'мин пешком')})
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
    if re.match(r'\s+не входит\w*\s+в\s+(?:мои\s+)?пожелани\w*|\s+(?:(?:рядом|поблизости)\s+)?(?:может|могут)\s+(?:и\s+)?не\s+быть', after):
        return False, False, True
    if re.search(r'пожелани\w*\s+к\s*$', before) and re.search(r'\bнет\b', after):
        return False, False, True
    if re.search(r"\bне\s*(?:нуж\w*|важ\w*|интерес\w*|треб\w*|ищ\w*)\s*(?:мне\s*)?(?:вид\w*\s*(?:на\s*)?)?$", before) or re.match(r"\s*(?:(?:мне|меня|нам|нас)\s+)?не\s*(?:важ\w*|нуж\w*|интерес\w*|треб\w*|ищ\w*)", after):
        return False, False, True
    markers = [(m.start(), True) for m in re.finditer(SOFT, before)] + [(m.start(), False) for m in re.finditer(HARD, before)]
    soft = max(markers, default=(-1, False))[1]
    if re.match(r"\s*(?:можно\s+)?(?:необязатель\w*|не обязател\w*|желатель\w*|по возможности|не\s*принципиал\w*|(?:будет\s+)?(?:приятн\w*\s+)?(?:плюсом|бонусом))", after):
        soft = True
    if re.match(r'\s*хотелось бы\s*[,;]?\s*(?:но\s+)?не обязател\w*', text[end:min(len(text), end+70)]):
        soft = True
    numeric_postfix = (r'\s*(?:(?:не менее|не ниже|не выше|не более|не дальше|не ближе|хотя бы|минимум|максимум|от|до|в|за)\s*)?'
                       r'\d+(?:[.,]\d+)?\s*(?:мбит(?:/с)?|mbps|мб/с|гбит(?:/с)?|gbps|минут\w*|час\w*|км|метр\w*|м\b|дб|градус\w*|°\s*[cс]?)'
                       r'\s*(?:(?:в секунду|пешком)\s*)?(?:желатель\w*|необязатель\w*|не обязател\w*|по возможности|не принципиал\w*)')
    if re.match(numeric_postfix, after):
        soft = True
    interval_postfix = (r'\s*(?:от\s*)?\d+(?:[.,]\d+)?\s*(?:до|по|[-–—])\s*\d+(?:[.,]\d+)?\s*'
                        r'(?:мбит(?:/с)?|mbps|мб/с|гбит(?:/с)?|gbps|минут\w*|час\w*|км|метр\w*|м\b|дб|градус\w*|°\s*[cс]?)'
                        r'\s*(?:желатель\w*|необязатель\w*|не обязател\w*|по возможности|не принципиал\w*)')
    if re.match(interval_postfix, after):
        soft = True
    if re.search(SOFT, text[start:end]):
        soft = True
    postfix = re.match(r'\s*[-–—]?\s*(?:(?:из\s+окна|по\s+утрам|утром|в\s+номере|для\s+прогулок)\s+)?(?:(?:был\w*\s+бы|это)\s+)?(?:' + SOFT + ')', after)
    if postfix:
        soft = True
    if re.search(r"(?:^|[,;])\s*можно(?: бы)?\s*$", before) and not re.search(r"\b(?:собак|кошк|котом|питом)\w*", text[start:end]):
        soft = True
    negation = re.search(r'(?P<words>(?:(?:\bбез|\bне)\s+)+)(?:вид\w*\s*(?:на\s*)?)?$', before[-60:])
    negative = bool(negation and len(re.findall(r'\b(?:не|без)\b', negation['words'])) % 2)
    if re.search(r'\bне\s+(?:хочу|хотим|нуж\w*)\s+(?:\w+\s+){0,2}без\s*(?:вид\w*\s*(?:на\s*)?)?$', before):
        negative = False
    if negative and re.match(r'[^,;.!?]{0,40}\b(?:не хочу|не хотим|не рассматрива\w*)', after):
        negative = False
    # A qualifier such as "не обязательно" is a preference, not negation.
    if re.search(r"не обязател\w*\s*$", before):
        negative = False
    return soft, negative, False


def _measurement_unit(field: str) -> str | None:
    distance = r'(?:километр\w*|км\b|метр\w*|м\b)'
    units = {
        'minutes': r'(?:мин(?:ут\w*)?|час\w*)',
        'water_c': r'(?:°\s*[cс]?(?!\s*[fф])|градус\w*|[cс]\b)',
        'download_mbps': r'(?:мбит(?:/с)?|mbps|мб/с|мегабит\w*|мегабайт(?:/с)?|гбит(?:/с)?|gbps|гигабит\w*)',
        'european_aqi': r'(?:aqi|единиц\w*)', 'night_db_max': r'дб',
        'visible_minutes': r'мин(?:ут\w*)?\b',
        'parking_distance_m': distance, 'playground_distance_m': distance,
        'distance_m': distance, 'ev_distance_m': distance,
        'venues_500m': r'(?:заведени\w*|' + VENUE_NOUN + ')',
        'bars_500m': r'(?:бар|паб|клуб)(?:а|у|ом|е|ы|ов|ам|ами|ах)?\b',
    }
    unit = units.get(field)
    if not unit and field not in {'available', 'windows', 'blackout', 'balcony', 'air_conditioning'}:
        unit = r'(?:час\w*|метр\w*|м\b|%|балл\w*|люкс\w*|км\b)'
    return unit


def _convert_measurement(number: str, unit: str, field: str) -> float:
    value = float(number.replace(',', '.'))
    if field == 'minutes' and unit.startswith('час'):
        value *= 60
    if field == 'download_mbps':
        if unit.startswith(('мегабайт', 'мб/с')):
            value *= 8
        elif unit.startswith(('гбит', 'gbps', 'гигабит')):
            value *= 1000
    if (field.endswith('_distance_m') or field == 'distance_m') and unit.startswith(('км', 'километр')):
        value *= 1000
    return value


def _numeric(text: str, start: int, end: int, field: str, default_op: str, default_value):
    left, right = clause_bounds(text, start, end)
    offset = max(left, start - 65)
    window = text[offset:min(right, end + 90)]
    unit = _measurement_unit(field)
    if not unit:
        return default_op, default_value
    interval = _numeric_range(text, start, end, field)
    if interval:
        return interval[0] if default_op == 'gte' else interval[1]
    operators = r'не менее|не меньше|не ниже|не холоднее|не ближе|хотя бы|минимум|больше|выше|от|≥|>=|не более|не дальше|не длиннее|не далее|не дольше|не выше|не больше|не быстрее|максимум|менее|меньше|ниже|до|≤|<='
    pattern = r'(?<![-\w.,])(?P<op>' + operators + r')?\s*(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>' + unit + ')'
    candidates = list(re.finditer(pattern, window))
    # Colloquial inversion «минут 5», «метров 300» names an approximate amount;
    # in «минут 5-10» the upper end is the limit.
    inverted = {'minutes': r'мин\b|минут\w*', 'visible_minutes': r'мин\b|минут\w*'}.get(
        field, r'метр\w*|километр\w*|км\b' if unit.startswith(r'(?:километр') else None)
    if inverted:
        candidates += list(re.finditer(r'(?<![\d.,])(?<![\d.,]\s)(?P<op>)(?P<unit>' + inverted + r')\s+(?:\d+(?:[.,]\d+)?\s*(?:[-–—]|до)\s*)?(?P<num>\d+(?:[.,]\d+)?)'
                                       r'(?![\d.,]|\s*(?:мин|час|метр|м\b|км|километр|%|°|градус|мбит|дб))', window))
    if field == 'european_aqi':
        candidates += list(re.finditer(r'(?P<unit>aqi)\s*(?:в районе\s+)?(?:нуж\w*\s+)?(?P<op>' + operators + r')?\s*(?P<num>\d+(?:[.,]\d+)?)', window))
    if field == 'exposure_index':
        candidates += list(re.finditer(r'(?P<unit>индекс\w*|экспозиц\w*)\s+(?:вечерн\w*\s+)?(?:солнечн\w*\s+)?(?:экспозиц\w*\s+)?(?:(?:нуж\w*|значени\w*)\s+)?(?P<op>' + operators + r')?\s*(?P<num>\d+(?:[.,]\d+)?)', window))

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
    op = 'gte' if found['op'] in {'не менее', 'не меньше', 'не ниже', 'не холоднее', 'не ближе', 'хотя бы', 'минимум', 'больше', 'выше', 'от', '≥', '>='} else default_op
    if found['op'] in {'не более', 'не дальше', 'не далее', 'не длиннее', 'не дольше', 'не выше', 'не больше', 'не быстрее', 'максимум', 'менее', 'меньше', 'ниже', 'до', '≤', '<='}:
        op = 'lte'
    return op, _convert_measurement(found['num'], found['unit'], field)


def _numeric_range(text: str, start: int, end: int, field: str) -> list[tuple[str, float]]:
    """Read an explicit inclusive interval without inventing an absent endpoint."""
    unit = _measurement_unit(field)
    if not unit:
        return []
    left, right = clause_bounds(text, start, end)
    pattern = (r'(?<![-\w.,])(?:от\s*)?(?P<low>\d+(?:[.,]\d+)?)\s*(?P<low_unit>' + unit + r')?\s*'
               r'(?:до|по|[-–—])\s*(?P<high>\d+(?:[.,]\d+)?)\s*(?P<unit>' + unit + ')')
    candidates = list(re.finditer(pattern, text[left:right]))
    if not candidates:
        return []

    def gap(match):
        return max(0, start-(left+match.end()), left+match.start()-end)

    # Do not attach another named POI's interval to this condition.
    anchors = [m for m in re.finditer(r'\b(?:аптек\w*|пляж\w*|мор[еяю]|морем|парковк\w*|площадк\w*|зарядк\w*)\b', text[left:right])
               if not (left+m.start() < end and left+m.end() > start)]
    candidates = [m for m in candidates if gap(m) <= 65 and not any(
        max(0, anchor.start()-m.end(), m.start()-anchor.end()) < gap(m) for anchor in anchors)]
    if not candidates:
        return []
    found = min(candidates, key=gap)
    return [('gte', _convert_measurement(found['low'], found['low_unit'] or found['unit'], field)),
            ('lte', _convert_measurement(found['high'], found['unit'], field))]


def _unsupported_measurement(text: str, match, cid: str) -> tuple[str, str] | None:
    unsupported = {
        'car_access': r'мин(?:ут\w*)?|час\w*|секунд\w*|шаг\w*|остановк\w*|руб\w*|₽|мест\w*',
        'family_nearby': r'мин(?:ут\w*)?|час\w*|секунд\w*|шаг\w*',
        'beach_walk': r'шаг\w*|остановк\w*|секунд\w*',
        'night_pharmacy': r'шаг\w*|остановк\w*|секунд\w*',
        'connectivity': r'ггц\b|ghz\b|кбит(?:/с)?|kbps\b',
        'climate': r'°\s*[fф]\b|фаренгейт\w*',
    }.get(cid)
    if not unsupported:
        return None
    left, right = clause_bounds(text, match.start(), match.end())
    candidates = list(re.finditer(r'\d+(?:[.,]\d+)?\s*(?:' + unsupported + ')', text[left:right]))
    if cid == 'climate' and re.search(r'фаренгейт\w*', text[left:right]):
        candidates += list(re.finditer(r'\d+(?:[.,]\d+)?\s*градус\w*\s+по\s+фаренгейт\w*', text[left:right]))
    anchors = [m for m in re.finditer(r'\b(?:аптек\w*|пляж\w*|мор[еяю]|морем|парковк\w*|площадк\w*|зарядк\w*)\b', text[left:right])
               if not (left+m.start() < match.end() and left+m.end() > match.start())]
    for candidate in candidates:
        gap = max(0, match.start()-(left+candidate.end()), left+candidate.start()-match.end())
        if gap > 50 or any(max(0, anchor.start()-candidate.end(), candidate.start()-anchor.end()) < gap for anchor in anchors):
            continue
        fragment = match[0] + ': ' + candidate[0]
        if cid in {'car_access', 'family_nearby'}:
            message = 'Эта единица для расстояния не поддерживается. Укажите метры или километры; время в расстояние не переводится.'
        elif cid in {'beach_walk', 'night_pharmacy'}:
            message = 'Укажите минуты, метры или километры. Шаги и остановки в расстояние не переводятся.'
        elif cid == 'connectivity':
            message = 'Эта единица связи пока не учитывается. Укажите скорость в Мбит/с.'
        else:
            message = 'Сейчас принимаем температуру воды в °C. Условие в другой температурной шкале не применяется.'
        return fragment, message
    return None


def _connectivity_followups(text: str, matches: list) -> list:
    """An adjacent typed speed belongs to the last unambiguous network anchor."""
    speed = re.compile(r'(?<![-\d.,])\d+(?:[.,]\d+)?\s*' + _measurement_unit('download_mbps') + r'(?![\w/])')
    foreign = '|'.join(pattern for cid, pattern in RULES.items() if cid != 'connectivity')
    foreign += r'|\b(?:аптек\w*|пляж\w*|парковк\w*|площадк\w*|цена|стоимост\w*|вес\w*|этаж\w*)\b'
    foreign += r'|\bномер\w*\s*(?:№\s*)?\d'
    result = []
    for anchor in matches:
        if not valid_intent_match('connectivity', text, anchor) or any(_importance(text, anchor.start(), anchor.end())[1:]):
            continue
        _, boundary = clause_bounds(text, anchor.start(), anchor.end())
        if boundary >= len(text) or re.search(foreign, text[anchor.end():boundary]):
            continue
        if text[boundary] in ',;':
            following = boundary+1
        elif re.match(r'(?:но|а)\b', text[boundary:]):
            following = boundary
        else:
            continue
        connector = re.match(r'\s*(?:(?:но|а|и|при этом)\s+)?', text[following:])
        start = following+connector.end()
        right = clause_bounds(text, start, start)[1]
        clause = text[start:right]
        if re.search(foreign, clause) or re.search(r'\b(?:без|не\s+(?:хочу|хотим|нуж\w*|треб\w*))\b', clause):
            continue
        result.extend(speed.finditer(text, start, right))
    return result


def _measurement_clauses(text: str, matches: list, cid: str) -> list:
    """Attach a directly following, noun-free measurement clarification."""
    if cid == 'connectivity':
        return [*matches, *_connectivity_followups(text, matches)]
    if cid not in {'beach_walk', 'car_access', 'night_pharmacy', 'family_nearby', 'connectivity', 'climate', 'air_quality', 'green_nearby', 'nightlife'}:
        return matches
    result = list(matches)
    operator = r'(?:не\s+(?:больше|дальше|дольше|длиннее|более|менее|ниже|выше)|максимум|минимум|хотя бы)'
    prefix = r'\s*(?:(?:он\s+должен\s+находиться|путь\s+должен\s+быть)\s+)?(?:расстояни\w*\s+(?:' + operator + r'\s*)?|' + operator + r'\s*)'
    units = r'(?:мин(?:ут\w*)?\b|час\w*|метр\w*|м\b|километр\w*|км\b|мбит(?:/с)?|mbps|градус\w*|°\s*[cс]?|заведени\w*)'
    for match in matches:
        _, boundary = clause_bounds(text, match.start(), match.end())
        if boundary >= len(text) or text[boundary] not in ',;:':
            continue
        continuation = re.match(prefix + r'\d+(?:[.,]\d+)?\s*' + units, text[boundary+1:])
        if not continuation:
            continue
        end = boundary + 1 + continuation.end()
        next_boundary = clause_bounds(text, end, end)[1]
        if re.search(r'\b(?:аптек\w*|пляж\w*|мор[еяю]|морем|парковк\w*|площадк\w*|зарядк\w*|интернет\w*|связь|вода)\b', text[boundary+1:next_boundary]):
            continue
        extended = re.compile(r'[\s\S]{' + str(end-match.start()) + '}').match(text, match.start())
        if extended:
            result.append(extended)
    return result


def _contextual_nouns(cid: str, text: str) -> list:
    """Recognize bounded property nouns only in their declared activity domain."""
    if cid == 'green_nearby':
        nouns = r'\b(?:парк(?:а|у|ом|е|и|ов|ам|ами|ах)?|сад(?:а|у|ом|е|ы|ов|ам|ами|ах)?|сквер(?:а|у|ом|е|ы|ов|ам|ами|ах)?)\b'
        return [m for m in re.finditer(nouns, text) if re.search(
            r'\b(?:нуж\w*|хочу|гуля\w*|прогул\w*|метр\w*|километр\w*|км|м|не дальше)\b',
            text[max(0, m.start()-55):min(len(text), m.end()+120)])]
    if cid == 'beach_walk':
        return [m for m in re.finditer(r'\b(?:к морю|к пляжу)\b', text) if re.search(
            r'\b(?:прогул\w*|пешком|идти|ходить|пройти|потратить|путь|маршрут\w*)\b',
            text[max(0, m.start()-60):min(len(text), m.end()+70)])]
    if cid == 'accessible_route':
        return [m for m in re.finditer(r'\b(?:маршрут\w*|путь)\b[^;.!?]{0,70}\b(?:пляж\w*|морю)\b', text)
                if re.search(r'инвалидн\w*\s+коляск\w*|проезд\s+коляск\w*|тег\w*\s+доступност\w*',
                             text[max(0, m.start()-100):min(len(text), m.end()+80)])]
    return []


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
    if cid == 'connectivity' and criterion.field == 'wifi_reported':
        fallback = 'Сообщено наличие Wi-Fi'
    if criterion.op == 'eq' and criterion.value is False:
        fallback = {
            ('sea_view', 'available'): 'Без вида на море',
            ('sunset_view', 'available'): 'Без вида на закат',
            ('mountain_view', 'available'): 'Без вида на горы',
            ('quiet_night', 'available'): 'Не тихо ночью',
            ('pets', 'available'): 'Без животных',
            ('car_access', 'available'): 'Без парковки поблизости',
            ('car_access', 'ev_available'): 'Без зарядки для электромобиля поблизости',
            ('connectivity', 'available'): 'Без интернета',
            ('night_pharmacy', 'available'): 'Без ночной аптеки поблизости',
            ('room_orientation', 'balcony'): 'Без балкона',
            ('soundproofing', 'air_conditioning'): 'Без кондиционера',
            ('soundproofing', 'blackout'): 'Без блэкаута',
            ('soundproofing', 'available'): 'Без шумоизоляции',
        }.get((cid, criterion.field), 'Отсутствует: ' + fallback)
    if (cid, criterion.field) in FIELD_LABELS or unit and criterion.op in {"lte", "gte"}:
        # A spec name may carry its default threshold in parentheses; the chip shows the actual one.
        name, unit = FIELD_LABELS.get((cid, criterion.field), (re.sub(r"\s*\([^)]*\)$", "", fallback), unit))
        sign = {"lte": "≤", "gte": "≥", "eq": "="}.get(criterion.op, criterion.op)
        threshold = f"{criterion.value:g}".replace(".", ",") if isinstance(criterion.value, int | float) else str(criterion.value)
        fallback = f"{name} {sign} {threshold} {unit}"
    flags = [label for active, label in ((criterion.no_stairs, "без лестниц"), (criterion.no_crossings, "без переходов")) if active]
    return ", ".join([fallback, *flags])


def _wifi_presence_only(text: str) -> list[re.Match]:
    """Only an explicit presence request/waiver relaxes inferred Mbps, never a number."""
    wifi = list(re.finditer(r'\bwi[ -]?fi\b', text))
    if not wifi or re.search(r'\d+(?:[.,]\d+)?\s*' + _measurement_unit('download_mbps'), text):
        return []
    waiver = (r'\b(?:(?:измеренн\w*|гарантированн\w*|высок\w*)\s+)?скорост\w*\s+'
              r'(?:wi[ -]?fi\s+)?(?:мне\s+)?не\s+(?:обязательн\w*|важн\w*|принципиальн\w*|нужн\w*)\b|'
              r'\bбез\s+требован\w*\s+к\s+(?:измеренн\w*\s+)?скорост\w*\b')
    presence = (r'\b(?:только|просто|достаточно)\s+(?:наличи\w*\s+)?wi[ -]?fi\b|'
                r'\bналичи\w*\s+wi[ -]?fi\b|\bчтобы\s+был\s+wi[ -]?fi\b')
    matches = [match for match in re.finditer(waiver, text) if any(
        abs(match.start()-anchor.start()) <= 200 and not re.search(
            r'(?<!\d)\.(?!\d)|[;!?\n]', text[min(match.end(), anchor.end()):max(match.start(), anchor.start())])
        for anchor in wifi)]
    return matches or list(re.finditer(presence, text))


def _numeric_mentions(text: str, spec: CharacteristicSpec, matches: list,
                      template: Criterion, warnings: list[str]) -> list[tuple[bool, Criterion]]:
    """Retain explicit limits from every mention, with that mention's priority."""
    fields = {
        'quiet_night': [('night_db_max', 'lte')],
        'beach_walk': [('minutes', 'lte'), ('distance_m', 'lte')],
        'night_pharmacy': [('minutes', 'lte'), ('distance_m', 'lte')],
        'connectivity': [('download_mbps', 'gte')],
        'climate': [('water_c', 'gte')],
        'air_quality': [('european_aqi', 'lte')],
        'morning_sun': [('visible_minutes', 'gte')],
        'evening_heat': [('exposure_index', 'lte')],
        'railway_noise': [('night_db_max', 'lte')],
        'green_nearby': [('distance_m', 'lte')],
    }.get(spec.id, [])
    result = []
    for atom in matches:
        soft, negative, skip = _importance(text, atom.start(), atom.end())
        if spec.id == 'connectivity' and re.fullmatch(r'\d+(?:[.,]\d+)?\s*' + _measurement_unit('download_mbps'), atom[0]):
            left, right = clause_bounds(text, atom.start(), atom.end())
            if not re.search(SOFT + '|' + HARD, text[left:right]):
                previous = max((m for m in matches if m.end() <= atom.start() and not re.fullmatch(
                    r'\d+(?:[.,]\d+)?\s*' + _measurement_unit('download_mbps'), m[0])), key=lambda m: m.end(), default=None)
                if previous is not None:
                    soft = _importance(text, previous.start(), previous.end())[0]
        if skip or negative:
            continue
        atom_fields = fields
        if spec.id == 'car_access':
            if re.search(r'\bпарковк\w*', atom[0]):
                atom_fields = [('parking_distance_m', 'lte')]
            elif re.search(r'\bзарядк\w*|\bзарядн\w*\s+станц\w*', atom[0]):
                atom_fields = [('ev_distance_m', 'lte')]
        elif spec.id == 'family_nearby' and re.search(r'\bплощадк\w*', atom[0]):
            atom_fields = [('playground_distance_m', 'lte')]
        for field, default_op in atom_fields:
            limits = _numeric_range(text, atom.start(), atom.end(), field)
            if not limits:
                op, value = _numeric(text, atom.start(), atom.end(), field, default_op, None)
                limits = [(op, value)] if value is not None else []
            elif limits[0][1] > limits[1][1]:
                warnings.append('Минимальный порог выше максимального: «' + spec.name_ru + '». Уточните границы интервала.')
            for op, value in limits:
                criterion = template.model_copy(update={'field': field, 'op': op, 'value': value})
                criterion.label = _criterion_label(criterion, spec.name_ru, spec.search.unit)
                result.append((soft, criterion))
    return result


def _fallback(request: SearchRequest, catalog: list[CharacteristicSpec]) -> ParsedQuery:
    source_text = normalized(request.text)
    text = search_context_text(source_text)
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
    enormous = re.search(r'\d{16,}', text)
    if enormous:
        parsed.unmapped.append('Числовой порог: ' + enormous[0][:40])
        parsed.clarifications = [*parsed.clarifications, 'Числовой порог слишком велик для поиска. Уточните значение и единицу измерения.'][:2]
        return parsed
    facade_match = next(((side, match) for side, pattern in SIDE_PATTERNS.items()
                         for match in re.finditer(pattern + r'\w*(?:\s+(?:сторон\w*|фасад\w*))?', text)
                         if facade_is_explicit(text, match)), None)
    facade_importance = _importance(text, facade_match[1].start(), facade_match[1].end()) if facade_match else (False, False, False)
    if facade_match and not any(facade_importance):
        parsed.facade = facade_match[0]
    floor = re.search(r"(?:со?|от|не ниже)\s*(-?\d{1,3})(?:\s*[-–]?(?:го|ого))?\s*(?:этажа?|эт\b)|\b(\d{1,3})\s*\+\s*этаж", text)
    reversed_floor = re.search(r'этаж\w*\s*(?:не ниже|от)\s*(-?\d{1,3})\b', text)
    exact_floor = re.search(r"на\s+(-?\d{1,3})(?:[- ]?(?:м|ом))?\s*этаже", text)
    if not exact_floor:
        exact_floor = re.search(r'(?<![\d.,-])\b(\d{1,3})\s*этаж\b', text)
    floor_range = re.search(r"(?:со?|от)\s*(-?\d{1,3})\s*(?:по|до|[-–—])\s*(-?\d{1,3})(?:\s*[-–]?(?:го|ого))?\s*этаж", text)
    floor_ceiling = re.search(r"(?:не выше|до)\s*(-?\d{1,3})(?:\s*[-–]?(?:го|ого))?\s*этаж", text)
    floor_match = floor_range or floor or reversed_floor or exact_floor or floor_ceiling
    requested_floor = int(floor_range[1] if floor_range else (floor[1] or floor[2]) if floor else reversed_floor[1] if reversed_floor else exact_floor[1]) if floor_range or floor or reversed_floor or exact_floor else None
    requested_max = int(floor_range[2]) if floor_range else int(exact_floor[1]) if exact_floor else int(floor_ceiling[1]) if floor_ceiling else None
    explicit_floor_number = re.search(r'(?:на|с|от|до|ниже|выше)\s*(-?\d+(?:[.,]\d+)?)(?:[- ]?(?:м|ом|го|ого))?\s*этаж', text)
    invalid_floor = any(value is not None and not 1 <= value <= 100 for value in (requested_floor, requested_max))
    invalid_floor = invalid_floor or bool(explicit_floor_number and (
        re.search(r'[.,]', explicit_floor_number[1]) or not 1 <= float(explicit_floor_number[1]) <= 100))
    if invalid_floor:
        parsed.unmapped.append('Этаж: ' + (explicit_floor_number[1] if explicit_floor_number else str(requested_floor or requested_max)))
        warnings.append('Для поиска укажите целый этаж от 1 до 100. Нулевой, подземный или дробный этаж нужно уточнить у отеля.')
        requested_floor, requested_max = None, None
    if requested_floor and requested_max and requested_floor > requested_max:
        warnings.append("Минимальный этаж не может быть выше максимального.")
        requested_floor, requested_max = None, None
    floor_importance = _importance(text, floor_match.start(), floor_match.end()) if floor_match else (False, False, False)
    if not any(floor_importance):
        parsed.min_floor = requested_floor or None
    hard_floor_max = None if any(floor_importance) else requested_max
    if facade_importance[1] or floor_importance[1]:
        parsed.unmapped.append('Исключение стороны или этажа номера')
        warnings.append('Исключение стороны или этажа пока не применяется. Укажите допустимую сторону или диапазон этажей.')
    weight = re.search(r"(?:собак\w*|питом\w*|лабрадор\w*|кошк\w*|кот(?:ом|енк\w*))[^,;.]{0,35}?(-?\d+(?:[.,]\d+)?)\s*(?:кг|килограмм\w*)", text)
    invalid_pet_weight = bool(weight and not 0 <= float(weight[1].replace(',', '.')) <= 100)
    if weight and not invalid_pet_weight:
        parsed.pet_weight_kg = float(weight[1].replace(",", "."))
    elif invalid_pet_weight:
        parsed.unmapped.append('Вес питомца: ' + weight[1] + ' кг')
        warnings.append('Вес питомца выходит за поддерживаемый диапазон 0–100 кг. Уточните вес; ограничение не применяется.')
    consumed = []
    positions = {}
    nearby = _nearby_intents(text)
    wifi_presence = _wifi_presence_only(text)
    for spec in catalog:
        if spec.status != "active":
            continue
        matches = [m for phrase in [spec.name_ru, *spec.synonyms] for m in re.finditer(_phrase_pattern(phrase), text)]
        if spec.id in RULES:
            matches += list(re.finditer(RULES[spec.id], text))
        matches += _contextual_nouns(spec.id, text)
        if spec.id == 'car_access':
            matches += list(re.finditer(CAR_CHARGING, text))
        if spec.id in nearby:
            matches.append(nearby[spec.id][0])
            consumed.extend((m.start(), m.end()) for m in nearby[spec.id][1])
        if spec.id == "room_orientation" and (facade_match or floor_match):
            matches += [facade_match[1]] if facade_match else [floor_match]
        if spec.id == 'room_orientation':
            # An exclusion cannot be encoded as a positive global room scope.
            matches = [m for m in matches if not (
                facade_importance[1] and facade_match and m.start() <= facade_match[1].start() < m.end()
                or (floor_importance[1] or invalid_floor) and floor_match and m.start() <= floor_match.start() < m.end())]
        if spec.id == 'pets' and invalid_pet_weight:
            continue
        matches = _measurement_clauses(text, matches, spec.id)
        matches = [m for m in matches if valid_intent_match(spec.id, text, m)]
        supported_matches = []
        for candidate in matches:
            unsupported = _unsupported_measurement(text, candidate, spec.id)
            if unsupported:
                fragment, message = unsupported
                parsed.unmapped.append(fragment)
                warnings.append(message)
                consumed.append((candidate.start(), candidate.end()))
            else:
                supported_matches.append(candidate)
        matches = supported_matches
        if not matches:
            continue
        consumed.extend((m.start(), m.end()) for m in matches)
        matches = [m for m in matches if not any(other.start() <= m.start() and other.end() >= m.end()
                   and other.end()-other.start() > m.end()-m.start() for other in matches)]
        matches = [m for m in matches if not _importance(text, m.start(), m.end())[2]]
        if not matches:
            continue
        # Longest phrase is most useful for negation ("без вида на море").
        match = min(matches, key=lambda m: (m.start(), -(m.end()-m.start())))
        if spec.id == 'car_access':
            # A vehicle is only an implicit charging/parking preference. An
            # explicit parking or charging statement carries its own polarity.
            explicit_car = [m for m in matches if re.search(r'\bпарковк\w*|\bзарядк\w*', m[0])]
            if explicit_car:
                parking = [m for m in explicit_car if re.search(r'\bпарковк\w*', m[0])]
                parking_vehicle = [m for m in matches if re.search(CAR_PARKING, m[0])]
                match = min(parking or parking_vehicle or explicit_car, key=lambda m: (m.start(), -(m.end()-m.start())))
        if spec.id == 'climate':
            # Swimming is activity context. A separately stated temperature
            # property owns its priority instead of creating an extra hard20.
            properties = [m for m in matches if re.search(r'\b(?:вод\w*|мор[еяю]|морем)\b', m[0])]
            if properties:
                match = min(properties, key=lambda m: (m.start(), -(m.end()-m.start())))
        positions[spec.id] = match.start()
        soft, negative, skip = _importance(text, match.start(), match.end())
        if skip:
            continue
        field, op, value = spec.search.field, spec.search.default_op, spec.search.default_value
        numeric_match = match
        explicit_measurement = False
        if spec.id == 'quiet_night':
            noise_op, noise_value = _numeric(text, match.start(), match.end(), 'night_db_max', 'lte', None)
            if noise_value is not None:
                field, op, value = 'night_db_max', noise_op, noise_value
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
                if measured is None and spec.id in {'beach_walk', 'night_pharmacy'}:
                    numeric_field = 'minutes'
                    measured = next(((m, comparison) for m in target_matches
                                     if (comparison := _numeric(text, m.start(), m.end(), numeric_field, 'lte', None))[1] is not None), None)
                if measured:
                    numeric_match, (op, value) = measured
                    field = numeric_field
                    explicit_measurement = True
        if explicit_measurement and _importance(text, numeric_match.start(), numeric_match.end())[0] != soft:
            # A limit in another clause can be optional even when the first
            # generic mention was mandatory (and vice versa).
            field, op, value = spec.search.field, spec.search.default_op, spec.search.default_value
            numeric_match, explicit_measurement = match, False
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
        if spec.id == 'connectivity' and wifi_presence and not negative and (
                _numeric(text, numeric_match.start(), numeric_match.end(), 'download_mbps', 'gte', None)[1] is None):
            field, op, value = 'wifi_reported', 'eq', True
            consumed.extend((m.start(), m.end()) for m in wifi_presence)
        if negative and isinstance(value, bool):
            value = not value
        elif negative and spec.id in {'car_access', 'connectivity', 'night_pharmacy'}:
            field, op, value = ('ev_available' if field == 'ev_distance_m' else 'available'), 'eq', False
        away = spec.id == "nightlife" and (negative or re.search(AWAY, text[max(0, match.start()-25):match.start()]))
        if away:
            op, value = "lte", CALM_VENUES_MAX
            if negative and re.search(r'\b(?:бар\w*|паб\w*|клуб\w*)\b', match[0]):
                field, op, value = 'bars_500m', 'eq', 0
        if spec.id == "nightlife":
            # "рядом бары" / "кафе рядом": the proximity is part of the 500 m density measure.
            consumed.extend((near.start(), near.end()) for near in re.finditer(r"\bрядом\b|\bподальше\b|\bвдали\b", text)
                            if any(min(abs(near.start() - m.end()), abs(m.start() - near.end())) <= 25
                                   and not re.search(r"[,;.!?]", text[min(near.end(), m.end()):max(near.start(), m.start())]) for m in matches))
        if spec.id == "soundproofing":
            matched_text = match[0]
            if re.search(r"двойн\w* (?:ок|стеклопакет)", matched_text):
                field, value = "windows", "double"
            elif re.search(r"блэкаут|(?:плотн\w*|светонепроницаем\w*) штор|штор\w*.*затемня", matched_text):
                predicate_negative = bool(re.search(r'\bне\s+(?:(?:долж\w*|полностью)\s+){0,2}затемня\w*', matched_text))
                field, value = "blackout", not (negative ^ predicate_negative)
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
        unsupported_negative = negative and not isinstance(value, bool) and spec.id not in {'nightlife', 'evening_heat', 'railway_noise'}
        if unsupported_negative:
            parsed.unmapped.append(match[0])
            warnings.append('Это отрицание пока не применяется к поиску. Укажите допустимое значение или числовой порог.')
        scope = None
        if spec.scope != "hotel" and (parsed.facade or parsed.min_floor or hard_floor_max):
            scope = Scope(facade=parsed.facade, floor_min=parsed.min_floor,
                          floor_max=hard_floor_max)
        if spec.id == "room_orientation" and soft and (facade_match or floor_match):
            scope = Scope(facade=facade_match[0] if facade_match else parsed.facade,
                          floor_min=requested_floor, floor_max=requested_max)
        criterion = Criterion(characteristic_id=spec.id, label=spec.name_ru, field=field, op=op, value=value, scope=scope)
        if not unsupported_negative:
            (parsed.nice if soft else parsed.must).append(criterion)
        if nearby_preference:
            nearby_soft, nearby_criterion = nearby_preference
            nearby_criterion.label = _criterion_label(nearby_criterion, spec.name_ru)
            (parsed.nice if nearby_soft else parsed.must).append(nearby_criterion)
        if spec.id == "soundproofing":
            for extra_field, pattern, extra_value in [("windows", r"двойн\w* (?:ок\w*|стеклопакет\w*)", "double"), ("blackout", r"блэкаут\w*|(?:плотн\w*|светонепроницаем\w*) штор\w*|штор\w*[^,;.]{0,30}затемня\w*", True), ("air_conditioning", r"кондиционер\w*", True)]:
                for extra in re.finditer(pattern, text):
                    extra_soft, extra_negative, extra_skip = _importance(text, extra.start(), extra.end())
                    if extra_field == 'blackout' and re.search(r'\bне\s+(?:(?:долж\w*|полностью)\s+){0,2}затемня\w*', extra[0]):
                        extra_negative = not extra_negative
                    if not extra_skip:
                        if extra_negative and not isinstance(extra_value, bool):
                            if extra[0] not in parsed.unmapped:
                                parsed.unmapped.append(extra[0])
                                warnings.append('Это отрицание пока не применяется к поиску. Укажите допустимое значение или числовой порог.')
                            continue
                        extra_criterion = Criterion(characteristic_id=spec.id, label=spec.name_ru, field=extra_field,
                            value=not extra_negative if isinstance(extra_value, bool) else extra_value, scope=scope)
                        extra_criterion.label = _criterion_label(extra_criterion, spec.name_ru)
                        (parsed.nice if extra_soft else parsed.must).append(extra_criterion)
        if spec.id == 'room_orientation':
            for extra in re.finditer(r'\bбалкон\w*', text):
                extra_soft, extra_negative, extra_skip = _importance(text, extra.start(), extra.end())
                if not extra_skip:
                    extra_scope = Scope(facade=parsed.facade, floor_min=parsed.min_floor, floor_max=hard_floor_max) if parsed.facade or parsed.min_floor or hard_floor_max else None
                    extra_criterion = Criterion(characteristic_id=spec.id, label=spec.name_ru, field='balcony', value=not extra_negative, scope=extra_scope)
                    extra_criterion.label = _criterion_label(extra_criterion, spec.name_ru)
                    (parsed.nice if extra_soft else parsed.must).append(extra_criterion)
        if spec.id == "car_access" and field != "ev_distance_m":
            charging = re.search(r'\bзарядк\w*(?:\s+для\s+(?:электромобил|электрокар)\w*)?|\bзарядн\w*\s+станц\w*', text) or re.search(CAR_CHARGING, text)
            if charging:
                charging_soft, charging_negative, charging_skip = _importance(text, charging.start(), charging.end())
                if not charging_skip:
                    charging_op, charging_value = _numeric(text, charging.start(), charging.end(), 'ev_distance_m', 'lte', EV_CHARGING_NEAR_M)
                    extra = Criterion(characteristic_id=spec.id, label=spec.name_ru,
                                      field='ev_available' if charging_negative else 'ev_distance_m',
                                      op='eq' if charging_negative else charging_op,
                                      value=False if charging_negative else charging_value)
                    extra.label = _criterion_label(extra, spec.name_ru)
                    (parsed.nice if charging_soft else parsed.must).append(extra)
        if spec.id == "beach_walk":
            for flag, extra_field, label, pattern, extra_value in [
                ("no_stairs", "has_steps", "Без лестниц до пляжа", r"без (?:лестниц\w*|ступен\w*)|\bколяск\w*|лестниц\w*[^,;.!?]{0,70}(?:не должно|не будет|нет)|не (?:поднима\w*|спуска\w*)(?:\s+\w+){0,3}\s+(?:лестниц\w*|ступен\w*)", False),
                ("no_crossings", "crossings", "Без переходов до пляжа", r"(?:без (?:дорожн\w*\s+)?(?:переход\w*|пересечени\w*)(?:\s+(?:и\s+)?переход\w*)?|без (?:лестниц\w*|ступен\w*)\s+и\s+(?:дорожн\w*\s+)?переход\w*)(?:\s+(?:через\s+)?(?:трасс\w*|дорог\w*|шоссе|железн\w*\s+дорог\w*))?|переход\w*\s+через\s+дорог\w*\s+исключен\w*|не (?:переход\w*|пересек\w*)(?:\s+\w+){0,3}\s+(?:дорог\w*|трасс\w*|шоссе|улиц\w*)|переход\w*[^,;.!?]{0,60}(?:не должно|не будет|нет)", []),
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
        if field == spec.search.field and isinstance(spec.search.default_value, bool) and isinstance(value, bool):
            # Repeated boolean mentions may carry a later hard constraint or an
            # explicit contradiction. Neither may disappear behind the first hit.
            for atom in matches:
                atom_soft, atom_negative, atom_skip = _importance(text, atom.start(), atom.end())
                if atom_skip:
                    continue
                atom_value = not spec.search.default_value if atom_negative else spec.search.default_value
                extra = criterion.model_copy(update={'value': atom_value})
                extra.label = _criterion_label(extra, spec.name_ru, spec.search.unit)
                (parsed.nice if atom_soft else parsed.must).append(extra)
        if spec.id in {'car_access', 'connectivity', 'night_pharmacy'}:
            for atom in matches:
                if spec.id == 'car_access' and not re.search(r'\bпарковк\w*|\bзарядк\w*', atom[0]):
                    continue
                if spec.id == 'connectivity' and not re.search(r'интернет|wi[ -]?fi|связь', atom[0]):
                    continue
                atom_soft, atom_negative, atom_skip = _importance(text, atom.start(), atom.end())
                if atom_skip or atom_negative == negative:
                    continue
                atom_field = 'ev_distance_m' if spec.id == 'car_access' and re.search(r'зарядк\w*', atom[0]) else spec.search.field
                if atom_negative:
                    atom_field, atom_op, atom_value = ('ev_available' if atom_field == 'ev_distance_m' else 'available'), 'eq', False
                else:
                    atom_default = EV_CHARGING_NEAR_M if atom_field == 'ev_distance_m' else spec.search.default_value
                    atom_op, atom_value = _numeric(text, atom.start(), atom.end(), atom_field, spec.search.default_op, atom_default)
                extra = Criterion(characteristic_id=spec.id, label=spec.name_ru, field=atom_field, op=atom_op, value=atom_value)
                extra.label = _criterion_label(extra, spec.name_ru, spec.search.unit)
                (parsed.nice if atom_soft else parsed.must).append(extra)
        measurements = _numeric_mentions(text, spec, matches, criterion, warnings)
        for atom_soft, extra in measurements:
            (parsed.nice if atom_soft else parsed.must).append(extra)
        own_measurement = _numeric(text, match.start(), match.end(), criterion.field, criterion.op, None)[1] if criterion.field else None
        same_priority = [extra for atom_soft, extra in measurements if atom_soft == soft]
        if spec.id == 'connectivity' and soft and own_measurement is None and any(not priority for priority, _ in measurements):
            # A later hard speed supersedes the inferred soft default; a later
            # soft speed must not remove the original hard work requirement.
            parsed.nice[:] = [c for c in parsed.nice if c is not criterion]
        replaces_default = any(extra.field == criterion.field or
                               spec.id in {'beach_walk', 'night_pharmacy'} and {extra.field, criterion.field} <= {'minutes', 'distance_m'} or
                               criterion.field == 'available' and spec.id in {'quiet_night', 'family_nearby', 'night_pharmacy', 'morning_sun'}
                               for extra in same_priority)
        if own_measurement is None and replaces_default:
            group = parsed.nice if soft else parsed.must
            group[:] = [c for c in group if not (c.characteristic_id == criterion.characteristic_id and c.field == criterion.field
                        and c.op == criterion.op and c.value == criterion.value and c.scope == criterion.scope)]
            # A numeric mention can equal the default; retain that explicit bound.
            group.extend(same_priority)
        hard = [c for c in parsed.must if c.characteristic_id == spec.id]
        for boolean_field in {c.field for c in hard if isinstance(c.value, bool)}:
            if len({c.value for c in hard if c.field == boolean_field and isinstance(c.value, bool)}) > 1:
                warnings.append('Обязательные условия противоречат друг другу: «' + spec.name_ru + '». Уточните, нужно ли это условие.')
        presence_fields = {'available': {'parking_distance_m'} if spec.id == 'car_access' else {'download_mbps'} if spec.id == 'connectivity' else set(),
                           'ev_available': {'ev_distance_m'}}
        if any(c.field == absence and c.value is False for absence, positives in presence_fields.items()
               if any(other.field in positives for other in hard) for c in hard):
            warnings.append('Обязательные условия противоречат друг другу: «' + spec.name_ru + '». Уточните, нужно ли это условие.')
    residual = list(source_text)
    for start, end in consumed:
        residual[start:end] = " " * (end-start)
    for marker in re.finditer(SOFT, text):
        residual[marker.start():marker.end()] = ' ' * (marker.end()-marker.start())
    remaining = "".join(residual)
    remaining = re.sub(r'оставить\s+(?:ее|его|машин\w*|автомобил\w*)?\s*рядом', ' ', remaining)
    # These clauses describe who is staying home, not hotel restrictions.
    remaining = re.sub(r'\bне\s+с\s+(?:собак|кошк|питом)\w*|\bдет\w*\s+не\s+берем|\bс\s+дет\w*\s+не\s+поедем', ' ', remaining)
    remaining = re.sub(r"\b(?:" + OUTSIDE + "|" + "|".join(MONTHS) + r")\w*\b", " ", remaining)
    ignored = r"сочи|адлер\w*|сириус\w*|имеретин\w*|центр\w*|номер\w*|отел\w*|гостиниц\w*|вид\w*|сторон\w*|этаж\w*|окн\w*|ноч\w*|днем|летом|утром|пешком|мин\w*|кг|градус\w*|мбит|мб|с|в|во|на|за|к|от|до|и|но|а|не|без|для|из|по|у|со|же|бы|или|нам|мне|мы|я|нас|над|около|это|его|ее|их|чтобы|где|ищу|найти|хочу|хотим|жить|живем|нуж\w*|обязател\w*|важ\w*|обязатель\w*|желатель\w*|необязатель\w*|возможности|хорошо|только|более|менее|ниже|выше|дальше|больше|дольше|максимум|сентябр\w*|пожалуйста|спасибо|ехать|едем|еду|отдых\w*|поездк\w*|пару|двоих|пары|романти\w*|годовщин\w*|муж\w*|жена|спит|сплю|чутко|чуткий|требуется|разрешен\w*|принима\w*|допуска\w*|принципиаль\w*|размер\w*|вес\w*|был\w*|будет|есть|быть|метр\w*|море|моря|пляж\w*|вод\w*|тепл\w*"
    ignored += r'|найди\w*|главн\w*|отпуск\w*|отдохн\w*|смотреть|любоват\w*|именно|остальн\w*|неважн\w*|принципиал\w*|доступност\w*|пеш\w*|ходьб\w*|поблизости|плюс\w*|можно|хотел\w*|путешеств\w*|один|одна|двое|двумя|хотя|минимум|секунд\w*|скорост\w*|хорош\w*|быстр\w*|стабильн\w*|нормальн\w*|стар\w*|весит|весом|оставить|устраива\w*|меня|мегабит\w*|mbps|aqi|километр\w*|даты|числ\w*|сво\w*|полностью|очень|берем|поедем|интерес\w*|кажд\w*|фасад\w*|вечером|пределах|радиусе|час\w*|гулять|спокойно|штор\w*|кило\w*|станц\w*|зарядн\w*|исключен\w*|ближе'
    # Selection words carry no amenity, price, measurement or priority meaning.
    ignored += r'|подбери(?:те)?|подобрать|покажи(?:те)?|показать|предложи(?:те)?|порекомендуй(?:те)?|вариант(?:а|у|ом|е|ы|ов|ам|ами|ах)?'
    if re.search(r"вид\w* на город\w*|городск\w* (?:вид|пейзаж)\w*|панорам\w* город\w*", text):
        # No city-view characteristic yet: say so instead of guessing «горы».
        warnings.append("Вид на город пока не рассчитываем: доступны вид на море, на горы и закат из окна.")
        remaining = re.sub(r"\bгород\w*|\bгородск\w*|\bпанорам\w*", " ", remaining)
    tokens = [word for word in re.findall(r"[а-яa-z][а-яa-z-]*", remaining) if len(word) > 2 and not re.fullmatch(ignored, word)]
    if tokens:
        parsed.unmapped.append(" ".join(dict.fromkeys(tokens)))
    parsed.unmapped = list(dict.fromkeys(parsed.unmapped))
    parsed.clarifications = list(dict.fromkeys([*parsed.clarifications, *warnings]))[:2]
    if not parsed.must and not parsed.nice and not outside:
        parsed.clarifications.append("Что важнее для поездки: тишина, вид, дорога к морю или другие условия?")
    if re.search(r"высок\w* этаж", text) and not parsed.min_floor:
        parsed.clarifications.append("Какой минимальный этаж вам подходит?")
    if parsed.unmapped and not outside and len(parsed.clarifications) < 2:
        suggestions = catalogue_suggestions(text, parsed.unmapped, catalog,
                                            {c.characteristic_id for c in [*parsed.must, *parsed.nice]})
        hint = (' Возможно, вы имели в виду ' + ' или '.join('«' + name + '»' for name in suggestions) + '.') if suggestions else ''
        parsed.clarifications.append("Не удалось сопоставить с каталогом: " + parsed.unmapped[-1] + "." + hint + " Уточните, что здесь обязательно.")
    parsed.clarifications = list(dict.fromkeys(parsed.clarifications))[:2]
    for group in (parsed.must, parsed.nice):
        group[:] = [c for c in group if not (c.characteristic_id == 'room_orientation' and c.field == 'available'
                    and c.value is True and any(other is not c and other.scope == c.scope and (
                        other.characteristic_id == c.characteristic_id and other.field == 'balcony'
                        or c.scope is not None and other.characteristic_id != c.characteristic_id
                        and next((s.scope != 'hotel' for s in catalog if s.id == other.characteristic_id), False))
                        for other in group))]
        unique = {}
        for criterion in group:
            key = json.dumps(criterion.model_dump(exclude={'label', 'weight'}, mode='json'), sort_keys=True)
            unique.setdefault(key, criterion)
        group[:] = unique.values()
        tightest = {}
        others = []
        for criterion in group:
            if criterion.op not in {'lte', 'gte'} or not isinstance(criterion.value, int | float) or isinstance(criterion.value, bool):
                others.append(criterion)
                continue
            key = json.dumps(criterion.model_dump(exclude={'label', 'weight', 'value'}, mode='json'), sort_keys=True)
            previous = tightest.get(key)
            if previous is None or (criterion.value < previous.value if criterion.op == 'lte' else criterion.value > previous.value):
                tightest[key] = criterion
        group[:] = [*others, *tightest.values()]
    hard_meanings = {json.dumps(c.model_dump(exclude={'label', 'weight'}, mode='json'), sort_keys=True) for c in parsed.must}
    parsed.nice[:] = [c for c in parsed.nice if json.dumps(c.model_dump(exclude={'label', 'weight'}, mode='json'), sort_keys=True) not in hard_meanings]
    parsed.nice[:] = [c for c in parsed.nice if not any(
        c.characteristic_id == hard.characteristic_id and c.field == hard.field and c.scope == hard.scope
        and c.op == hard.op and c.op in {'lte', 'gte'}
        and isinstance(c.value, int | float) and not isinstance(c.value, bool)
        and isinstance(hard.value, int | float) and not isinstance(hard.value, bool)
        and (hard.value <= c.value if c.op == 'lte' else hard.value >= c.value)
        for hard in parsed.must)]
    parsed.must.sort(key=lambda c: positions[c.characteristic_id])
    parsed.nice.sort(key=lambda c: positions[c.characteristic_id])
    criteria = [*parsed.must, *parsed.nice]
    parsed.persona = next((name for cid, name in [
        ('family_nearby', 'семья с детьми'), ('pets', 'путешествие с питомцем'),
        ('connectivity', 'удалённая работа'), ('quiet_night', 'чуткий сон')]
        if any(c.characteristic_id == cid and c.value is not False for c in criteria)),
        'романтическая поездка' if re.search(r'годовщин|романти|медов\w* месяц', text) else None)
    if not any(c.characteristic_id == 'pets' and c.value is True for c in criteria):
        parsed.pet_weight_kg = None
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
