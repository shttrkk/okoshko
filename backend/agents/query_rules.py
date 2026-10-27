"""Query-plan safety and coverage projection, independent from hotel facts.

Providers may suggest interpretations. Only locally grounded conditions reach
ranking; coverage is supplied by the repository and never guessed from language.
"""
from __future__ import annotations

import json
import math

from backend.contracts import CharacteristicSpec, Criterion, ParsedQuery
from backend.llm.client import UnsafeOutput

NUMERIC_FIELDS = {
    'quiet_night': {'night_db_max'},
    'beach_walk': {'minutes', 'distance_m', 'return_minutes', 'ascent_m', 'return_ascent_m'},
    'night_pharmacy': {'minutes', 'distance_m'},
    'family_nearby': {'playground_distance_m', 'hospital_distance_m', 'crossings', 'walk_children_hospital.minutes', 'walk_promenade.minutes'},
    'car_access': {'parking_distance_m', 'ev_distance_m'},
    'climate': {'water_c', 'air_c', 'sunshine_hours', 'precipitation_mm'},
    'pets': {'max_weight_kg', 'fee_rub_night'},
    'air_quality': {'european_aqi', 'pm2_5'},
    'connectivity': {'download_mbps', 'upload_mbps', 'latency_ms'},
    'nightlife': {'venues_500m', 'bars_500m', 'nearest_bar_m', 'promenade_distance_m'},
    'airport_transfer': {'transfers', 'scheduled_minutes', 'car_distance_km'},
    'airport_noise': {'runway_distance_m', 'noise_db'}, 'mobile_masts': {'mapped_count'},
    'climate_days': {'sunny_days', 'rainy_days', 'water_c'},
    'hotel_parking': {'fee_rub_per_day'},
    'night_open': {'shop.minutes', 'food.minutes'},
    'dog_walk': {'dog_park.minutes', 'ordinary_park_fallback.minutes'},
    'veterinary_nearby': {'minutes', 'distance_m'}, 'pet_shop_nearby': {'minutes', 'distance_m'},
    'location_access': {'center_route.minutes', 'station_route.minutes', 'bus_stop_route.minutes'},
    'mobile_internet': {'download_mbps', 'upload_mbps', 'latency_ms'},
}
BOOLEAN_FIELDS = {
    'connectivity': {'available', 'wifi_reported'},
    'soundproofing': {'available', 'blackout', 'air_conditioning'},
    'room_orientation': {'available', 'balcony', 'courtyard'},
    'beach_walk': {'available', 'has_steps'},
    'car_access': {'available', 'ev_available', 'pedestrian_zone'},
    'night_open': {'available', 'shop.available', 'food.available'},
    'dog_walk': {'available', 'dog_park.available'},
    'airport_transfer': {'available', 'night_service'},
    'climate_days': {'available', 'wet_month_proxy'},
    'hotel_parking': {'private_parking', 'reservation_required', 'ev_charging'},
    'location_access': {'in_central_sochi', 'metro.available', 'center_route.available', 'station_route.available', 'bus_stop_route.available'},
    'family_nearby': {'available', 'walk_children_hospital.available', 'walk_playground.available', 'walk_hospital.available', 'walk_promenade.available'},
    'photo_features': {'available', 'balcony', 'terrace', 'pool'},
}


def _value_kind(value) -> str:
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, int | float):
        return 'number'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, list):
        return 'array'
    return 'unsupported'


def _valid_shape(criterion: Criterion, spec: CharacteristicSpec) -> bool:
    field = criterion.field or spec.search.field
    numeric = NUMERIC_FIELDS.get(spec.id, set())
    boolean = BOOLEAN_FIELDS.get(spec.id, {'available'})
    properties = spec.value_schema.get('properties', {})
    if field in numeric:
        kind = 'number'
    elif field in boolean:
        kind = 'boolean'
    elif spec.id == 'soundproofing' and field == 'windows' or spec.id == 'building_history' and field in {'explicit_building_date', 'architecture'}:
        kind = 'string'
    elif spec.id == 'beach_walk' and field == 'crossings':
        kind = 'array'
    elif field == spec.search.field:
        kind = _value_kind(spec.search.default_value)
    elif field in properties:
        types = properties[field].get('type', [])
        types = [types] if isinstance(types, str) else types
        kind = next((name for name in ('boolean', 'number', 'integer', 'string', 'array') if name in types), 'unsupported')
        kind = 'number' if kind == 'integer' else kind
    else:
        return False
    value = criterion.value
    if _value_kind(value) != kind:
        return False
    if kind == 'number':
        if not math.isfinite(value) or criterion.op not in {'eq', 'lte', 'gte'}:
            return False
    elif kind == 'boolean':
        if criterion.op != 'eq':
            return False
    elif kind == 'string':
        if criterion.op not in {'eq', 'contains'} or not value or len(value) > 120:
            return False
        enum = properties.get(field, {}).get('enum')
        if enum and value not in enum:
            return False
        if spec.id == 'soundproofing' and field == 'windows' and value not in {'single', 'double', 'triple'}:
            return False
    elif kind == 'array':
        if criterion.op not in {'eq', 'contains'} or len(value) > 50:
            return False
    else:
        return False
    return not (criterion.no_stairs or criterion.no_crossings) or spec.id == 'beach_walk'


def _meaning(criterion: Criterion, spec: CharacteristicSpec) -> str:
    """Labels and model-selected weights never change the meaning of a condition."""
    data = criterion.model_dump(exclude={'label', 'weight'}, mode='json')
    data['field'] = data['field'] or spec.search.field
    if isinstance(data['value'], float) and data['value'].is_integer():
        data['value'] = int(data['value'])
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def validate_query_plan(plan: ParsedQuery, baseline: ParsedQuery,
                        catalog: list[CharacteristicSpec]) -> ParsedQuery:
    """Validate before caching, then retain every locally grounded hard constraint.

    A generic catalogue schema often allows extra object properties. The explicit
    field/type/operator checks here therefore precede grounding against the local
    extractor. Unknown language remains unmapped instead of creating a threshold.
    """
    specs = {spec.id: spec for spec in catalog if spec.status == 'active'}
    local = {_meaning(c, specs[c.characteristic_id])
             for c in [*baseline.must, *baseline.nice, *baseline.no_data]
             if c.characteristic_id in specs}
    unknown = []
    for criterion in [*plan.must, *plan.nice, *plan.no_data]:
        spec = specs.get(criterion.characteristic_id)
        if spec is None:
            # Preserve a useful unknown label, without caching an executable ID.
            if criterion.label and len(criterion.label) <= 200:
                unknown.append(criterion.label)
            continue
        if not _valid_shape(criterion, spec) or _meaning(criterion, spec) not in local:
            raise UnsafeOutput('Query condition lacks a valid locally grounded meaning')
    result = baseline.model_copy(deep=True)
    result.unmapped = list(dict.fromkeys([*result.unmapped, *unknown]))
    return result


def apply_region_coverage(parsed: ParsedQuery, coverage: dict[str, dict]) -> ParsedQuery:
    """Project coverage without forgetting hard conditions or edited chip priority."""
    import json

    for room in parsed.rooms:
        # A newer editor may send an unsupported hard condition directly in
        # no_data_must. Keep it even when the redundant no_data list is omitted.
        unknown = [*room.no_data]
        unknown.extend(c for c in room.no_data_must if c not in unknown
                       and c not in room.must and c not in room.nice)
        local = ParsedQuery(text=parsed.text, must=room.must, nice=room.nice,
                            no_data=unknown, no_data_must=room.no_data_must,
                            clarifications=room.clarifications)
        apply_region_coverage(local, coverage)
        for name in ('must', 'nice', 'no_data', 'no_data_must', 'clarifications'):
            setattr(room, name, getattr(local, name))

    def meaning(criterion):
        data = criterion.model_dump(exclude={'label', 'weight'}, mode='json')
        data['field'] = data['field'] or coverage.get(criterion.characteristic_id, {}).get('search_field')
        return json.dumps(data, sort_keys=True, ensure_ascii=False)

    previous = {meaning(c) for c in parsed.no_data}
    hard = {meaning(c) for c in parsed.no_data_must} & previous
    # Explicit chip groups take precedence over an old no_data marker. A marker
    # whose chip was removed from no_data cannot resurrect the deleted condition.
    groups = [(c, True) for c in parsed.must] + [(c, False) for c in parsed.nice]
    explicit = {meaning(c) for c, _ in groups}
    groups.extend((c, meaning(c) in hard) for c in parsed.no_data if meaning(c) not in explicit)
    parsed.must, parsed.nice, parsed.no_data, parsed.no_data_must = [], [], [], []
    seen = set()
    for criterion, required in groups:
        key = meaning(criterion)
        if key in seen:
            continue
        seen.add(key)
        row = coverage.get(criterion.characteristic_id, {})
        field = criterion.field or row.get('search_field')
        field_coverage = row.get('fields', {}).get(field, row)
        status = field_coverage.get('status')
        partial_evidence = (criterion.characteristic_id == 'connectivity'
                            and field in {'download_mbps', 'upload_mbps', 'latency_ms'}
                            and field_coverage.get('partial_evidence') is True
                            and field_coverage.get('wifi_reported_count', 0) > 0)
        if not partial_evidence and (status == 'no_data' or status is None and key in previous):
            parsed.no_data.append(criterion)
            if required:
                parsed.no_data_must.append(criterion)
        else:
            (parsed.must if required else parsed.nice).append(criterion)
    parsed.clarifications = [c for c in parsed.clarifications if not c.startswith('В демо-регионе нет данных:')]
    if parsed.must or parsed.nice or parsed.no_data_must:
        parsed.clarifications = [c for c in parsed.clarifications if not c.startswith('Не нашли в запросе условий из каталога')]
    if parsed.no_data:
        labels = ', '.join(dict.fromkeys('«' + criterion.label + '»' for criterion in parsed.no_data))
        conclusion = ('Полное совпадение нельзя подтвердить: нет данных по обязательным условиям.'
                      if parsed.no_data_must else 'Эти желательные условия не учитываются в оценке.')
        message = f'В демо-регионе нет данных: {labels}. {conclusion}'
        parsed.clarifications = list(dict.fromkeys([message, *parsed.clarifications]))[:2]
    return parsed
