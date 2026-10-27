"""No-source boot shortcut keeps exact rail selection and unknown semantics."""
import csv
import gzip
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from shapely.geometry import LineString, box, mapping

from backend.contracts import ToolCall, ToolContext
from backend.tests.test_api_integration import ApiRepo
from backend.tools.registry import Registry
from geo.coordinates import TO_UTM, geographic
from geo.traits import _railway_no_sources, compute_trait

HOTEL = {"id": "rail-test", "name": "Synthetic rail hotel", "lon": 39.72, "lat": 43.58,
         "tags": {"building_id": "own"}}
X, Y = TO_UTM.transform(HOTEL["lon"], HOTEL["lat"])
UNKNOWN = "Нет картированного железнодорожного источника в зоне модели; шум неизвестен"


def feature(identity, geometry, **properties):
    return {"type": "Feature", "id": identity, "geometry": mapping(geographic(geometry)),
            "properties": {"source_id": "osm_geofabrik", "observed_at": "2026-10-25T21:21:21Z", **properties}}


def write(directory, name, features, compressed=False):
    seed = directory / "seed"
    seed.mkdir(parents=True, exist_ok=True)
    path = seed / (name + (".geojson.gz" if compressed else ".geojson"))
    content = json.dumps({"type": "FeatureCollection", "features": features}).encode()
    path.write_bytes(gzip.compress(content) if compressed else content)
    return path


def inputs(directory, rails=None, building=None):
    own = feature("own", building if building is not None else box(X-12, Y-12, X+12, Y+12),
                  height_m=12, height_source="height", levels=4)
    write(directory, "buildings", [own])
    return write(directory, "railways", rails or [])


def test_no_rail_sources_skips_full_region_with_the_identical_unknown_result(tmp_path):
    inputs(tmp_path)
    with patch("geo.traits._railway_no_sources", return_value=False):
        expected = compute_trait(HOTEL, tmp_path, "railway_noise")
    with patch("geo.traits.get_region", side_effect=AssertionError("Full Region should be unnecessary")):
        actual = compute_trait(HOTEL, tmp_path, "railway_noise")
    assert actual == expected and actual["claims"] == [] and actual["warnings"] == [UNKNOWN]


@pytest.mark.parametrize("distance,shortcut", [(249, False), (600, True), (649, True), (651, True)])
def test_candidate_buffer_and_actual_facade_observer_range_match_the_original_engine(tmp_path, distance, shortcut):
    rail = feature("rail", LineString([(X+distance, Y-10), (X+distance, Y+10)]), railway="rail")
    inputs(tmp_path, [rail])
    assert _railway_no_sources(HOTEL, tmp_path) is shortcut
    with patch("geo.traits._railway_no_sources", return_value=False):
        expected = compute_trait(HOTEL, tmp_path, "railway_noise")
    actual = compute_trait(HOTEL, tmp_path, "railway_noise")
    assert actual == expected
    if distance == 249:
        assert actual["claims"] and all(row["confidence"] == .32 for row in actual["claims"])
        assert all(row["scope"]["type"] == "facade" and row["value"]["sources"] == ["rail"] for row in actual["claims"])


def test_sources_outside_500m_from_hotel_but_inside_observer_range_keep_known_noise(tmp_path):
    rail = feature("rail", LineString([(X+600, Y-10), (X+600, Y+10)]), railway="rail")
    inputs(tmp_path, [rail], building=box(X-120, Y-12, X+120, Y+12))
    assert not _railway_no_sources(HOTEL, tmp_path)
    with patch("geo.traits._railway_no_sources", return_value=False):
        expected = compute_trait(HOTEL, tmp_path, "railway_noise")
    actual = compute_trait(HOTEL, tmp_path, "railway_noise")
    assert actual == expected and actual["claims"]
    assert any(row["scope"]["facade"] == "E" for row in actual["claims"])


def test_requested_building_outside_actual_1600m_domain_keeps_building_first_warning(tmp_path):
    inputs(tmp_path, building=box(X+2000, Y-12, X+2024, Y+12))
    assert not _railway_no_sources(HOTEL, tmp_path)
    result = compute_trait(HOTEL, tmp_path, "railway_noise")
    assert not result["claims"] and result["warnings"] == ["Нет привязки к зданию для пофасадной оценки железнодорожного шума"]


def test_missing_or_ambiguous_explicit_building_association_keeps_the_existing_engine(tmp_path):
    inputs(tmp_path)
    assert not _railway_no_sources({**HOTEL, "tags": {}}, tmp_path)
    assert not _railway_no_sources({**HOTEL, "tags": {"building_id": "unmapped"}}, tmp_path)
    own = feature("own", box(X-12, Y-12, X+12, Y+12))
    write(tmp_path, "buildings", [own, own])
    assert not _railway_no_sources(HOTEL, tmp_path)


@pytest.mark.parametrize("replacement", [None, "malformed", '{"type":"FeatureCollection"}',
    '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{},"geometry":null}]}'])
def test_missing_or_broken_rail_inputs_are_not_a_proof_of_an_empty_source_set(tmp_path, replacement):
    path = inputs(tmp_path)
    if replacement is None:
        path.unlink()
    else:
        path.write_text(replacement)
    assert not _railway_no_sources(HOTEL, tmp_path)
    # Registry retains unknown/error handling, never emitting an absent/quiet Claim.
    repo = ApiRepo()
    repo.insert("hotels", HOTEL)
    def collect():
        return Registry(repo, data_dir=tmp_path).execute(
            ToolContext(hotel_id=HOTEL["id"], characteristic_id="railway_noise", run_id="test"),
            ToolCall(tool="geo_trait", params={"kind": "railway_noise"}))
    if replacement and '"geometry":null' in replacement:
        # This malformed geometry already fails closed in the existing engine;
        # the preflight must not turn that failure into a successful empty set.
        with patch("geo.traits._railway_no_sources", return_value=False), pytest.raises(AttributeError):
            collect()
        with pytest.raises(AttributeError):
            collect()
        return
    result = collect()
    assert not result.claims and result.warnings


def test_digest_cache_detects_restored_mtime_and_plain_gzip_precedence(tmp_path):
    inputs(tmp_path)
    assert _railway_no_sources(HOTEL, tmp_path)
    path = tmp_path / "seed/railways.geojson"
    previous = path.stat()
    rail = feature("rail", LineString([(X+250, Y-20), (X+250, Y+20)]), railway="rail")
    write(tmp_path, "railways", [rail])
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert not _railway_no_sources(HOTEL, tmp_path)
    write(tmp_path, "railways", [], compressed=True)
    assert not _railway_no_sources(HOTEL, tmp_path)  # Plain source still wins.
    path.unlink()
    assert _railway_no_sources(HOTEL, tmp_path)


def test_changed_geometry_still_abstains_before_any_baseline_preflight(tmp_path):
    inputs(tmp_path)
    with patch("geo.traits._railway_no_sources", side_effect=AssertionError("Baseline must not be used")):
        result = compute_trait(HOTEL, tmp_path, "railway_noise", geo_artifact="/data/geo/rail-test-change.json")
    assert not result["claims"] and "Изменённая геометрия" in result["warnings"][0]


def test_real_gamma_boot_case_abstains_without_initializing_full_region():
    root = Path(__file__).resolve().parents[1]
    with gzip.open(root / "data/seed/hotels.csv.gz", "rt", newline="") as stream:
        hotel = next(json.loads(row["payload"]) for row in csv.DictReader(stream)
                     if json.loads(row["payload"])["id"] == "osm-node-13825400642")
    with patch("geo.traits.get_region", side_effect=AssertionError("Gamma needs no full Region for an unknown result")):
        result = compute_trait(hotel, root / "data", "railway_noise")
    assert result["claims"] == [] and result["warnings"] == [UNKNOWN]
