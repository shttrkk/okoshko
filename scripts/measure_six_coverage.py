"""Read-only presence of six core characteristics, not same-room satisfaction.

Reproduce against the local stand:
    docker compose exec -T app python < scripts/measure_six_coverage.py
The report separates all source objects from the current eligibility policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime

import psycopg

from backend.catalogue import searchable
from backend.quality import current_value
from backend.settings import DATA, DB_URL, ROOT

KEYS = ("sea_view", "sunset_view", "mountain_view", "quiet_night", "beach_walk", "night_pharmacy")


def measure(hotels: list[dict], facts: list[dict], month: int) -> dict:
    at = datetime.now(UTC)
    report = {
        "checked_at": at.isoformat(), "month": month, "characteristic_ids": list(KEYS),
        "method": "Current known computed/confirmed values, including true and false, any facade/floor; month absent or selected. NOT same-room satisfaction or field accuracy.",
        "confidence_semantics": "Declared heuristic confidence, uncalibrated; non_synthetic excludes mixed synthetic facts.",
        "input_sha256": {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
            "backend/quality.py", "backend/catalogue.py", "data/seed/catalogue_reviews.json")},
    }
    for population, ids in (("source_objects", {h["id"] for h in hotels}),
                            ("searchable", {h["id"] for h in hotels if searchable(h)})):
        values = {}
        for minimum in (0, .6):
            for non_synthetic in (False, True):
                found = defaultdict(set)
                for fact in facts:
                    if (fact["hotel_id"] not in ids or not current_value(fact, at)
                            or fact["confidence"] < minimum or non_synthetic and fact.get("synthetic")):
                        continue
                    selected_month = fact.get("scope", {}).get("month")
                    if selected_month is None and isinstance(fact.get("value"), dict):
                        selected_month = fact["value"].get("month")
                    if selected_month in (None, month):
                        found[fact["hotel_id"]].add(fact["characteristic_id"])
                key = f"confidence_gte_{minimum:g}" + ("_non_synthetic" if non_synthetic else "_including_synthetic")
                values[key] = sum(set(KEYS) <= found[hotel_id] for hotel_id in ids)
        report[population] = {"hotels": len(ids), "complete": values}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", type=int, choices=range(1, 13), default=9)
    args = parser.parse_args()
    if not all((DATA/"characteristics"/(key+".yaml")).is_file() for key in KEYS):
        raise ValueError("The six canonical characteristic specifications must be present")
    with psycopg.connect(DB_URL) as connection:
        hotels = [row[0] for row in connection.execute("SELECT payload FROM hotels")]
        facts = [row[0] for row in connection.execute(
            "SELECT payload-'evidence' FROM facts WHERE payload->>'characteristic_id'=ANY(%s)", (list(KEYS),))]
    print(json.dumps(measure(hotels, facts, args.month), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
