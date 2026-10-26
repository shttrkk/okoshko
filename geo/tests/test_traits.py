from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pytest
import yaml
from shapely.geometry import LineString, Point, box

from backend.contracts import CharacteristicSpec, Claim
from geo.coordinates import TO_UTM
from geo.sun import facade_exposure, monthly_exposure_paths
from geo.traits import TRAITS, compute_trait, route_accessibility, solar_floor_ranges


def test_morning_evening_orientation_blocker_and_missing_horizon():
    morning = monthly_exposure_paths(39.72, 43.58, period="morning")[8]
    evening = monthly_exposure_paths(39.72, 43.58, period="evening")[8]
    angles = np.arange(0, 360, 3)
    clear = np.zeros((1, len(angles)))
    east = facade_exposure(morning, angles, clear, 90)[0]
    west = facade_exposure(morning, angles, clear, 270)[0]
    assert east["visible_minutes"] >= 150
    assert west["visible_minutes"] == 0
    assert east["exposure_index"] > .5
    assert facade_exposure(evening, angles, clear, 270)[0]["exposure_index"] > .5
    assert facade_exposure(evening, angles, clear, 90)[0]["visible_minutes"] == 0
    assert facade_exposure(morning, angles, np.full_like(clear, 90), 90)[0]["visible_minutes"] == 0
    assert facade_exposure(morning, angles, np.full_like(clear, np.nan), 90) == [None]
    # Unknown horizon on the back side is irrelevant, not a fabricated blockage.
    assert facade_exposure(morning, angles, np.full_like(clear, np.nan), 270)[0]["visible_minutes"] == 0
    assert datetime.fromisoformat(morning["points"][0]["time"]).utcoffset().total_seconds() == 0
    assert len(morning["points"]) == 36


def test_accessibility_missing_tags_and_unverified_endpoints_stay_unknown():
    edges = [{"u": "1", "v": "2", "osm_way_id": "way", "highway": "footway"}]
    missing = route_accessibility(edges, {})
    assert missing["has_steps"] is False
    assert missing["available"] is None
    assert missing["wheelchair_tag_coverage"] == 0
    explicit_no = route_accessibility(edges, {"way": {"wheelchair": "no"}})
    assert explicit_no["available"] is False
    explicit_yes = {"way": {"wheelchair": "yes", "surface": "asphalt"}}
    assert route_accessibility(edges, explicit_yes)["available"] is None
    assert route_accessibility(edges, explicit_yes, endpoint_connections_verified=True)["available"] is True
    stairs = route_accessibility([{**edges[0], "highway": "steps"}], {})
    assert stairs["has_steps"] is True
    assert stairs["available"] is None


def test_compact_solar_ranges_preserve_extrema_and_heat_threshold():
    outputs = [{"visible_minutes": 30, "exposure_index": .1},
               {"visible_minutes": 40, "exposure_index": .2},
               {"visible_minutes": 50, "exposure_index": .5}, None,
               {"visible_minutes": 10, "exposure_index": .01}]
    ranges = list(solar_floor_ranges(outputs, "evening_heat"))
    assert [(low, high) for low, high, _ in ranges] == [(1, 2), (3, 3), (5, 5)]
    value = ranges[0][2]
    assert value["visible_minutes"] == value["visible_minutes_min"] == 30
    assert value["visible_minutes_max"] == 40
    assert value["exposure_index"] == value["exposure_index_max"] == .2
    assert value["exposure_index_min"] == .1
    assert "bound" in value["value_semantics"]


@pytest.fixture
def solar_artifacts(tmp_path):
    directory = tmp_path/"geo"
    directory.mkdir()
    hotel = {"id": "test", "lon": 39.72, "lat": 43.58}
    baseline = {"hotel_id": "test", "computed_at": "2026-10-26T00:00:00Z", "synthetic": True,
                "floors": {"count": 2}, "inputs": {}, "limitations": [],
                "facades": [{"code": "E", "azimuth_deg": 90, "confidence": .8},
                            {"code": "W", "azimuth_deg": 270, "confidence": .8}],
                "claims": [{"evidence": [{"source_ids": ["test_fixture"]}]}]}
    (directory/"test.json").write_text(json.dumps(baseline))
    np.savez_compressed(directory/"test.npz", facade_0_horizon=np.zeros((2, 120)),
                        facade_1_horizon=np.zeros((2, 120)))
    return hotel, tmp_path, baseline


def test_adapter_all_months_scopes_provenance_and_no_writes(solar_artifacts):
    hotel, directory, _ = solar_artifacts
    before = {path.name: path.read_bytes() for path in (directory/"geo").iterdir()}
    result = compute_trait(hotel, directory, "morning_sun")
    claims = [Claim.model_validate(row) for row in result["claims"]]
    assert len(claims) == 24
    assert {claim.scope.month for claim in claims} == set(range(1, 13))
    assert {(claim.scope.floor_min, claim.scope.floor_max) for claim in claims} == {(1, 2)}
    assert all(claim.synthetic and claim.evidence[0].kind == "simulation" for claim in claims)
    assert all(claim.confidence <= .54 for claim in claims)
    assert all(claim.evidence[0].inputs["input_sha256"] for claim in claims)
    september = compute_trait(hotel, directory, "evening_heat", month=9)["claims"]
    assert len(september) == 2
    assert all(row["scope"]["month"] == 9 and row["value"]["temperature_c"] is None for row in september)
    assert all(0 <= row["value"]["exposure_index"] <= 1 for row in september)
    assert before == {path.name: path.read_bytes() for path in (directory/"geo").iterdir()}


def test_unknown_floors_and_changed_snapshot_do_not_reuse_horizon(solar_artifacts):
    hotel, directory, baseline = solar_artifacts
    baseline["floors"]["count"] = None
    (directory/"geo"/"test.json").write_text(json.dumps(baseline))
    assert compute_trait(hotel, directory, "morning_sun")["claims"] == []
    baseline["floors"]["count"] = 2
    baseline["inputs"] = {"/data/seed/missing.geojson": "0"*64}
    (directory/"geo"/"test.json").write_text(json.dumps(baseline))
    result = compute_trait(hotel, directory, "morning_sun")
    assert not result["claims"] and "пересчёта" in result["warnings"][0]


class FakeRegion:
    def __init__(self, features, own=None):
        self.features = {name: features.get(name, []) for name in ("roads", "railways", "beaches", "pois")}
        self.own = own
        self.observed_at = "2026-10-25T21:21:21Z"
        self.inputs = {"/data/seed/"+name+".geojson": "test-hash" for name in self.features}

    def nearby(self, name, xy, radius):
        return [f for f in self.features[name] if f["shape"].distance(Point(xy)) <= radius]

    def buildings(self, *args):
        return [self.own] if self.own else []

    def hotel_building(self, *args):
        return self.own, "contains", 0


def feature(id, geometry, **properties):
    return {"id": id, "shape": geometry, "properties": {"source_id": "osm_geofabrik",
            "observed_at": "2026-10-25T21:21:21Z", **properties}}


def test_beach_missing_surface_is_not_sand_and_green_uses_polygon_boundary(monkeypatch, tmp_path):
    hotel = {"id": "test", "lon": 39.72, "lat": 43.58}
    x, y = TO_UTM.transform(hotel["lon"], hotel["lat"])
    beach = feature("beach", box(x+100, y-20, x+500, y+20), natural="beach")
    park = feature("park", box(x+400, y-200, x+2000, y+200), leisure="park")
    private = feature("private", box(x+50, y-10, x+70, y+10), leisure="garden", access="private")
    region = FakeRegion({"beaches": [beach], "pois": [park, private]})
    monkeypatch.setattr("geo.traits.get_region", lambda _: region)
    unknown = compute_trait(hotel, tmp_path, "beach_surface")["claims"][0]
    assert unknown["value"]["surface"] is None and unknown["value"]["available"] is None
    beach["properties"]["surface"] = "pebblestone"
    known = compute_trait(hotel, tmp_path, "beach_surface")["claims"][0]
    assert known["value"]["surface"] == "pebblestone"
    green = compute_trait(hotel, tmp_path, "green_nearby")["claims"][0]
    assert green["value"]["osm_id"] == "park"
    assert green["value"]["distance_m"] == 400
    assert green["value"]["available"] is True
    assert Claim.model_validate(known).evidence[0].inputs["tags"]["surface"] == "pebblestone"


def test_railway_proxy_has_no_road_contribution_and_no_false_quiet(monkeypatch, tmp_path):
    hotel = {"id": "test", "lon": 39.72, "lat": 43.58}
    x, y = TO_UTM.transform(hotel["lon"], hotel["lat"])
    own = feature("own", box(x-10, y-10, x+10, y+10))
    rail = feature("rail", LineString([(x-50, y-250), (x-50, y+250)]), railway="rail")
    road = feature("road", LineString([(x-20, y-250), (x-20, y+250)]), highway="motorway")
    region = FakeRegion({"roads": [road], "railways": [rail]}, own)
    monkeypatch.setattr("geo.traits.get_region", lambda _: region)
    rows = compute_trait(hotel, tmp_path, "railway_noise")["claims"]
    assert rows and all(row["confidence"] <= .38 for row in rows)
    assert all(row["value"]["sources"] == ["rail"] and row["value"]["measurement"] is False for row in rows)
    west = next(row["value"] for row in rows if row["scope"]["facade"] == "W")
    east = next(row["value"] for row in rows if row["scope"]["facade"] == "E")
    assert west["night_db_min"] > east["night_db_min"]+8
    region.features["railways"] = []
    result = compute_trait(hotel, tmp_path, "railway_noise")
    assert result["claims"] == [] and result["warnings"]


def test_new_specs_are_typed_and_have_one_whitelisted_adapter():
    from pathlib import Path
    for characteristic in TRAITS:
        spec = CharacteristicSpec.model_validate(yaml.safe_load(Path("data/characteristics", characteristic+".yaml").read_text()))
        assert spec.methods[0].tool == "geo_trait"
        assert spec.methods[0].params == {"kind": characteristic}
        assert spec.ttl_days == 90
