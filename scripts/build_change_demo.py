"""Precompute the shipped synthetic «новый 16-этажный дом» scenario (offline, ~10 s).

The new building is invented (geo/fixtures/new_building.json), but the line-of-
sight recomputation is real: the polygon enters the DSM and the geo engine
recomputes views for the hotel. Output:

* data/geo/<hotel>-change-<digest>.json/.npz — the recomputed geo artifact;
* data/seed/demo_change_event.json — synthetic claims and a labelled demo
  passport, applied once on first start by backend.agents.freshness.seed_change_event.

    python -m scripts.build_change_demo [--hotel-id osm-way-53284165]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.contracts import Claim
from backend.settings import DATA
from geo.changes import new_building_fixture
from geo.service import compute_hotel

DEFAULT_HOTEL = "osm-way-53284165"  # «Империя»: 6 floors, sea view from the 3rd floor on three facades


def build(hotel_id: str, data_dir: Path = DATA) -> dict:
    hotel = next(row for row in json.loads((data_dir / "seed" / "hotels.json").read_text()) if row["id"] == hotel_id)
    base = json.loads((data_dir / "geo" / (hotel_id + ".json")).read_text())
    fixture = new_building_fixture(hotel, base.get("geo", base))
    result = compute_hotel(hotel, data_dir, new_building=fixture)
    claims = [Claim.model_validate({**row, "synthetic": True}).model_dump(mode="json") for row in result["claims"]]
    before = {facade["code"]: facade.get("min_floor_sea") for facade in base.get("facades", [])}
    after = {facade["code"]: facade.get("min_floor_sea") for facade in result["geo"].get("facades", [])}
    document = {
        "schema_version": 1, "synthetic": True, "scenario": "new_building_fixture_v1", "hotel_id": hotel_id,
        "description": "Синтетический сценарий: вымышленный 16-этажный дом в 70 м перед фасадом с лучшим видом на море. "
                       "Пересчёт прямой видимости настоящий; стройки в реальности нет.",
        "fixture": fixture,
        "geo": {"artifact": result["geo"]["artifact"], "computed_at": result["geo"].get("computed_at"),
                "calculation_version": result["geo"].get("calculation_version")},
        "min_floor_sea": {"before": before, "after": after},
        "claims": claims,
        "passport": {"guest_name": "Демо-гость (синтетический паспорт)", "text": "Вид на море из окна",
                     "criterion": {"characteristic_id": "sea_view", "label": "Вид на море", "field": "available", "value": True}},
    }
    (data_dir / "seed" / "demo_change_event.json").write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return {"hotel_id": hotel_id, "artifact": result["geo"]["artifact"], "claims": len(claims), "min_floor_sea": document["min_floor_sea"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hotel-id", default=DEFAULT_HOTEL)
    args = parser.parse_args(argv)
    print(json.dumps(build(args.hotel_id), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
