"""Deterministic Russian templates that turn a short description into a spec.

Offline the studio compiles text without an LLM by matching a small grammar
over the tool registry, never by executing or generating code:

* «<место> рядом», «<место> в N минутах», «<место> ближе/до/не дальше N м»,
  «<место> в пределах N км» -> poi_nearby(category, near_m) and a numeric
  search on distance_m;
* «рядом нет <места>», «без <места> ближе N м», «подальше от <места>» -> the same
  poi_nearby, searched as "nothing of this category within N m";
* «искупаться до завтрака: пляж до N минут, вода от T°» -> derive over the
  walking route and the water temperature.

Unsupported text raises a ValueError that lists the supported patterns.
"""
from __future__ import annotations

import re

from backend.contracts import CharacteristicSpec, SearchSpec, ToolCall
from backend.tools.registry import POI_CATEGORIES

WALK_METRES_PER_MINUTE = 80  # ≈ 4.8 km/h; straight-line proxy, not a routed time
NEARBY_DEFAULT_M = 500
# Ordered: more specific phrases first ("детская площадка" before "площадка").
CATEGORY_WORDS = [
    (r"детск\w*\s+площадк\w*|площадк\w*\s+для\s+дет\w*", "playground"),
    (r"супермаркет\w*|продукт\w*|продуктов\w*\s+магазин\w*|магазин\w*", "supermarket"),
    (r"аптек\w*", "pharmacy"),
    (r"кафе|кофейн\w*", "cafe"),
    (r"ресторан\w*", "restaurant"),
    (r"\bбар(?:ы|а|ов)?\b|\bпаб\w*|ночн\w*\s+клуб\w*", "bar"),
    (r"остановк\w*|автобус\w*|общественн\w*\s+транспорт\w*", "bus_stop"),
    (r"\bпарк(?:а|ом|и|ов)?\b|сквер\w*", "park"),
    (r"банкомат\w*", "atm"),
    (r"спортзал\w*|фитнес\w*|тренаж[её]рн\w*\s+зал\w*", "fitness"),
    (r"\bрын(?:ок|ка|ке|ком)\b", "marketplace"),
    (r"набережн\w*|променад\w*|пешеходн\w*\s+улиц\w*", "promenade"),
    (r"\bпляж\w*", "beach"),
    (r"прокат\w*|аренд\w*\s+(?:велосипед|самокат|авто|машин)\w*", "rental"),
    (r"заправк\w*|\bазс\b", "fuel"),
]
# Asking for the absence of a place: «рядом нет баров», «без ночных клубов», «подальше от рынка».
NEGATION = re.compile(r"(?<!\w)(?:нет|без|никаких|подальше\s+от|вдали\s+от|не\s+рядом|не\s+должно\s+быть|чтобы\s+не\s+было)(?!\w)")
SUPPORTED = ("Без LLM студия понимает шаблоны «<место> рядом», «<место> в N минутах», «<место> ближе N м» "
             "(также «до N м», «не дальше N м», «в пределах N км») и «рядом нет <места>», где место — супермаркет или продукты, аптека, кафе, "
             "ресторан, бар, остановка, парк, банкомат, детская площадка, спортзал, рынок, пляж, набережная, прокат, "
             "заправка; а также «искупаться до завтрака: пляж до N минут, вода от T°». Другое описание задайте YAML "
             "или подключите LLM.")


def _category(text: str) -> tuple[str, re.Match] | None:
    for pattern, category in CATEGORY_WORDS:
        match = re.search(pattern, text)
        if match:
            return category, match
    return None


def _threshold(text: str) -> tuple[int, str] | None:
    """Metres and a human phrase; minutes are converted at a stated walking pace."""
    minutes = re.search(r"(\d{1,2})\s*(?:-|–)?\s*(?:х\s+)?мин\w*", text)
    if minutes:
        value = int(minutes[1])
        return value * WALK_METRES_PER_MINUTE, f"в {value} мин пешком, ≈ {value * WALK_METRES_PER_MINUTE} м по прямой"
    kilometres = re.search(r"(\d+(?:[.,]\d+)?)\s*км\b", text)
    if kilometres:
        metres = round(float(kilometres[1].replace(",", ".")) * 1000)
        return metres, f"≤ {metres} м"
    metres = re.search(r"(?:ближе|до|не дальше|в пределах|в)\s*(\d{2,4})\s*(?:м\b|метр\w*)", text)
    if metres:
        return int(metres[1]), f"≤ {int(metres[1])} м"
    if re.search(r"\bрядом\b|\bнедалеко\b|\bпоблизости\b|\bблизко\b|\bв шаговой доступности\b", text):
        return NEARBY_DEFAULT_M, f"≤ {NEARBY_DEFAULT_M} м"
    return None


def proximity_template(text: str) -> CharacteristicSpec | None:
    low = text.casefold().replace("ё", "е")
    found, threshold = _category(low), _threshold(low)
    # The negation must govern the place itself («нет (шумных) баров»), not
    # another word: «до пляжа без лестниц» still asks for a beach.
    negated = bool(found) and any(m.end() <= found[1].start() and len(low[m.end():found[1].start()].split()) <= 1
                                  for m in NEGATION.finditer(low))
    if negated and found and not threshold:
        threshold = NEARBY_DEFAULT_M, f"≤ {NEARBY_DEFAULT_M} м"
    if not found or not threshold:
        return None
    category, word = found
    metres, phrase = threshold
    if not 50 <= metres <= 5000:
        raise ValueError("Порог расстояния должен быть от 50 м до 5 км. " + SUPPORTED)
    label = POI_CATEGORIES[category][0]
    if negated:
        return absence_spec(text, low, category, label, word[0].strip(), metres)
    name = f"{label} рядом ({phrase})"
    note = (" Минуты переведены в расстояние по прямой при 80 м/мин; это не время пешего маршрута." if "мин" in phrase else "")
    return CharacteristicSpec(
        id=f"near_{category}_{metres}m", name_ru=name[:80], category="Рядом",
        description=f"{text.strip()} — ближайший объект OSM категории «{label}» не дальше {metres} м по прямой.{note}",
        synonyms=list(dict.fromkeys([low.strip(), f"{label.casefold()} рядом", word[0].strip()])),
        value_schema={"type": "object", "properties": {"available": {"type": ["boolean", "null"]},
                                                       "distance_m": {"type": ["number", "null"]}}, "additionalProperties": True},
        methods=[ToolCall(tool="poi_nearby", params={"category": category, "near_m": metres},
                          rationale=f"Локальный снимок OSM: ближайший объект «{label}» и число объектов в {metres} м")],
        ttl_days=90, hotelier_answerable=False,
        search=SearchSpec(kind="numeric", field="distance_m", default_op="lte", default_value=metres, unit="м",
                          explain_ok=f"{label} в пределах {metres} м", explain_bad=f"Ближайший объект «{label}» дальше {metres} м"))


def absence_spec(text: str, low: str, category: str, label: str, word: str, metres: int) -> CharacteristicSpec:
    # Nothing mapped within the search radius stays unknown in poi_nearby, so
    # "absent" is claimed only when the nearest mapped object is farther away.
    return CharacteristicSpec(
        id=f"no_{category}_{metres}m", name_ru=f"Нет поблизости: {label.casefold()} (ближе {metres} м)"[:80], category="Рядом",
        description=f"{text.strip()} — ближайший объект OSM категории «{label}» дальше {metres} м по прямой.",
        synonyms=list(dict.fromkeys([low.strip(), f"рядом нет: {label.casefold()}", f"без {word}"])),
        value_schema={"type": "object", "properties": {"available": {"type": ["boolean", "null"]},
                                                       "distance_m": {"type": ["number", "null"]}}, "additionalProperties": True},
        methods=[ToolCall(tool="poi_nearby", params={"category": category, "near_m": metres},
                          rationale=f"Локальный снимок OSM: есть ли объект «{label}» ближе {metres} м")],
        ttl_days=90, hotelier_answerable=False,
        search=SearchSpec(kind="boolean", field="available", default_op="eq", default_value=False,
                          explain_ok=f"Ближе {metres} м нет объектов «{label}»",
                          explain_bad=f"«{label}» ближе {metres} м"))


def morning_swim_template(text: str) -> CharacteristicSpec | None:
    low = text.casefold().replace("ё", "е")
    if not any(word in low for word in ["искуп", "до завтрака", "утреннее купание", "поплавать"]):
        return None
    minutes = re.search(r"(?:≤|<=|до)\s*(\d{1,2})\s*мин", low)
    water = re.search(r"(?:≥|>=|от)\s*(\d{1,2})\s*°", low)
    expression = f"beach_walk.minutes <= {int(minutes[1]) if minutes else 10} and climate.water_c >= {int(water[1]) if water else 20}"
    return CharacteristicSpec(
        id="morning_swim", name_ru="Искупаться до завтрака", description=text,
        synonyms=["искупаться до завтрака", "утреннее купание", "поплавать утром"],
        value_schema={"type": "object", "properties": {"available": {"type": "boolean"}, "minutes": {"type": ["number", "null"]},
                      "water_c": {"type": ["number", "null"]}, "month": {"type": "integer"}}, "required": ["available"]},
        methods=[ToolCall(tool="derive", params={"expr": expression},
                          rationale="Объединить реальный пеший маршрут и архивную температуру воды для месяца поездки")],
        combine=expression, ttl_days=90,
        search=SearchSpec(explain_ok="Пляж рядом и вода достаточно тёплая по архивным данным",
                          explain_bad="Условие по времени до моря или температуре воды не выполнено"))


def compile_template(text: str) -> CharacteristicSpec | None:
    return morning_swim_template(text) or proximity_template(text)
