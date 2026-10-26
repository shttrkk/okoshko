"""Offline model comparison; no network, runtime database, or invented hotel claims.

Reproduce: python -m scripts.compare_filters --self-test
           python -m scripts.compare_filters --month 9 --k-floor 5 --confidence-floor .6
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import yaml
from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union

from backend.agents.query_match import assess_hotel, fresh, scope_values
from backend.agents.resolver import resolve_claims
from backend.contracts import CharacteristicSpec, Claim, Criterion, Fact, ParsedQuery
from geo.coordinates import geographic, metric
from geo.io import exists, read_bytes, read_json

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ("sea_view", "sunset_view")
NEGATIVE_CONFIDENCE = .7  # Same trusted refusal threshold as the production evaluator.


def metric_distance(hotel: dict, coastline) -> float | None:
    """Minimum point-to-mapped-coast distance in EPSG:32637 metres, not a walk."""
    if coastline is None or coastline.is_empty:
        return None
    try:
        return float(metric(Point(float(hotel["lon"]), float(hotel["lat"]))).distance(coastline))
    except (KeyError, TypeError, ValueError):
        return None


def slot_state(facts: list[Fact], facade: str, floor: int, month: int, confidence: float) -> str:
    candidates = []
    for fact in facts:
        scope = scope_values(fact)
        if (fact.synthetic or not fresh(fact) or not fact.evidence or not fact.supporting_claim_ids
                or scope.get("facade") != facade or scope.get("month", month) != month
                or scope.get("floor_min") is None or scope.get("floor_max") is None
                or not scope["floor_min"] <= floor <= scope["floor_max"]):
            continue
        value = fact.value.get("available") if isinstance(fact.value, dict) else None
        if value is True and fact.confidence >= confidence:
            candidates.append("positive")
        elif value is False and fact.confidence >= max(confidence, NEGATIVE_CONFIDENCE):
            candidates.append("negative")
    # Missing / conflicted / weak negative observations never establish absence.
    return candidates[0] if candidates and len(set(candidates)) == 1 else "unknown"


def summarize_view(hotel: dict, geo: dict, facts: list[Fact], cid: str, month: int, k: int, confidence: float) -> dict:
    model = geo.get("floors", {})
    count = model.get("modeled_count") or 0
    known_count = hotel.get("floors")
    facades = sorted({row["code"] for row in geo.get("facades", []) if row.get("code")})
    complete_domain = bool(isinstance(known_count, int) and known_count > 0 and count >= known_count
                           and model.get("count") == known_count and facades and not geo.get("synthetic"))
    height = min(count, known_count) if complete_domain else count
    selected = [fact for fact in facts if fact.characteristic_id == cid]
    slots = {(side, floor): slot_state(selected, side, floor, month, confidence)
             for side in facades for floor in range(1, height + 1)}
    positive = [slot for slot, state in slots.items() if state == "positive"]
    complete_known = bool(complete_domain and slots and all(state != "unknown" for state in slots.values()))
    state = "positive" if positive else "model_negative" if complete_known and all(s == "negative" for s in slots.values()) else "unknown"
    positive_sides = {side for side, _ in positive}
    observed_high_only = bool(positive and min(floor for _, floor in positive) >= k)
    observed_one_side = len(positive_sides) == 1
    return {
        "state": state, "partial_positive": bool(positive and not complete_known),
        "complete_modeled_domain": complete_domain, "complete_known_slots": complete_known,
        "known_floor_count": known_count, "modeled_floor_count": count, "facade_count": len(facades),
        "positive_scope_count": len(positive), "unknown_scope_count": sum(s == "unknown" for s in slots.values()),
        "observed_positive_only_at_or_above_k": observed_high_only,
        "model_only_at_or_above_k": bool(observed_high_only and complete_domain and all(
            state == "negative" for (_, floor), state in slots.items() if floor < k)),
        "observed_positive_only_one_facade": observed_one_side,
        "model_only_one_facade": bool(observed_one_side and complete_domain and all(
            state == "negative" for (side, _), state in slots.items() if side not in positive_sides)),
    }


def self_test() -> None:
    from datetime import timedelta
    # Known answers in metric space are round-tripped to public WGS84 input.
    coast = LineString([(500000, 4800000), (500000, 4802000)])
    for offset in (0, 499, 501):
        point = geographic(Point(500000 + offset, 4801000))
        actual = metric_distance({"lon": point.x, "lat": point.y}, coast)
        assert abs(actual - offset) < .001
        assert (actual <= 500) == (offset <= 500)
    assert metric_distance({"lon": 39.7, "lat": 43.5}, None) is None
    now = datetime.now(UTC)
    evidence = [{"id": "test", "kind": "calculation", "label": "known-answer synthetic geometry test",
                 "source_ids": ["test"], "artifact": "/test.json"}]
    negative = Fact(hotel_id="test", characteristic_id="sea_view", value={"available": False}, confidence=.9,
                    status="computed", supporting_claim_ids=["test"], evidence=evidence,
                    scope={"type": "floor_range", "facade": "SW", "floor_min": 1, "floor_max": 5},
                    valid_until=now + timedelta(days=1))
    geo = {"floors": {"count": 5, "modeled_count": 5}, "facades": [{"code": "SW"}]}
    def result(hotel=None, facts=None, geometry=None):
        return summarize_view(hotel or {"floors": 5}, geometry or geo, facts or [negative], "sea_view", 9, 5, .6)
    assert result()["state"] == "model_negative"
    assert result({"floors": None})["state"] == "unknown"
    assert result(geometry={**geo, "floors": {"count": 8, "modeled_count": 5}})["state"] == "unknown"
    for change in ({"confidence": .69}, {"status": "disputed"}, {"value": {"available": None}},
                   {"synthetic": True}, {"valid_until": now - timedelta(days=1)}):
        assert result(facts=[negative.model_copy(update=change)])["state"] == "unknown"
    assert result(geometry={**geo, "facades": [{"code": "SW"}, {"code": "N"}]})["state"] == "unknown"
    from backend.quality import provenance
    base = negative.model_dump(mode="json")
    rows = [{**base, "synthetic": False, "supporting_claim_ids": ["real"]},
            {**base, "synthetic": True, "supporting_claim_ids": ["fake"]},
            {**base, "synthetic": True, "supporting_claim_ids": ["real", "fake"]}]
    rows += [{**base, **change} for change in ({"status": "unknown"}, {"status": "stale"},
             {"status": "disputed"}, {"value": {}}, {"value": []}, {"value": " "},
             {"valid_until": (now - timedelta(days=1)).isoformat()}, {"value": {"available": None}})]
    result = provenance(rows, [{"id": "real", "synthetic": False}, {"id": "fake", "synthetic": True}], at=now)
    assert result == {"unit": "materialized_facts", "known": 3, "non_synthetic": 1, "synthetic": 2,
                      "mixed": 1, "unknown_stale_disputed": 8, "synthetic_share": 2/3}
    assert provenance([])["synthetic_share"] is None
    print("known-answer metric geometry, absence/unknown and provenance denominator guards: PASS")


def compare(data: Path, *, radius: float, month: int, k: int, confidence: float) -> dict:
    hashes = {}
    def hashed(path: Path):
        value = read_bytes(path)
        hashes[str(path.relative_to(data))] = hashlib.sha256(value).hexdigest()
        return value
    hotels = json.loads(hashed(data / "seed/hotels.json"))
    manifest = json.loads(hashed(data / "seed/manifest.json"))
    coastline_doc = read_json(data / "seed/coastline.geojson")  # Logical path supports .gz fallback.
    hashed(data / "seed/coastline.geojson")
    coast = unary_union([metric(shape(feature["geometry"])) for feature in coastline_doc.get("features", [])
                         if feature.get("properties", {}).get("source_id", "osm_geofabrik") == "osm_geofabrik"])
    specs = []
    for path in sorted((data / "characteristics").glob("*.yaml")):
        specs.append(CharacteristicSpec.model_validate(yaml.safe_load(hashed(path))))
    sources = []
    source_path = data / "seed/sources.csv.gz"
    hashed(source_path)
    with gzip.open(source_path, "rt", encoding="utf-8") as stream:
        sources = [json.loads(row["payload"]) for row in csv.DictReader(stream)]
    by_hotel = defaultdict(list)
    excluded = Counter()
    for path in (data / "seed/open_claims.jsonl.gz", data / "geo/geo_claims.jsonl.gz"):
        if not path.is_file():
            continue
        hashed(path)
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if row["characteristic_id"] not in VIEWS:
                    continue
                if row.get("synthetic") or any(e.get("kind") not in {"calculation", "open_data"} for e in row["evidence"]):
                    excluded["synthetic_or_non_open"] += 1
                    continue
                by_hotel[row["hotel_id"]].append(Claim.model_validate(row))
    parsed = ParsedQuery(text="Вид на море и закат: модельное сравнение", month=month, min_floor=1,
                         must=[Criterion(characteristic_id=cid, label=cid, value=True) for cid in VIEWS])
    rows = []
    for hotel in hotels:
        path = data / "geo" / (hotel["id"] + ".json")
        geo = json.loads(hashed(path)) if exists(path) else {}
        # Explicitly exclude new-building artifacts and any synthetic model envelope.
        if geo.get("synthetic"):
            geo = {}
        facts, _ = resolve_claims(hotel["id"], by_hotel[hotel["id"]], specs, sources)
        facts = [fact for fact in facts if not fact.synthetic and fact.characteristic_id in VIEWS]
        views = {cid: summarize_view(hotel, geo, facts, cid, month, k, confidence) for cid in VIEWS}
        assessment = assess_hotel(facts, parsed, specs)
        supported = len(assessment.matches) == 2 and all(match.supported and match.confidence >= confidence for match in assessment.matches)
        rows.append({"hotel_id": hotel["id"], "name": hotel["name"], "distance_m": metric_distance(hotel, coast),
                     "views": views, "compatible": supported,
                     "partial": supported and any(match.status == "partial" for match in assessment.matches),
                     "context": {"facade": assessment.context.facade, "floor": assessment.context.floor, "month": month},
                     "confidence": [round(match.confidence, 4) for match in assessment.matches],
                     "scope_conflict": assessment.scope_conflict})
    inside = [row for row in rows if row["distance_m"] is not None and row["distance_m"] <= radius]
    outside = [row for row in rows if row["distance_m"] is not None and row["distance_m"] > radius]
    found = [row for row in rows if row["compatible"]]
    outside_found = [row for row in outside if row["compatible"]]
    def view_counts(values, cid):
        counts = Counter(row["views"][cid]["state"] for row in values)
        return {**{key: counts[key] for key in ("positive", "model_negative", "unknown")},
                "partial_positive": sum(row["views"][cid]["partial_positive"] for row in values)}
    def example(row):
        return {"hotel_id": row["hotel_id"], "name": row["name"], "distance_m": round(row["distance_m"], 1) if row["distance_m"] is not None else None,
                "context": row["context"], "confidence": row["confidence"], "partial": row["partial"]}
    return {
        "schema_version": 1, "generated_at": datetime.now(UTC).isoformat(),
        "seed_version": manifest.get("seed_version", manifest.get("generated_at", "unknown")),
        "settings": {"radius_m": radius, "month": month, "k_floor": k, "confidence_floor": confidence,
                     "negative_confidence_floor": max(confidence, NEGATIVE_CONFIDENCE), "search_min_floor": 1,
                     "distance_method": "minimum hotel point to mapped OSM coastline, EPSG:32637 metric distance",
                     "search_method": "production resolve_claims + assess_hotel; sea_view AND sunset_view in one compatible room scope",
                     "input_policy": "versioned nonsynthetic geo/open seed only; no runtime, hotelier fixtures or new-building artifacts",
                     "reproduce": f"python -m scripts.compare_filters --month {month} --k-floor {k} --confidence-floor {confidence:g} --radius {radius:g}"},
        "population": {"hotels": len(rows), "known_floor_count": sum(bool(h.get("floors")) for h in hotels),
                       "unknown_floor_count": sum(not h.get("floors") for h in hotels),
                       "distance_unknown": sum(row["distance_m"] is None for row in rows), "excluded_claims": dict(excluded)},
        "baseline": {"within_radius": len(inside), "outside_radius": len(outside), "views_within_radius": {cid: view_counts(inside, cid) for cid in VIEWS}},
        "scoped": {cid: {**view_counts(rows, cid), **{key: sum(row["views"][cid][key] for row in rows) for key in (
            "observed_positive_only_at_or_above_k", "model_only_at_or_above_k", "observed_positive_only_one_facade", "model_only_one_facade")},
            "complete_modeled_domain": sum(row["views"][cid]["complete_modeled_domain"] for row in rows),
            "complete_known_slots": sum(row["views"][cid]["complete_known_slots"] for row in rows)} for cid in VIEWS},
        "search": {"compatible_supported": len(found), "partial_supported": sum(row["partial"] for row in found),
                   "within_radius_supported": sum(row["compatible"] for row in inside), "outside_radius_supported": len(outside_found),
                   "outside_radius_partial": sum(row["partial"] for row in outside_found),
                   "scope_conflict": sum(row["scope_conflict"] for row in rows),
                   "unknown_or_unsupported": len(rows) - len(found)},
        "examples": {"outside_radius_supported": [example(row) for row in sorted(outside_found, key=lambda row: row["distance_m"])[:5]],
                     "within_radius_unknown": [example(row) for row in inside if any(row["views"][cid]["state"] == "unknown" for cid in VIEWS)][:3]},
        "limitations": [
            "Это сравнение фильтров на сохранённой модели, не конверсия, не полевая точность и не подтверждение реальных окон или номеров.",
            "Расстояние до картированной береговой линии по прямой не означает пеший доступ к пляжу, безопасность или вид из окна.",
            "Отрицательный вывод относится только ко всем покрытым моделью фасадам/этажам при известной этажности и confidence ≥ порога отказа; неполнота остаётся unknown.",
            "Observed-only считает расположение доступных положительных расчётов. Model-only дополнительно требует уверенные negatives во всех исключённых scope; это всё ещё не планировка окон.",
            "Положительные partial ≥0,6 показаны отдельно; scope compatibility и пороги штатного evaluator не ослаблены.",
            "Фасады — направления модели, а не инвентаризация окон. Неизвестные высоты, DSM и ограниченный регион береговой линии влияют на результат.",
            "Свежесть проверяется на время генерации. После истечения TTL повторный запуск вправе переводить наблюдения в unknown.",
        ],
        "inputs_sha256": {**hashes, "scripts/compare_filters.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path, default=ROOT / "data/eval/filter_baseline.json")
    parser.add_argument("--radius", type=float, default=500)
    parser.add_argument("--month", type=int, choices=range(1, 13), default=9)
    parser.add_argument("--k-floor", type=int, default=5)
    parser.add_argument("--confidence-floor", type=float, default=.6)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not 1 <= args.k_floor <= 100 or not .6 <= args.confidence_floor <= 1 or args.radius <= 0:
        parser.error("k-floor must be 1..100, confidence-floor .6..1, radius >0; production thresholds cannot be weakened")
    report = compare(args.data.resolve(), radius=args.radius, month=args.month, k=args.k_floor, confidence=args.confidence_floor)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"population": report["population"], "baseline": report["baseline"], "scoped": report["scoped"], "search": report["search"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
