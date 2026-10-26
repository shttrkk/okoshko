from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pytest
import yaml
from shapely.geometry import LineString, Point, box, mapping, shape

from backend.contracts import CharacteristicSpec, Claim
from geo.coordinates import TO_UTM, metric
from geo.sun import facade_exposure, monthly_exposure_paths
from geo.traits import TRAITS, compute_trait, route_accessibility, solar_building_match, solar_floor_ranges


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


def test_solar_association_requires_robust_interior_when_hotel_floors_unknown():
    footprint = box(39.7199, 43.5799, 39.7201, 43.5801)
    baseline = {"building": {"id": "building", "geometry": mapping(footprint)},
                "building_id": "building", "building_match": {"method": "contains", "distance_m": 0},
                "floors": {"count": 2}, "facades": [{"confidence": .75}]}
    hotel = {"id": "example", "lon": 39.72, "lat": 43.58, "floors": None}
    assert solar_building_match(hotel, baseline) == (True, "strict_interior")
    assert solar_building_match({**hotel, "lon": 39.7201}, baseline) == (False, "boundary_or_outside")
    assert solar_building_match({**hotel, "lon": 39.7202}, baseline) == (False, "boundary_or_outside")
    # A sub-millimetre projected interior is still a numerical boundary case.
    assert solar_building_match({**hotel, "lon": 39.7201-1e-10}, baseline) == (False, "boundary_or_outside")


def test_explicit_floor_identity_and_conditional_nearest_are_distinct_from_contains():
    footprint = box(39.7199, 43.5799, 39.7201, 43.5801)
    baseline = {"building": {"id": "building", "geometry": mapping(footprint)},
                "building_id": "building", "building_match": {"method": "id", "distance_m": 0},
                "floors": {"count": 2}, "facades": [{"confidence": .75}]}
    hotel = {"id": "example", "lon": 39.7201, "lat": 43.58, "floors": 2,
             "tags": {"building_id": "building"}}
    assert solar_building_match(hotel, baseline) == (True, "existing_floor_count_and_building_id")
    assert not solar_building_match({**hotel, "floors": None}, baseline)[0]
    hotel = {**hotel, "lon": 39.7202, "floors": None}
    projected_point = Point(TO_UTM.transform(hotel["lon"], hotel["lat"]))
    distance = metric(footprint).distance(projected_point)
    baseline["building_match"] = {"method": "nearest_30m", "distance_m": distance}
    assert solar_building_match(hotel, baseline) == (True, "conditional_nearest_30m")
    baseline["building_match"].pop("distance_m")
    assert solar_building_match(hotel, baseline) == (False, "unverified_nearest_match")
    baseline["building_match"]["distance_m"] = distance
    baseline["facades"][0]["confidence"] = 1
    assert solar_building_match(hotel, baseline) == (False, "unverified_nearest_match")


@pytest.fixture
def solar_artifacts(tmp_path):
    directory = tmp_path/"geo"
    directory.mkdir()
    hotel = {"id": "test", "lon": 39.72, "lat": 43.58, "floors": 2,
             "tags": {"building_id": "test-building"}}
    baseline = {"hotel_id": "test", "computed_at": "2026-10-26T00:00:00Z", "synthetic": True,
                "building_id": "test-building", "building_match": {"method": "id", "distance_m": 0},
                "building": {"id": "test-building", "geometry": mapping(box(39.7199, 43.5799, 39.7201, 43.5801))},
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
    associations = [claim.evidence[0].inputs["building_association"] for claim in claims]
    assert all(a["result"] == "existing_floor_count_and_building_id" for a in associations)
    assert all(a["hotel_floor_count"] == a["contour_floor_count"] == 2 for a in associations)
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


def test_solar_adapter_does_not_publish_exact_floors_from_boundary_contains(solar_artifacts):
    hotel, directory, baseline = solar_artifacts
    hotel.update(lon=39.7201, floors=None)
    baseline["building_match"]["method"] = "contains"
    (directory/"geo"/"test.json").write_text(json.dumps(baseline))
    for characteristic in ("morning_sun", "evening_heat"):
        result = compute_trait(hotel, directory, characteristic)
        assert result["claims"] == []
        assert "boundary_or_outside" in result["warnings"][0]


def test_conditional_solar_evidence_does_not_turn_contour_floors_into_hotel_floors(solar_artifacts):
    hotel, directory, baseline = solar_artifacts
    hotel.update(lon=39.7202, floors=None)
    footprint = shape(baseline["building"]["geometry"])
    point = Point(TO_UTM.transform(hotel["lon"], hotel["lat"]))
    baseline["building_match"] = {"method": "nearest_30m", "distance_m": metric(footprint).distance(point)}
    (directory/"geo"/"test.json").write_text(json.dumps(baseline))
    result = compute_trait(hotel, directory, "morning_sun", month=9)
    assert result["claims"]
    for claim in result["claims"]:
        evidence = claim["evidence"][0]
        association = evidence["inputs"]["building_association"]
        assert association["result"] == "conditional_nearest_30m"
        assert association["floor_domain"] == "contour_conditional"
        assert association["contour_floor_count"] == 2
        assert "hotel_floor_count" not in association
        assert any("соответствие отелю не подтверждено, этажи условны" in item for item in evidence["limitations"])


def test_explicit_changed_artifact_never_falls_back_to_clear_baseline(solar_artifacts):
    hotel, directory, baseline = solar_artifacts
    changed = directory/"geo"/"test-change-example.json"
    changed.write_text(json.dumps(baseline))
    # Compact cached variants use fN_horizon rather than facade_N_horizon.
    np.savez_compressed(changed.with_suffix(".npz"), f0_horizon=np.full((2, 120), 90),
                        f1_horizon=np.full((2, 120), 90))
    result = compute_trait(hotel, directory, "morning_sun", month=9, geo_artifact=changed)
    assert result["claims"] and all(not c["value"]["available"] for c in result["claims"])
    assert all(c["evidence"][0]["artifact"].endswith("test-change-example.json") for c in result["claims"])
    missing = compute_trait(hotel, directory, "morning_sun", geo_artifact="/data/geo/test-change-missing.json")
    assert missing["claims"] == [] and missing["warnings"]
    unsupported_rail = compute_trait(hotel, directory, "railway_noise", geo_artifact=changed)
    assert unsupported_rail["claims"] == [] and unsupported_rail["warnings"]
    with pytest.raises(ValueError):
        compute_trait(hotel, directory, "morning_sun", geo_artifact=directory/"outside.json")


def test_materializer_skips_active_change_without_supported_artifact(solar_artifacts):
    from pipeline.compute_traits import materialize
    hotel, directory, _ = solar_artifacts
    seed = directory/"seed"
    seed.mkdir()
    (seed/"hotels.json").write_text(json.dumps([hotel]))
    (seed/"demo_change_event.json").write_text(json.dumps({"hotel_id": hotel["id"], "geo": {}}))
    report = materialize(directory, directory/"output", characteristics=["morning_sun"])
    assert report["counts"]["morning_sun"]["claims"] == 0
    assert report["warnings"]["morning_sun"]


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


def test_beach_surface_uses_walk_destination_instead_of_mixing_beaches(monkeypatch, tmp_path):
    hotel = {"id": "test", "lon": 39.72, "lat": 43.58}
    x, y = TO_UTM.transform(hotel["lon"], hotel["lat"])
    nearest = feature("sand", box(x+100, y-20, x+200, y+20), natural="beach", surface="sand")
    routed = feature("pebbles", box(x+400, y-20, x+500, y+20), natural="beach", surface="pebblestone")
    region = FakeRegion({"beaches": [nearest, routed]})
    monkeypatch.setattr("geo.traits.get_region", lambda _: region)
    geo = tmp_path/"geo"
    geo.mkdir()
    (geo/"test.json").write_text(json.dumps({"hotel_id": "test", "route_details": {"beach_id": "pebbles"}}))
    value = compute_trait(hotel, tmp_path, "beach_surface")["claims"][0]["value"]
    assert value["surface"] == "pebblestone" and value["beach_id"] == "pebbles"
    assert value["selection_kind"] == "beach_walk_destination"
    region.features["beaches"] = [nearest]
    assert compute_trait(hotel, tmp_path, "beach_surface")["claims"] == []


def test_railway_proxy_has_no_road_contribution_and_no_false_quiet(monkeypatch, tmp_path):
    hotel = {"id": "test", "lon": 39.72, "lat": 43.58}
    x, y = TO_UTM.transform(hotel["lon"], hotel["lat"])
    own = feature("own", box(x-10, y-10, x+10, y+10), source_id="overture_buildings", synthetic=True)
    rail = feature("rail", LineString([(x-50, y-250), (x-50, y+250)]), railway="rail")
    road = feature("road", LineString([(x-20, y-250), (x-20, y+250)]), highway="motorway")
    region = FakeRegion({"roads": [road], "railways": [rail]}, own)
    monkeypatch.setattr("geo.traits.get_region", lambda _: region)
    rows = compute_trait(hotel, tmp_path, "railway_noise")["claims"]
    assert rows and all(row["confidence"] <= .38 for row in rows)
    assert all(row["evidence"][0]["source_ids"] == ["osm_geofabrik", "overture_buildings"] for row in rows)
    assert all(row["synthetic"] and row["evidence"][0]["kind"] == "simulation" for row in rows)
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
