"""Which characteristics have any current data in the demo region.

An open-data characteristic with no known value for any hotel (for example,
no Ookla speed tiles over the region) is reported as "no_data" and is not used
as a search condition; it is not shown as hundreds of per-hotel unknowns.
Hotel-answerable characteristics stay searchable: their data comes from hotels.
"""
from __future__ import annotations

from datetime import UTC, datetime

from backend.quality import current_value

NO_DATA_NOTE = "нет данных в регионе"


def region_coverage(fact_rows: list[dict], specs: list, hotel_count: int, *, at: datetime | None = None) -> dict[str, dict]:
    at = at or datetime.now(UTC)
    known: dict[str, set[str]] = {}
    for row in fact_rows:
        if current_value(row, at):
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
