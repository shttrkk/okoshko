"""Display helpers: presentation never changes the parsed comparison or facts."""
from __future__ import annotations

import math
import re

from backend.contracts import CharacteristicSpec, Criterion

UNITS = {
    "minutes": "мин", "return_minutes": "мин", "visible_minutes": "мин",
    "download_mbps": "Мбит/с", "upload_mbps": "Мбит/с", "latency_ms": "мс",
    "water_c": "°C", "air_c": "°C", "sunshine_hours": "ч", "precipitation_mm": "мм",
    "european_aqi": "AQI", "pm2_5": "мкг/м³", "venues_500m": "заведений в 500 м",
    "bars_500m": "баров и клубов в 500 м", "max_weight_kg": "кг",
    "fee_rub_night": "₽/ночь", "index": "индекс 0–1", "exposure_index": "индекс 0–1",
    "crossings": "переходов",
}
DEFAULT_FIELDS = {"beach_walk": "minutes", "nightlife": "venues_500m", "connectivity": "download_mbps",
                  "climate": "water_c", "air_quality": "european_aqi", "car_access": "parking_distance_m",
                  "evening_heat": "exposure_index", "railway_noise": "night_db_max", "green_nearby": "distance_m"}
FIELD_LABELS = {
    ("beach_walk", "minutes"): "До пляжа", ("beach_walk", "return_minutes"): "Обратно с пляжа",
    ("beach_walk", "distance_m"): "До пляжа", ("beach_walk", "ascent_m"): "Подъём до пляжа",
    ("beach_walk", "return_ascent_m"): "Подъём обратно с пляжа",
    ("nightlife", "venues_500m"): "В гуще событий", ("nightlife", "bars_500m"): "Бары и клубы рядом",
    ("nightlife", "nearest_bar_m"): "До ближайшего бара", ("nightlife", "promenade_distance_m"): "До набережной",
    ("car_access", "parking_distance_m"): "Парковка", ("car_access", "ev_distance_m"): "Зарядка для электромобиля",
    ("family_nearby", "playground_distance_m"): "Детская площадка",
    ("family_nearby", "hospital_distance_m"): "До больницы", ("family_nearby", "crossings"): "Переходы",
    ("night_pharmacy", "minutes"): "Ночная аптека", ("night_pharmacy", "distance_m"): "Ночная аптека",
    ("pets", "max_weight_kg"): "Допустимый вес питомца", ("pets", "fee_rub_night"): "Доплата за питомца",
    ("connectivity", "download_mbps"): "Связь для работы", ("connectivity", "upload_mbps"): "Скорость отправки",
    ("connectivity", "latency_ms"): "Задержка связи", ("climate", "water_c"): "Температура воды",
    ("climate", "air_c"): "Температура воздуха", ("climate", "sunshine_hours"): "Солнечные часы",
    ("climate", "precipitation_mm"): "Осадки", ("air_quality", "european_aqi"): "Качество воздуха",
    ("air_quality", "pm2_5"): "Мелкие частицы в воздухе", ("quiet_night", "night_db_max"): "Шум ночью снаружи",
    ("railway_noise", "night_db_max"): "Шум поездов ночью", ("green_nearby", "distance_m"): "Парк или сад рядом",
    ("evening_heat", "exposure_index"): "Вечернее солнце",
}


def number(value) -> str:
    return f"{value:g}".replace(".", ",") if isinstance(value, int | float) else str(value)


def rounded_minutes(value: float) -> int:
    """Match Math.round for nonnegative minutes; a positive trip is at least 1."""
    return max(1 if value > 0 else 0, math.floor(value + .5))


def field_unit(field: str, unit: str | None = None) -> str | None:
    if unit:
        return unit
    if field in UNITS:
        return UNITS[field]
    if field.endswith("_m"):
        return "м"
    if "_db" in field:
        return "дБ"
    return None


def criterion_label(criterion: Criterion, spec: CharacteristicSpec | None = None) -> str:
    """Render the current threshold, even when an edited chip kept an old label."""
    if isinstance(criterion.value, bool) or not isinstance(criterion.value, int | float):
        return criterion.label
    field = criterion.field or (spec.search.field if spec else DEFAULT_FIELDS.get(criterion.characteristic_id, ""))
    fallback = (getattr(spec, "display_name", None) or spec.name_ru) if spec else criterion.label
    # An unknown/custom characteristic may still carry an obsolete comparison.
    fallback = re.split(r"\s*(?:≤|≥|<=|>=|=|<|>)\s*-?\d", fallback)[0].strip().rstrip("(").strip()
    name = FIELD_LABELS.get((criterion.characteristic_id, field), fallback)
    unit = field_unit(field, spec.search.unit if spec and field == spec.search.field else None)
    if field == "minutes" and criterion.characteristic_id in {"beach_walk", "night_pharmacy"}:
        unit = "мин пешком"
    sign = {"lte": "≤", "gte": "≥", "eq": "="}.get(criterion.op, criterion.op)
    label = f"{name} {sign} {number(criterion.value)}" + (f" {unit}" if unit else "")
    flags = [text for active, text in ((criterion.no_stairs, "без лестниц"), (criterion.no_crossings, "без переходов")) if active]
    return ", ".join([label, *flags])


def beach_name(value) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if name.casefold() in {"пляж", "beach", "ближайший пляж"}:
        return "ближайший пляж"
    if not name or re.search(r"\bosm\b|\b(?:osm-)?(?:way|node|relation)[-/ ]?\d+\b", name, re.I):
        return "ближайший пляж"
    return name


def guest_limitation(text: str) -> str | None:
    """Raw evidence keeps technical provenance; first-level caveats do not."""
    if re.search(r"simulator|симулятор|fixture|фикстур|demo[ -]?truth|synthetic|синтет|confidence", text, re.I):
        return None
    if "%" in text and re.search(r"над[её]жност|уверенност|точност|reliab", text, re.I):
        return None
    return text
