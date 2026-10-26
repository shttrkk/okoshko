"""Known-answer checks for parking / EV charging proximity (synthetic coordinates)."""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from backend.agents.query_parser import parse_query
from backend.agents.ranker import rank_hotels
from backend.agents.resolver import resolve_claims
from backend.contracts import CharacteristicSpec, Claim, Evidence, Hotel, SearchRequest, ToolCall, ToolContext
from backend.seed import poi_plans
from backend.tests.test_api_integration import ApiRepo
from backend.tools.registry import EV_CHARGING_NEAR_M, PARKING_NEAR_M, Registry, poi_method

ROOT = Path(__file__).resolve().parents[1]
HOTEL = {"id": "car-1", "name": "Synthetic car hotel", "lat": 43.58, "lon": 39.72}
METRES_PER_DEGREE_LAT = 111_132


@pytest.fixture
def catalog():
    return [CharacteristicSpec.model_validate(yaml.safe_load(path.read_text()))
            for path in sorted((ROOT / "data/characteristics").glob("*.yaml"))]


def point_north(metres: float, **properties):
    return {"type": "Feature", "id": properties.pop("id"), "properties": {"observed_at": "2026-10-25T21:21:21Z", **properties},
            "geometry": {"type": "Point", "coordinates": [HOTEL["lon"], HOTEL["lat"] + metres / METRES_PER_DEGREE_LAT]}}


def collect(tmp_path, features, repo=None):
    (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
    (tmp_path / "seed" / "pois.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    repo = repo or ApiRepo()
    if repo.get("hotels", HOTEL["id"]) is None:
        repo.insert("hotels", HOTEL)
    registry = Registry(repo, data_dir=tmp_path)
    context = ToolContext(hotel_id=HOTEL["id"], characteristic_id="car_access", run_id="run")
    [claim] = registry.execute(context, ToolCall(tool="poi_nearby", params={"kind": "car"})).claims
    return claim


def test_parking_within_threshold_is_available_with_explicit_numbers(tmp_path):
    claim = collect(tmp_path, [point_north(300, id="osm-node-p1", amenity="parking"),
                               point_north(700, id="osm-node-ev1", amenity="charging_station")])
    assert claim.method == poi_method("car") == "local_poi_lookup@3"
    assert claim.value["available"] is True and 280 <= claim.value["parking_distance_m"] <= 320
    assert claim.value["ev_available"] is True and claim.value["parking_threshold_m"] == PARKING_NEAR_M
    assert claim.value["ev_threshold_m"] == EV_CHARGING_NEAR_M
    assert any(str(PARKING_NEAR_M) in limit for limit in claim.evidence[0].limitations)


def test_distant_parking_is_a_negative_and_missing_parking_stays_unknown(tmp_path):
    far = collect(tmp_path / "far", [point_north(800, id="osm-node-p2", amenity="parking")])
    assert far.value["available"] is False and far.value["parking_distance_m"] > PARKING_NEAR_M
    private = collect(tmp_path / "private", [point_north(100, id="osm-node-p3", amenity="parking", access="private")])
    assert private.value["available"] is None and private.value["parking_distance_m"] is None
    assert private.confidence < .5


def test_new_method_version_supersedes_older_parking_claims(tmp_path):
    repo = ApiRepo()
    repo.insert("hotels", HOTEL)
    observed = datetime(2026, 10, 25, tzinfo=UTC)
    legacy = Claim(id="poi-legacy", hotel_id=HOTEL["id"], characteristic_id="car_access", value={"available": None, "parking_distance_m": 91},
                   source="osm_geofabrik", method="local_poi_lookup@2", confidence=.65, observed_at=observed, valid_until=observed + timedelta(days=90),
                   evidence=[Evidence(kind="calculation", label="legacy", source_ids=["osm_geofabrik"], artifact="/data/seed/pois.geojson")])
    repo.add_claim(legacy)
    claim = collect(tmp_path, [point_north(90, id="osm-node-p4", amenity="parking")], repo)
    assert "poi-legacy" in claim.supersedes_ids
    facts, _ = resolve_claims(HOTEL["id"], [legacy, claim], [], [])
    car = [fact for fact in facts if fact.characteristic_id == "car_access"]
    assert len(car) == 1 and car[0].value["available"] is True and car[0].supporting_claim_ids == [claim.id]


@pytest.mark.parametrize(("text", "expected"), [
    ("с собакой 30 кг, нужна парковка", [("parking_distance_m", 500, "Парковка ≤ 500 м")]),
    ("на машине и зарядка для электромобиля", [("parking_distance_m", 500, "Парковка ≤ 500 м"), ("ev_distance_m", 1000, "Зарядка для электромобиля ≤ 1000 м")]),
    ("электромобиль, нужна зарядка", [("ev_distance_m", 1000, "Зарядка для электромобиля ≤ 1000 м")]),
    ("парковка не дальше 200 м", [("parking_distance_m", 200, "Парковка ≤ 200 м")]),
])
def test_parser_states_the_threshold_in_the_criterion(text, expected, catalog):
    parsed = parse_query(SearchRequest(text=text), catalog)
    car = [(c.field, c.value, c.label) for c in parsed.must if c.characteristic_id == "car_access"]
    assert car == expected
    assert all(c.op == "lte" for c in parsed.must if c.characteristic_id == "car_access")


def test_measured_parking_supports_search_and_missing_distance_does_not(tmp_path, catalog):
    near = collect(tmp_path / "near", [point_north(91, id="osm-node-p5", amenity="parking")])
    missing = near.model_copy(update={"id": "poi-missing", "hotel_id": "car-2",
                                      "value": {**near.value, "available": None, "parking_distance_m": None}, "confidence": .35})
    facts = [*resolve_claims("car-1", [near], catalog, [])[0], *resolve_claims("car-2", [missing], catalog, [])[0]]
    parsed = parse_query(SearchRequest(text="нужна парковка"), catalog)
    hits = {hit.hotel.id: hit for hit in rank_hotels([Hotel(**HOTEL), Hotel(**{**HOTEL, "id": "car-2"})], facts, parsed, catalog)}
    assert hits["car-1"].group == "all" and "до парковки ≈ 91 м" in hits["car-1"].verdicts[0].text
    assert hits["car-2"].group == "unknown" and hits["car-2"].verdicts[0].status == "unknown"


def test_boot_backfill_follows_poi_characteristics_declared_in_yaml(catalog):
    plans = {spec.id: params["kind"] for spec, params in poi_plans(catalog)}
    assert {"car_access": "car", "night_pharmacy": "pharmacy", "family_nearby": "family"}.items() <= plans.items()
