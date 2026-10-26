"""Exact source identities for small map markers; all coordinates are fixtures."""
import gzip
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from shapely.geometry import Point, shape

from backend.nearby import linked_places


def place(identity, coordinates=(39.7, 43.6), *, geometry=None, **properties):
    return {"type": "Feature", "id": identity,
            "geometry": geometry or {"type": "Point", "coordinates": list(coordinates)},
            "properties": {"osm_id": identity, "source_id": "osm_geofabrik", **properties}}


def snapshot(root, dataset, features, *, compressed=False):
    path = root/"seed"/(dataset+".geojson"+(".gz" if compressed else ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps({"type": "FeatureCollection", "features": features}).encode()
    path.write_bytes(gzip.compress(content) if compressed else content)
    return path


def fact(cid, value, *, identity="fact-1", **updates):
    return {"id": identity, "hotel_id": "hotel-1", "characteristic_id": cid,
            "status": "computed", "confidence": .7, "value": value,
            "valid_until": (datetime.now(UTC)+timedelta(days=1)).isoformat(),
            "evidence": [{"source_ids": ["geo_engine", "osm_geofabrik"]}], **updates}


def markers(root, facts, geo=None):
    result = linked_places(facts, {"hotel_id": "hotel-1", **(geo or {})}, root)
    assert result["type"] == "FeatureCollection"
    return result["features"]


def test_exact_fact_id_wins_over_a_closer_unlinked_object(tmp_path):
    snapshot(tmp_path, "pois", [place("osm-node-1", (39.7, 43.6), amenity="pharmacy", name="Ближе"),
                                place("osm-node-2", (39.8, 43.7), amenity="pharmacy", name="Связанная")])
    row = fact("night_pharmacy", {"osm_id": "osm-node-2", "distance_m": 140})
    [marker] = markers(tmp_path, [row])
    assert marker["geometry"] == {"type": "Point", "coordinates": [39.8, 43.7]}
    assert marker["properties"].items() >= {"kind": "pharmacy", "name": "Связанная", "osm_id": "osm-node-2",
                                            "characteristic_id": "night_pharmacy", "fact_id": "fact-1",
                                            "coordinate_source": "osm_point", "distance_m": 140,
                                            "distance_kind": "straight_to_geometry"}.items()
    assert marker["properties"]["source_ids"] == ["geo_engine", "osm_geofabrik"]
    assert marker["properties"]["opening_hours_verified"] is False
    assert not ({"minutes", "opening_hours", "available"} & marker["properties"].keys())


def test_all_five_links_export_only_point_geometry_and_source_references(tmp_path):
    snapshot(tmp_path, "beaches", [place("osm-way-1", natural="beach")])
    snapshot(tmp_path, "pois", [place("osm-node-2", amenity="pharmacy"),
                                place("osm-node-3", amenity="parking"),
                                place("osm-node-4", leisure="playground"),
                                place("osm-node-5", leisure="garden")])
    rows = [fact("beach_walk", {"beach_id": "osm-way-1"}),
            fact("night_pharmacy", {"osm_id": "osm-node-2"}),
            fact("car_access", {"parking_osm_id": "osm-node-3"}),
            fact("family_nearby", {"playground_osm_id": "osm-node-4"}),
            fact("green_nearby", {"osm_id": "osm-node-5"})]
    result = markers(tmp_path, rows)
    assert [f["properties"]["kind"] for f in result] == ["beach", "pharmacy", "parking", "playground", "park"]
    assert all(f["geometry"]["type"] == "Point" and f["properties"]["source_ids"] for f in result)
    assert len({f["properties"]["osm_id"] for f in result}) == 5


@pytest.mark.parametrize("value", [{"distance_m": 20}, {"osm_id": "missing", "distance_m": 20},
                                   {"osm_id": ["osm-node-1"]}])
def test_missing_or_bad_id_never_uses_nearest_geometry(tmp_path, value):
    snapshot(tmp_path, "pois", [place("osm-node-1", amenity="pharmacy")])
    assert markers(tmp_path, [fact("night_pharmacy", value)]) == []


def test_beach_falls_back_only_to_the_actual_saved_route_destination(tmp_path):
    snapshot(tmp_path, "beaches", [place("osm-way-1", natural="beach"), place("osm-way-2", natural="beach")])
    route = {"route_details": {"beach_id": "osm-way-2"}}
    [marker] = markers(tmp_path, [fact("beach_walk", {"minutes": 7, "distance_m": 800})], route)
    assert marker["properties"]["osm_id"] == "osm-way-2"
    assert "distance_m" not in marker["properties"]  # Route length is not straight-line distance.
    assert markers(tmp_path, [], route) == []
    assert markers(tmp_path, [fact("beach_walk", {})]) == []
    assert markers(tmp_path, [fact("beach_walk", {"beach_id": "missing"})], route) == []
    [explicit] = markers(tmp_path, [fact("beach_walk", {"beach_id": "osm-way-1"})], route)
    assert explicit["properties"]["osm_id"] == "osm-way-1"


@pytest.mark.parametrize("status", ["unknown", "stale", "disputed", "invalid", None, {}])
def test_noncurrent_status_cannot_create_a_marker(tmp_path, status):
    snapshot(tmp_path, "pois", [place("osm-node-1", amenity="pharmacy")])
    assert markers(tmp_path, [fact("night_pharmacy", {"osm_id": "osm-node-1"}, status=status)]) == []


@pytest.mark.parametrize("deadline", [None, "", "not-a-date", "2026-99-99T00:00:00Z",
                                      "2000-01-01T00:00:00Z", "2099-01-01T00:00:00", 7, {}])
def test_expired_missing_naive_and_malformed_ttl_are_safe(tmp_path, deadline):
    snapshot(tmp_path, "pois", [place("osm-node-1", amenity="pharmacy")])
    assert markers(tmp_path, [fact("night_pharmacy", {"osm_id": "osm-node-1"}, valid_until=deadline)]) == []


def test_confirmed_fact_and_aware_datetime_ttl_are_supported(tmp_path):
    snapshot(tmp_path, "pois", [place("osm-node-1", amenity="pharmacy")])
    assert len(markers(tmp_path, [fact("night_pharmacy", {"osm_id": "osm-node-1"}, status="confirmed",
                                      valid_until=datetime.now(UTC)+timedelta(days=1))])) == 1
    assert markers(tmp_path, [fact("night_pharmacy", {"osm_id": "osm-node-1"}, hotel_id="other")]) == []


@pytest.mark.parametrize(("cid", "value", "tags"), [
    ("night_pharmacy", {"osm_id": "osm-node-1"}, {"amenity": "cafe"}),
    ("car_access", {"parking_osm_id": "osm-node-1"}, {"amenity": "parking", "access": "private"}),
    ("car_access", {"parking_osm_id": "osm-node-1"}, {"amenity": "parking", "access": "no"}),
    ("family_nearby", {"playground_osm_id": "osm-node-1"}, {"leisure": "park"}),
    ("green_nearby", {"osm_id": "osm-node-1"}, {"leisure": "playground"}),
    ("green_nearby", {"osm_id": "osm-node-1"}, {"leisure": "park", "access": "private"}),
    ("beach_walk", {"beach_id": "osm-node-1"}, {"natural": "bay"}),
])
def test_mismatched_tags_and_private_parking_are_not_promoted(tmp_path, cid, value, tags):
    snapshot(tmp_path, "beaches" if cid == "beach_walk" else "pois", [place("osm-node-1", **tags)])
    assert markers(tmp_path, [fact(cid, value)]) == []


def test_polygon_marker_is_inside_geometry_and_explicitly_not_an_entrance(tmp_path):
    # Concave U: its centroid lies outside, so a centroid is not a valid substitute.
    polygon = {"type": "Polygon", "coordinates": [[[39, 43], [39.004, 43], [39.004, 43.004],
                [39.003, 43.004], [39.003, 43.001], [39.001, 43.001], [39.001, 43.004],
                [39, 43.004], [39, 43]]]}
    snapshot(tmp_path, "beaches", [place("osm-way-1", geometry=polygon, natural="beach")], compressed=True)
    [marker] = markers(tmp_path, [fact("beach_walk", {"beach_id": "osm-way-1"})])
    source = shape(polygon)
    assert source.covers(Point(marker["geometry"]["coordinates"])) and not source.covers(source.centroid)
    assert marker["properties"]["coordinate_source"] == "osm_geometry_representative_point"
    assert marker["properties"]["coordinate_note"] == "Точка геометрии, не вход"
    assert marker["properties"]["entrance_verified"] is False
    assert marker["properties"]["access_verified"] is False


@pytest.mark.parametrize("geometry", [None, {}, {"type": "Point", "coordinates": [181, 43]},
    {"type": "Point", "coordinates": [39, 91]}, {"type": "Point", "coordinates": [float("nan"), 43]},
    {"type": "Point", "coordinates": [True, 43]}, {"type": "Point", "coordinates": ["39", 43]},
    {"type": "Point", "coordinates": []}, {"type": [], "coordinates": [39, 43]},
    {"type": "LineString", "coordinates": [[39, 43], [39.1, 43.1]]},
    {"type": "Polygon", "coordinates": [[[39, 43], [181, 43], [39, 44], [39, 43]]]},
    {"type": "Polygon", "coordinates": [[[39, 43], [40, 44], [39, 44], [40, 43], [39, 43]]]},
])
def test_invalid_coordinates_and_geometry_types_never_leak(tmp_path, geometry):
    feature = place("osm-node-1", amenity="pharmacy")
    feature["geometry"] = geometry
    snapshot(tmp_path, "pois", [feature])
    assert markers(tmp_path, [fact("night_pharmacy", {"osm_id": "osm-node-1"})]) == []


def test_cache_refreshes_when_snapshot_replaced_with_same_size_and_mtime(tmp_path):
    path = snapshot(tmp_path, "pois", [place("osm-node-1", (39.1, 43.1), amenity="pharmacy")])
    rows = [fact("night_pharmacy", {"osm_id": "osm-node-1"})]
    assert markers(tmp_path, rows)[0]["geometry"]["coordinates"] == [39.1, 43.1]
    old = path.stat()
    replacement = path.with_name("replacement.json")
    replacement.write_text(path.read_text().replace("39.1", "39.2"))
    os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
    replacement.replace(path)
    assert path.stat().st_size == old.st_size and path.stat().st_mtime_ns == old.st_mtime_ns
    changed = markers(tmp_path, rows)
    assert changed[0]["geometry"]["coordinates"] == [39.2, 43.1]
    changed[0]["geometry"]["coordinates"][0] = 0
    assert markers(tmp_path, rows)[0]["geometry"]["coordinates"] == [39.2, 43.1]


def test_plain_snapshot_precedes_compressed_and_broken_snapshots_are_empty(tmp_path):
    snapshot(tmp_path, "pois", [place("osm-node-1", (39.1, 43.1), amenity="pharmacy")], compressed=True)
    rows = [fact("night_pharmacy", {"osm_id": "osm-node-1"})]
    assert markers(tmp_path, rows)[0]["geometry"]["coordinates"] == [39.1, 43.1]
    plain = snapshot(tmp_path, "pois", [place("osm-node-1", (39.2, 43.2), amenity="pharmacy")])
    assert markers(tmp_path, rows)[0]["geometry"]["coordinates"] == [39.2, 43.2]
    plain.write_text("broken JSON")
    assert markers(tmp_path, rows) == []  # Never silently use the older gz beside it.


def test_one_per_category_and_exact_identity_deduplication(tmp_path):
    snapshot(tmp_path, "pois", [place("osm-node-1", amenity="parking", leisure="park"),
                                place("osm-node-2", leisure="garden"),
                                place("osm-node-3", amenity="pharmacy"),
                                place("osm-node-4", amenity="pharmacy")])
    rows = [fact("car_access", {"parking_osm_id": "osm-node-1"}, identity="car"),
            fact("green_nearby", {"osm_id": "osm-node-1"}, identity="green-1"),
            fact("green_nearby", {"osm_id": "osm-node-2"}, identity="green-2", confidence=.6),
            fact("night_pharmacy", {"osm_id": "osm-node-3"}, identity="pharmacy-1", confidence=.4),
            fact("night_pharmacy", {"osm_id": "osm-node-4"}, identity="pharmacy-2", confidence=.8)]
    result = markers(tmp_path, rows)
    assert {f["properties"]["kind"]: f["properties"]["osm_id"] for f in result} == {
        "parking": "osm-node-1", "park": "osm-node-2", "pharmacy": "osm-node-4"}


def test_duplicate_or_inconsistent_source_ids_are_ambiguous(tmp_path):
    snapshot(tmp_path, "pois", [place("osm-node-1", amenity="pharmacy"),
                                place("osm-node-1", (39.8, 43.8), amenity="pharmacy")])
    rows = [fact("night_pharmacy", {"osm_id": "osm-node-1"})]
    assert markers(tmp_path, rows) == []
    mismatched = place("osm-node-1", amenity="pharmacy")
    mismatched["properties"]["osm_id"] = {"not": "an ID"}
    snapshot(tmp_path, "pois", [mismatched])
    assert markers(tmp_path, rows) == []


@pytest.mark.parametrize("distance", [-1, True, "12", float("inf"), float("nan")])
def test_invalid_distance_is_omitted_without_inventing_a_measurement(tmp_path, distance):
    snapshot(tmp_path, "pois", [place("osm-node-1", amenity="pharmacy")])
    [marker] = markers(tmp_path, [fact("night_pharmacy", {"osm_id": "osm-node-1", "distance_m": distance})])
    assert "distance_m" not in marker["properties"]


def test_explicit_route_distance_is_not_relabelled_as_straight_line(tmp_path):
    snapshot(tmp_path, "pois", [place("osm-node-1", amenity="pharmacy")])
    [marker] = markers(tmp_path, [fact("night_pharmacy", {"osm_id": "osm-node-1", "distance_m": 300,
                                                        "distance_kind": "walking_route"})])
    assert "distance_m" not in marker["properties"]


def test_names_remain_plain_strings_and_no_other_source_properties_escape(tmp_path):
    name = '<img src=x onerror="alert(1)">'
    snapshot(tmp_path, "pois", [place("osm-node-1", amenity="pharmacy", name=name,
                                      opening_hours="24/7", phone="not-exported")])
    [marker] = markers(tmp_path, [fact("night_pharmacy", {"osm_id": "osm-node-1"})])
    assert marker["properties"]["name"] == name and isinstance(marker["properties"]["name"], str)
    assert "opening_hours" not in marker["properties"] and "phone" not in marker["properties"]
