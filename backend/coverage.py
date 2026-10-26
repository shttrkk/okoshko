"""Which primary search fields have current data in the demo region.

An open-data characteristic with no known value for any hotel (for example,
no Ookla speed tiles over the region) is reported as "no_data" and is not used
as a search condition; it is not shown as hundreds of per-hotel unknowns.
Hotel-answerable characteristics stay searchable: their data comes from hotels.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime

from backend.quality import current_value

NO_DATA_NOTE = "нет данных в регионе"


def known_search_value(value, spec) -> bool:
    """A typed primary field is evidence; sibling flags and metadata are not."""
    parts = spec.search.field.split(".")
    if any(part in {"metadata", "model", "month", "period", "limitations"} for part in parts):
        return False
    schema = spec.value_schema
    if isinstance(value, dict):
        for part in parts:
            value = value.get(part) if isinstance(value, dict) else None
            schema = schema.get("properties", {}).get(part, {})
    elif spec.search.field != "available" and (isinstance(value, bool) or not isinstance(value, int | float)):
        return False
    if isinstance(value, bool):
        kinds = {"boolean"}
    elif isinstance(value, int | float) and math.isfinite(value):
        kinds = {"number", "integer"} if float(value).is_integer() else {"number"}
    elif isinstance(value, str) and value.strip():
        kinds = {"string"}
    else:
        return False
    expected = {"numeric": {"number"}, "boolean": {"boolean"}, "composite": {"boolean"}}.get(spec.search.kind)
    if expected is None:
        default = spec.search.default_value
        expected = {"boolean"} if isinstance(default, bool) else {"number"} if isinstance(default, int | float) else {"string"}
    declared = schema.get("type")
    declared = {declared} if isinstance(declared, str) else set(declared or [])
    return bool(kinds & expected) and (not declared or bool(kinds & declared)) and (
        "enum" not in schema or value in schema["enum"])


def region_coverage(fact_rows: list[dict], specs: list, hotel_count: int, *, at: datetime | None = None) -> dict[str, dict]:
    at = at or datetime.now(UTC)
    known: dict[str, set[str]] = {}
    specs_by_id = {spec.id: spec for spec in specs}
    for row in fact_rows:
        spec = specs_by_id.get(row["characteristic_id"])
        if spec and known_search_value(row.get("value"), spec) and current_value({**row, "value": True}, at):
            known.setdefault(row["characteristic_id"], set()).add(row["hotel_id"])
    result = {}
    for spec in specs:
        count = len(known.get(spec.id, ()))
        if count:
            status = "available" if count == hotel_count else "partial"
        else:
            status = "awaiting_hotels" if spec.hotelier_answerable else "no_data"
        result[spec.id] = {"known_hotels": count, "hotels": hotel_count, "status": status,
                           "note": NO_DATA_NOTE if status == "no_data" else None}
    return result
