"""Versioned raw POI distances preserve values, ordering and bootstrap history."""
import gzip
import json
import os
from unittest.mock import patch

import pytest
from shapely.geometry import Point

import backend.db.repository as db
from backend.contracts import CharacteristicSpec, ToolCall, ToolContext
from backend.seed import bootstrap_poi
from backend.tests.dbapi_fakes import FakeDatabase
from backend.tests.test_api_integration import ApiRepo
from backend.tools.registry import POI_NEARBY_CACHE_SIZE, Registry
from geo.coordinates import TO_UTM

HOTEL = {"id": "test-hotel", "name": "Synthetic POI hotel", "lon": 39.72, "lat": 43.58}


def point(identity, latitude=43.58, **properties):
    return {"type": "Feature", "id": identity,
            "properties": {"observed_at": "2026-10-25T21:21:21Z", "identity": identity, **properties},
            "geometry": {"type": "Point", "coordinates": [39.72, latitude]}}


def write(path, features, *, compressed=False):
    seed = path / "seed"
    seed.mkdir(parents=True, exist_ok=True)
    target = seed / ("pois.geojson.gz" if compressed else "pois.geojson")
    payload = json.dumps({"type": "FeatureCollection", "features": features}).encode()
    target.write_bytes(gzip.compress(payload) if compressed else payload)
    return target


def registry(path):
    repo = ApiRepo()
    repo.upsert("hotels", HOTEL)
    return Registry(repo, data_dir=path)


def center():
    return Point(*TO_UTM.transform(HOTEL["lon"], HOTEL["lat"]))


def test_raw_cache_keeps_predicates_independent_and_avoids_repeated_distance_calls(tmp_path):
    write(tmp_path, [point("parking", amenity="parking"), point("pharmacy", 43.581, amenity="pharmacy")])
    tool = registry(tmp_path)
    original = Point.distance
    calls = []
    def distance(self, other):
        calls.append(1)
        return original(self, other)
    with patch("shapely.geometry.base.BaseGeometry.distance", distance):
        pharmacy = tool._nearby("pois", center(), 500, lambda properties: properties.get("amenity") == "pharmacy")
        first_calls = len(calls)
        parking = tool._nearby("pois", center(), 500, lambda properties: properties.get("amenity") == "parking")
        assert len(calls) == first_calls == 2
    assert [row[1]["id"] for row in pharmacy] == ["pharmacy"]
    assert [row[1]["id"] for row in parking] == ["parking"]
    parking.clear()
    assert len(tool._nearby("pois", center(), 500)) == 2


def test_predicate_order_and_stable_distance_ties_match_original_query_order(tmp_path):
    write(tmp_path, [point("a"), point("b"), point("c")])
    tool = registry(tmp_path)
    first_order = []
    first = tool._nearby("pois", center(), 500, lambda properties: first_order.append(properties["identity"]) or True)
    repeated_order = []
    repeated = tool._nearby("pois", center(), 500, lambda properties: repeated_order.append(properties["identity"]) or True)
    assert repeated_order == first_order == [row[1]["id"] for row in first]
    assert repeated == first and len(first) == 3


def test_same_size_restored_mtime_rewrite_invalidates_index_and_raw_cache(tmp_path):
    target = write(tmp_path, [point("location", 43.58)])
    tool = registry(tmp_path)
    assert tool._nearby("pois", center(), 500)
    previous = target.stat()
    write(tmp_path, [point("location", 43.59)])
    assert target.stat().st_size == previous.st_size
    os.utime(target, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert tool._nearby("pois", center(), 500) == []


def test_gzip_plain_precedence_removal_and_reappearance_are_versioned(tmp_path):
    compressed = write(tmp_path, [point("gzip")], compressed=True)
    tool = registry(tmp_path)
    assert tool._nearby("pois", center(), 500)[0][1]["id"] == "gzip"
    plain = write(tmp_path, [point("plain")])
    assert tool._nearby("pois", center(), 500)[0][1]["id"] == "plain"
    plain.unlink()
    assert tool._nearby("pois", center(), 500)[0][1]["id"] == "gzip"
    compressed.unlink()
    assert tool._nearby("pois", center(), 500) is None
    write(tmp_path, [point("new")])
    assert tool._nearby("pois", center(), 500)[0][1]["id"] == "new"


def test_broken_replacement_does_not_reuse_previous_spatial_result(tmp_path):
    target = write(tmp_path, [point("valid")])
    tool = registry(tmp_path)
    assert tool._nearby("pois", center(), 500)
    target.write_text("broken input")
    with pytest.raises(ValueError):
        tool._nearby("pois", center(), 500)


def test_nearby_cache_is_bounded_and_evicted_centers_are_recomputed(tmp_path):
    write(tmp_path, [point("fixed")])
    tool = registry(tmp_path)
    xy = center()
    first = tool._nearby("pois", xy, 500)
    for index in range(1, POI_NEARBY_CACHE_SIZE+2):
        tool._nearby("pois", Point(xy.x+index, xy.y), 500)
    assert len(tool._nearby_cache) == POI_NEARBY_CACHE_SIZE
    assert tool._nearby("pois", xy, 500) == first


def test_narrow_reader_uses_bound_characteristics_literal_method_prefix_and_no_revision(monkeypatch):
    row = {"id": "previous", "method": "local_poi_lookup@2", "characteristic_id": "car_access"}
    connection = FakeDatabase()
    connection.query_handler = lambda *_: [(row,)]
    calls = connection.calls
    monkeypatch.setattr(db.psycopg, "connect", connection.connect)
    repo = db.Repository("unused")
    assert repo.prior_poi_claims([]) == [] and not calls
    assert repo.prior_poi_claims(["car_access", "car_access"]) == [row]
    assert calls[0][1] == (["car_access"], "local_poi_")
    assert "=ANY(%s)" in calls[0][0] and "starts_with" in calls[0][0]
    assert repo.search_generation() == 0


@pytest.mark.parametrize("narrow_reader", [False, True])
def test_bootstrap_requests_only_declared_poi_history_and_keeps_method_supersession(tmp_path, narrow_reader):
    write(tmp_path, [point("parking", amenity="parking")])
    repo = ApiRepo()
    repo.upsert("hotels", HOTEL)
    car = repo.get("characteristics", "car_access")
    repo.tables["characteristics"] = {"car_access": car}
    context = ToolContext(hotel_id=HOTEL["id"], characteristic_id="car_access", run_id="initial")
    [current] = Registry(repo, data_dir=tmp_path).execute(context, ToolCall(tool="poi_nearby", params={"kind": "car"})).claims
    legacy = current.model_copy(update={"id": "legacy", "method": "local_poi_lookup@2"})
    repo.add_claim(legacy)
    irrelevant = current.model_copy(update={"id": "unrelated", "characteristic_id": "unplanned_car", "method": "local_poi_lookup@2"})
    repo.add_claim(irrelevant)
    old_all = repo.all
    read_scopes = []
    if narrow_reader:
        def reader(characteristic_ids):
            read_scopes.append(characteristic_ids)
            return [legacy.model_dump(mode="json")]
        repo.prior_poi_claims = reader
        def all_except_claims(table):
            assert table != "claims"
            return old_all(table)
        repo.all = all_except_claims
    bootstrap_poi(repo, tmp_path)
    rows = old_all("claims")
    [new] = [row for row in rows if row["id"] not in {"legacy", "unrelated"}]
    assert new["id"] == current.id and new["method"] == current.method
    assert new["value"] == current.value and new["confidence"] == current.confidence
    assert new["supersedes_ids"] == ["legacy"]
    assert next(row for row in rows if row["id"] == "legacy") == legacy.model_dump(mode="json")
    assert next(row for row in rows if row["id"] == "unrelated") == irrelevant.model_dump(mode="json")
    if narrow_reader:
        assert read_scopes == [["car_access"]]


def test_no_declared_poi_plan_does_not_load_any_claims(tmp_path):
    repo = ApiRepo()
    original = CharacteristicSpec.model_validate(repo.get("characteristics", "sea_view"))
    repo.tables["characteristics"] = {original.id: original.model_dump(mode="json")}
    old_all = repo.all
    def without_claims(table):
        assert table != "claims"
        return old_all(table)
    repo.all = without_claims
    bootstrap_poi(repo, tmp_path)
