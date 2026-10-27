"""Known-answer topology, immutable inputs and address-only optimistic seed updates."""
import copy
import gzip
import hashlib
import json
import shutil
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from shapely.geometry import Point, box, mapping, shape

from backend.addresses import (
    MAX_ADDRESS_BYTES,
    apply_address_supplement,
    load_supplement,
    projected_hotel,
    validate_document,
)
from backend.contracts import Source
from pipeline.enrich_addresses import (
    DISTRICT_SOURCE,
    DISTRICTS,
    enrich,
    output_metadata,
    run,
    snapshot_from_cache,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]


def source(identifier="osm_geofabrik"):
    received = datetime.now(UTC) - timedelta(days=1)
    return Source(id=identifier, name=identifier, kind="open_data", url="https://www.openstreetmap.org/",
                  license="ODbL-1.0", attribution="© OpenStreetMap contributors", observed_at="2020-01-01T00:00:00Z",
                  retrieved_at=received).model_dump(mode="json")


def hotel(identifier="h", *, address="Сочи", **updates):
    return {"id": identifier, "name": identifier, "lat": .5, "lon": .5, "address": address,
            "source_ids": ["osm_geofabrik"], "tags": {"building_id": "osm-way-1"},
            "price_rub": None, "room_types": [], **updates}


def building(*, geometry=None, properties=None, identifier="osm-way-1"):
    return {"type": "Feature", "id": identifier, "geometry": mapping(geometry if geometry is not None else box(0, 0, 1, 1)),
            "properties": {"source_id": "osm_geofabrik", "addr:street": "Тестовая улица", "addr:housenumber": "7А",
                           **(properties or {})}}


def snapshot(*, overlap=False):
    admin = source(DISTRICT_SOURCE)
    features = [{"type": "Feature", "id": "osm-relation-1116490", "geometry": mapping(box(-2, -2, 2, 2)),
                 "properties": {"osm_id": 1116490, "name": "Полное название района", "display_name": "Центральный район",
                                "source_url": "https://www.openstreetmap.org/relation/1116490", "retrieved_at": admin["retrieved_at"]}}]
    if overlap:
        other = copy.deepcopy(features[0])
        other["id"], other["properties"]["osm_id"] = "osm-relation-907728", 907728
        features.append(other)
    return {"type": "FeatureCollection", "schema_version": 1, "source": admin, "features": features}


def document(hotels=None, buildings=None, districts=None):
    return enrich(hotels if hotels is not None else [hotel()], buildings if buildings is not None else [building()],
                  [source()], districts if districts is not None else snapshot(), region="test")


def test_linked_osm_polygon_must_contain_point_and_no_nearest_address_is_used():
    outside = building(geometry=box(1, 1, 2, 2))
    nearby = building(identifier="osm-way-2")
    result = document(buildings=[outside, nearby])
    [entry] = result["entries"]
    assert entry["precision"] == "area" and entry["address"] == "Сочи, Центральный район"
    assert result["counts"]["linked_building_outside_or_invalid"] == 1
    assert not any("building_id" in ev["inputs"] for ev in entry["evidence"])


def test_boundary_touch_is_not_strict_containment():
    result = document(hotels=[hotel(lon=1)])
    assert result["entries"][0]["precision"] == "area"


def test_invalid_linked_geometry_falls_back_to_district_without_address_guess():
    invalid = building()
    invalid["geometry"] = None
    result = document(buildings=[invalid])
    assert result["entries"][0]["precision"] == "area"
    assert result["counts"]["linked_building_outside_or_invalid"] == 1


def test_containment_uses_exact_source_street_and_house_and_receipt_time():
    original = hotel()
    sources = [source()]
    result = enrich([original], [building()], sources, snapshot(), region="test")
    [entry] = result["entries"]
    assert entry["address"] == "Сочи, Тестовая улица, 7А, Центральный район" and entry["precision"] == "building"
    assert entry["observed_at"] == sources[0]["retrieved_at"]
    assert entry["observed_at"] != sources[0]["observed_at"]
    witness = next(ev for ev in entry["evidence"] if ev["inputs"].get("building_id"))
    assert witness["inputs"]["addr:street"] == "Тестовая улица" and witness["inputs"]["addr:housenumber"] == "7А"
    assert witness["url"] == "https://www.openstreetmap.org/way/1"
    assert original == hotel()


@pytest.mark.parametrize("properties,precision", [({"addr:street": None}, "area"), ({"addr:housenumber": None}, "street")])
def test_missing_tags_never_invent_street_or_house(properties, precision):
    entry = document(buildings=[building(properties=properties)])["entries"][0]
    assert entry["precision"] == precision and "None" not in entry["address"]
    if precision == "street":
        assert "7А" not in entry["address"]


def test_no_building_binding_gets_only_a_real_district():
    assert document(hotels=[hotel(tags={})])["entries"][0]["precision"] == "area"


def test_district_source_does_not_impersonate_missing_original_hotel_source():
    [entry] = document(hotels=[hotel(tags={}, source_ids=[])])["entries"]
    assert entry["precision"] == "area" and entry["source"] == DISTRICT_SOURCE
    assert len(entry["evidence"]) == 1
    assert entry["evidence"][0]["inputs"]["district_id"] == "osm-relation-1116490"
    assert entry["evidence"][0]["artifact"] != "/data/seed/hotels.json"


def test_existing_address_prices_rooms_and_other_payload_fields_are_preserved():
    original = hotel(address="Сочи, Исходная улица, 9", price_rub=12345,
                     room_types=[{"id": "owner-room", "name": "Owner room"}], owner_field={"keep": [1, 2]})
    [entry] = document(hotels=[original])["entries"]
    assert entry["precision"] == "source" and entry["address"] == original["address"]
    changed = projected_hotel(original, entry)
    assert changed["address"] == original["address"]
    for key in original:
        if key != "tags":
            assert changed[key] == original[key]
    assert changed["tags"]["building_id"] == original["tags"]["building_id"]
    assert changed["tags"]["address_district"] == "Центральный район"
    assert len(json.dumps(changed["tags"]["address_evidence"])) < 2000
    assert "geometry" not in changed["tags"]["address_evidence"]


def test_other_regions_and_overlaps_remain_unknown_instead_of_nearest():
    outside = enrich([hotel(tags={})], [], [source()], None, region="other")
    assert outside["entries"][0]["address"] == "Сочи" and outside["entries"][0]["district"] is None
    assert outside["entries"][0]["confidence"] == 0
    ambiguous = document(hotels=[hotel(tags={})], districts=snapshot(overlap=True))
    assert ambiguous["entries"][0]["address"] == "Сочи" and ambiguous["counts"]["district_ambiguous"] == 1


@pytest.mark.parametrize("updates", [{"id": "different"}, {"lat": .6}, {"lon": .6}, {"address": "Ручной адрес"}])
def test_runtime_id_coordinates_and_owner_address_edits_are_never_overwritten(updates):
    [entry] = document()["entries"]
    assert projected_hotel(hotel(**updates), entry) is None


def test_previous_projection_is_refreshable_but_later_manual_edit_is_not():
    [entry] = document()["entries"]
    projected = projected_hotel(hotel(), entry)
    assert projected_hotel(projected, entry) is None
    newer = copy.deepcopy(entry)
    newer["address"] = "Сочи, Тестовая улица, 7Б, Центральный район"
    assert projected_hotel(projected, newer)["address"] == newer["address"]
    projected["address"] = "Адрес владельца"
    assert projected_hotel(projected, newer) is None


def test_expired_projection_is_not_applied():
    [entry] = document()["entries"]
    later = datetime.fromisoformat(entry["valid_until"]) + timedelta(seconds=1)
    assert projected_hotel(hotel(), entry, at=later) is None


def test_sidecar_validation_binds_base_address_and_coordinates():
    original = document()
    for key, value in [("hotel_id", "other"), ("lat", .6), ("base_address", "Чужой исходный адрес")]:
        altered = copy.deepcopy(original)
        altered["entries"][0][key] = value
        with pytest.raises(ValueError, match="binding|hotel id"):
            validate_document(altered, [hotel()])


def test_sidecar_validation_requires_complete_bounded_provenance():
    original = document()
    for key in ("base_address", "address", "source", "observed_at"):
        altered = copy.deepcopy(original)
        del altered["entries"][0][key]
        with pytest.raises(ValueError, match="Incomplete"):
            validate_document(altered, [hotel()])
    altered = copy.deepcopy(original)
    altered["entries"][0]["evidence"][0]["source_ids"] = []
    with pytest.raises(ValueError, match="source_ids|evidence source"):
        validate_document(altered, [hotel()])
    altered = copy.deepcopy(original)
    altered["entries"][0]["evidence"][0]["inputs"]["oversized"] = "x" * 20_000
    with pytest.raises(ValueError, match="oversized"):
        validate_document(altered, [hotel()])


def write_fixture(tmp_path, *, hotels=None):
    seed = tmp_path / "data/seed"
    seed.mkdir(parents=True)
    hotels = hotels if hotels is not None else [hotel()]
    payload = document(hotels=hotels)
    write_json(seed / "hotel_addresses.json", payload)
    write_json(seed / "hotels.json", hotels)
    return seed.parent, payload


def test_optional_sidecar_and_bounded_read_reject_before_connecting(tmp_path):
    def fail(*args, **kwargs):
        pytest.fail("unexpected database connection")
    assert apply_address_supplement(tmp_path, "fixture", connection_factory=fail)["absent"]
    data, _ = write_fixture(tmp_path)
    (data / "seed/hotel_addresses.json").write_bytes(b"x" * (MAX_ADDRESS_BYTES + 1))
    with pytest.raises(ValueError, match="exceeds"):
        apply_address_supplement(data, "fixture", connection_factory=fail)


class Cursor:
    def __init__(self, rows=(), rowcount=0):
        self.rows, self.rowcount = rows, rowcount

    def __iter__(self):
        return iter(self.rows)


class Database:
    def __init__(self, hotels, *, race=False):
        self.hotels = {row["id"]: copy.deepcopy(row) for row in hotels}
        self.sources, self.order, self.active, self.race = {}, [], False, race

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @contextmanager
    def transaction(self):
        self.active = True
        yield
        self.active = False

    def execute(self, query, params):
        if query.startswith("SELECT"):
            assert not self.active
            return Cursor([(key, copy.deepcopy(row)) for key, row in sorted(self.hotels.items()) if key in params[0]])
        assert self.active
        if query.startswith("INSERT INTO sources"):
            assert "ON CONFLICT(id) DO NOTHING" in query
            self.sources.setdefault(params[0], copy.deepcopy(params[1].obj))
            return Cursor()
        assert query == "UPDATE hotels SET payload=%s,updated_at=now() WHERE id=%s AND payload=%s"
        projected, identifier, expected = params[0].obj, params[1], params[2].obj
        self.order.append(identifier)
        if self.race:
            self.hotels[identifier]["owner_edit"] = "concurrent change"
        if self.hotels[identifier] != expected:
            return Cursor(rowcount=0)
        self.hotels[identifier] = copy.deepcopy(projected)
        return Cursor(rowcount=1)


def test_parameterized_cas_updates_in_id_order_and_keeps_source_priors(tmp_path):
    hotels = [hotel("b"), hotel("a")]
    data, _ = write_fixture(tmp_path, hotels=hotels)
    db = Database(hotels)
    db.sources["osm_geofabrik"] = {"id": "osm_geofabrik", "alpha": 42, "beta": 2}
    def connect(*args, **kwargs):
        assert kwargs == {"autocommit": True}
        return db
    result = apply_address_supplement(data, "fixture", connection_factory=connect)
    assert result["updated"] == 2 and db.order == ["a", "b"]
    assert db.sources["osm_geofabrik"]["alpha"] == 42 and db.sources["osm_geofabrik"]["beta"] == 2
    assert DISTRICT_SOURCE in db.sources
    assert apply_address_supplement(data, "fixture", connection_factory=connect)["updated"] == 0


def test_concurrent_payload_change_is_not_overwritten(tmp_path):
    data, _ = write_fixture(tmp_path)
    db = Database([hotel()], race=True)
    result = apply_address_supplement(data, "fixture", connection_factory=lambda *args, **kwargs: db)
    assert result["updated"] == 0 and result["conflicted"] == 1
    assert db.hotels["h"]["address"] == "Сочи" and db.hotels["h"]["owner_edit"] == "concurrent change"


def test_snapshot_from_cache_selects_exact_administrative_relations_without_simplifying(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    geometries = {}
    for index, osm_id in enumerate(DISTRICTS):
        geometry = json.loads(json.dumps(mapping(box(index * 3, 0, index * 3 + 1, 1))))
        geometries[osm_id] = geometry
        rows = [{"osm_type": "relation", "osm_id": osm_id, "name": f"Исходный район {osm_id}", "type": "administrative", "geojson": geometry}]
        rows += [{"osm_type": "node", "osm_id": 5083822957, "type": "memorial", "geojson": {"type": "Point", "coordinates": [0, 0]}},
                 {"osm_type": "relation", "osm_id": 11865377, "type": "town", "geojson": geometry}]
        write_json(cache / f"{index}.json", {"results": rows, "url": "https://nominatim.openstreetmap.org/search", "sha256": "a" * 64,
                                            "retrieved_at": f"2026-10-27T17:50:4{index}+00:00"})
    result = snapshot_from_cache(cache)
    assert {feature["properties"]["osm_id"] for feature in result["features"]} == set(DISTRICTS)
    for feature in result["features"]:
        assert feature["geometry"] == geometries[feature["properties"]["osm_id"]]
    assert result["source"]["license"] == "ODbL-1.0" and result["source"]["attribution"] == "© OpenStreetMap contributors"


def test_shipped_coverage_geometry_and_frozen_inputs_are_honest():
    data = ROOT / "data"
    supplement = load_supplement(data)
    counts = supplement["counts"]
    assert counts["total_hotels"] == counts["entries"] == counts["district_covered"] == 250
    assert counts["district_missing"] == counts["district_ambiguous"] == 0
    assert (counts["base_specific"], counts["base_generic"], counts["new_building_address"], counts["precision_area"]) == (80, 170, 106, 64)
    snapshot_data = json.loads(gzip.decompress((data / "seed/admin_districts.geojson.gz").read_bytes()))
    polygons = {feature["id"]: shape(feature["geometry"]) for feature in snapshot_data["features"]}
    hotels = json.loads((data / "seed/hotels.json").read_text())
    for entry in supplement["entries"]:
        assert polygons[entry["district"]].contains(Point(entry["lon"], entry["lat"]))
        if entry["precision"] == "source":
            assert entry["address"] == entry["base_address"]
    assert len(hotels) == 250 and sum(h["address"] == "Сочи" for h in hotels) == 170
    manifest = json.loads((data / "seed/manifest.json").read_text())
    for filename in ("admin_districts.geojson.gz", "hotel_addresses.json"):
        path = data / "seed" / filename
        assert manifest["outputs"]["data/seed/" + filename] == output_metadata(path)
    assert (data / "seed/admin_districts.geojson.gz").read_bytes()[4:8] == b"\0\0\0\0"


def copy_published_inputs(tmp_path):
    seed = tmp_path / "data/seed"
    seed.mkdir(parents=True)
    for name in ("hotels.json", "hotels.csv.gz", "sources.json", "sources.csv.gz", "buildings.geojson.gz",
                 "admin_districts.geojson.gz", "hotel_addresses.json", "manifest.json"):
        shutil.copyfile(ROOT / "data/seed" / name, seed / name)
    return seed.parent


def test_rebuild_is_idempotent_and_changes_only_supplemental_manifest_fields(tmp_path):
    data = copy_published_inputs(tmp_path)
    paths = [data / "seed" / name for name in ("hotels.json", "hotels.csv.gz", "sources.json", "sources.csv.gz")]
    frozen = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    sidecars = [data / "seed" / name for name in ("admin_districts.geojson.gz", "hotel_addresses.json", "manifest.json")]
    before = {path: path.read_bytes() for path in sidecars}
    run(data)
    assert {path: path.read_bytes() for path in sidecars} == before
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths} == frozen


def test_final_compaction_restores_supplemental_registry_and_rebuilds_addresses(tmp_path, monkeypatch):
    from pipeline import compact_seed
    data = copy_published_inputs(tmp_path)
    manifest_path = data / "seed/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for name in ("admin_districts.geojson.gz", "hotel_addresses.json"):
        manifest["outputs"].pop("data/seed/" + name)
    manifest.pop("supplemental_sources")
    write_json(manifest_path, manifest)
    addresses = data / "seed/hotel_addresses.json"
    original_addresses = addresses.read_bytes()
    addresses.unlink()
    frozen_names = ("hotels.json", "hotels.csv.gz", "sources.json", "sources.csv.gz", "buildings.geojson.gz")
    frozen = {name: (data / "seed" / name).read_bytes() for name in frozen_names}
    monkeypatch.setattr(compact_seed, "ROOT", tmp_path)
    monkeypatch.setattr(compact_seed, "SEED", data / "seed")
    monkeypatch.setattr(compact_seed, "output_metadata", output_metadata)
    compact_seed.run()
    assert addresses.read_bytes() == original_addresses
    assert {name: (data / "seed" / name).read_bytes() for name in frozen_names} == frozen
    final = json.loads(manifest_path.read_text())
    assert {key: value for key, value in final.items() if key not in {"outputs", "supplemental_sources"}} == {
        key: value for key, value in manifest.items() if key not in {"outputs", "supplemental_sources"}}
    assert [row["id"] for row in final["supplemental_sources"]] == [DISTRICT_SOURCE]
    for name in ("admin_districts.geojson.gz", "hotel_addresses.json"):
        assert final["outputs"]["data/seed/" + name] == output_metadata(data / "seed" / name)


def test_registered_supplemental_source_is_exposed_by_source_api(tmp_path):
    from backend.api.catalog import sources
    data, _ = write_fixture(tmp_path)
    db = Database([hotel()])
    apply_address_supplement(data, "fixture", connection_factory=lambda *args, **kwargs: db)

    class Repository:
        def all(self, table):
            assert table == "sources"
            return list(db.sources.values())

    rows = sources(repo=Repository())
    admin = next(row for row in rows if row["id"] == DISTRICT_SOURCE)
    assert Source.model_validate(admin).license == "ODbL-1.0"
