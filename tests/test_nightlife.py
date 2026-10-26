"""«В гуще событий»: a declarative characteristic over local OSM POIs (synthetic coordinates)."""
import json
from pathlib import Path

import pytest
import yaml

from backend.agents.query_parser import parse_query
from backend.agents.ranker import rank_hotels
from backend.agents.resolver import resolve_claims
from backend.contracts import CharacteristicSpec, Hotel, SearchRequest, ToolCall, ToolContext
from backend.seed import poi_plans
from backend.tests.test_api_integration import ApiRepo
from backend.tools.registry import NIGHTLIFE_MIN_VENUES, Registry

ROOT = Path(__file__).resolve().parents[1]
HOTEL = {"id": "night-1", "name": "Synthetic nightlife hotel", "lat": 43.58, "lon": 39.72}
METRES_PER_DEGREE_LAT = 111_132


@pytest.fixture
def catalog():
    return [CharacteristicSpec.model_validate(yaml.safe_load(path.read_text()))
            for path in sorted((ROOT / "data/characteristics").glob("*.yaml"))]


def at(metres, index, **properties):
    return {"type": "Feature", "id": f"osm-node-{index}", "properties": {"observed_at": "2026-10-25T21:21:21Z", **properties},
            "geometry": {"type": "Point", "coordinates": [HOTEL["lon"], HOTEL["lat"] + metres / METRES_PER_DEGREE_LAT]}}


def nightlife_claim(tmp_path, venues: int, bars: int):
    pois = [at(100 + i, i, amenity="bar" if i < bars else "cafe") for i in range(venues)] + [at(900, 999, amenity="restaurant")]
    lat = HOTEL["lat"] + 200 / METRES_PER_DEGREE_LAT
    roads = [{"type": "Feature", "id": "osm-way-1", "properties": {"highway": "pedestrian", "name": "Набережная"},
              "geometry": {"type": "LineString", "coordinates": [[HOTEL["lon"] - .001, lat], [HOTEL["lon"] + .001, lat]]}}]
    (tmp_path / "seed").mkdir(parents=True)
    (tmp_path / "seed" / "pois.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": pois}))
    (tmp_path / "seed" / "roads.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": roads}))
    repo = ApiRepo()
    repo.insert("hotels", HOTEL)
    context = ToolContext(hotel_id=HOTEL["id"], characteristic_id="nightlife", run_id="run")
    [claim] = Registry(repo, data_dir=tmp_path).execute(context, ToolCall(tool="poi_nearby", params={"kind": "nightlife"})).claims
    return claim


def test_density_counts_only_venues_within_500_m_and_finds_the_promenade(tmp_path):
    claim = nightlife_claim(tmp_path, venues=NIGHTLIFE_MIN_VENUES, bars=4)
    assert claim.method == "local_poi_nightlife@1" and claim.value["available"] is True
    assert claim.value["venues_500m"] == NIGHTLIFE_MIN_VENUES and claim.value["bars_500m"] == 4
    assert 190 <= claim.value["promenade_distance_m"] <= 210 and claim.value["nearest_bar_m"] == 100
    assert any("30 баров" in limit for limit in claim.evidence[0].limitations)
    assert "/data/seed/roads.geojson" in claim.evidence[0].inputs["artifacts"]
    quiet = nightlife_claim(tmp_path / "quiet", venues=3, bars=0)
    assert quiet.value["available"] is False and quiet.value["venues_500m"] == 3


@pytest.mark.parametrize(("text", "op", "value", "label"), [
    ("хочу жить в гуще событий, рядом бары и ночная жизнь", "gte", 30, "В гуще событий ≥ 30 заведений в 500 м"),
    ("тусовки и движ", "gte", 30, "В гуще событий ≥ 30 заведений в 500 м"),
    ("кафе и рестораны рядом", "gte", 30, "В гуще событий ≥ 30 заведений в 500 м"),
    ("подальше от баров", "lte", 10, "Спокойный район ≤ 10 заведений в 500 м"),
])
def test_parser_maps_lively_and_calm_requests(text, op, value, label, catalog):
    parsed = parse_query(SearchRequest(text=text), catalog)
    [criterion] = [c for c in parsed.must if c.characteristic_id == "nightlife"]
    assert (criterion.field, criterion.op, criterion.value, criterion.label) == ("venues_500m", op, value, label)
    assert not parsed.unmapped


def test_lively_hotel_ranks_first_with_numbers_in_the_explanation(tmp_path, catalog):
    lively = nightlife_claim(tmp_path / "lively", venues=40, bars=6)
    calm = nightlife_claim(tmp_path / "calm", venues=4, bars=0).model_copy(update={"id": "calm-claim", "hotel_id": "night-2"})
    facts = [*resolve_claims("night-1", [lively], catalog, [])[0], *resolve_claims("night-2", [calm], catalog, [])[0]]
    hotels = [Hotel(**HOTEL), Hotel(**{**HOTEL, "id": "night-2"})]
    hits = rank_hotels(hotels, facts, parse_query(SearchRequest(text="в гуще событий"), catalog), catalog)
    assert [hit.hotel.id for hit in hits] == ["night-1", "night-2"]
    assert hits[0].group == "all" and "в 500 м заведений: 40, из них баров и клубов 6" in hits[0].verdicts[0].text
    assert hits[1].group != "all"


def test_nightlife_is_declared_in_yaml_and_backfilled_like_other_poi_characteristics(catalog):
    spec = next(spec for spec in catalog if spec.id == "nightlife")
    assert spec.search.unit == "заведений в 500 м" and spec.methods[0].params == {"kind": "nightlife"}
    assert ("nightlife", "nightlife") in {(spec.id, params.get("kind")) for spec, params in poi_plans(catalog)}


@pytest.mark.parametrize("text", ["до пляжа 5 минут пешком без перехода через оживлённую дорогу",
                                  "без перехода через оживлённую трассу", "тихо, далеко от оживлённой улицы"])
def test_busy_road_is_not_nightlife(text, catalog):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert "nightlife" not in {c.characteristic_id for c in [*parsed.must, *parsed.nice]}
