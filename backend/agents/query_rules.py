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
    'family_nearby': {'playground_distance_m', 'hospital_distance_m', 'crossings'},
    'car_access': {'parking_distance_m', 'ev_distance_m'},
    'climate': {'water_c', 'air_c', 'sunshine_hours', 'precipitation_mm'},
    'pets': {'max_weight_kg', 'fee_rub_night'},
    'air_quality': {'european_aqi', 'pm2_5'},
    'connectivity': {'download_mbps', 'upload_mbps', 'latency_ms'},
    'nightlife': {'venues_500m', 'bars_500m', 'nearest_bar_m', 'promenade_distance_m'},
}
BOOLEAN_FIELDS = {
    'soundproofing': {'available', 'blackout', 'air_conditioning'},
    'room_orientation': {'available', 'balcony'},
    'beach_walk': {'available', 'has_steps'},
    'car_access': {'available', 'ev_available', 'pedestrian_zone'},
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
    elif spec.id == 'soundproofing' and field == 'windows':
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
    """Move unsupported regional conditions out of ranking using authoritative data."""
    missing = [c for c in [*parsed.must, *parsed.nice]
               if coverage.get(c.characteristic_id, {}).get('status') == 'no_data']
    if missing:
        parsed.must = [c for c in parsed.must if c not in missing]
        parsed.nice = [c for c in parsed.nice if c not in missing]
        for criterion in missing:
            if criterion not in parsed.no_data:
                parsed.no_data.append(criterion)
        labels = ', '.join(dict.fromkeys('«' + criterion.label + '»' for criterion in missing))
        message = f'В демо-регионе нет данных: {labels}. Это условие не учитывается в выдаче.'
        parsed.clarifications = list(dict.fromkeys([message, *parsed.clarifications]))[:2]
    return parsed
