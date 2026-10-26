from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import LineString, Point, box, mapping

from backend.contracts.models import Claim
from geo.changes import new_building_fixture
from geo.coordinates import TO_UTM, geographic, metric
from geo.facades import extract_facades
from geo.noise import energy_sum, night_hours_possible, noise_facade, source_samples
from geo.region import Region
from geo.service import compute_with_region
from geo.sun import monthly_paths, visible_sunset
from geo.terrain import Grid, SeaClassifier, build_local_dsm
from geo.views import los_to_point, scan_profiles
from geo.walk import WalkGraph, crossing_details, travel_minutes


def test_facade_outward_normals_and_observers():
    footprint = box(0, 0, 20, 10)
    facades = {facade["code"]: facade for facade in extract_facades(footprint)}
    assert set(facades) == {"N", "E", "S", "W"}
    assert facades["N"]["normals"] == [0, 0, 0]
    assert facades["W"]["normals"] == [270, 270, 270]
    assert all(not footprint.covers(Point(point)) for facade in facades.values() for point in facade["points"])


def test_known_blocker_floor_threshold_and_ids():
    distance = np.arange(2, 1000, 2).astype(float)
    ground = np.zeros((1, len(distance)), np.float32)
    ids = np.full(ground.shape, -1, np.int32)
    ids[0, (distance >= 40) & (distance <= 50)] = 0
    sea = np.broadcast_to(distance > 100, ground.shape).copy()
    result = scan_profiles(ground, ids, sea, distance, np.array([7.0]), np.array([0.0]), np.array([1.5, 4.5, 7.5, 10.5]))
    assert result[0][:, 0].tolist() == [False, False, True, True]
    assert result[2][:2, 0].tolist() == [0, 0]
    changed = scan_profiles(ground, ids, sea, distance, np.array([15.0]), np.array([0.0]), np.array([1.5, 4.5, 7.5, 10.5]))
    assert not changed[0].any()


def test_coastline_land_left_sea_right():
    sea = SeaClassifier([LineString([(0, 1000), (0, -1000)])])
    assert sea.sample(np.array([-20, 20]), np.array([0, 0])).tolist() == [True, False]
    rays = sea.rays((20, 0), np.array([270, 90]), np.array([10, 30, 100]))
    assert rays.tolist() == [[False, True, True], [False, False, False]]
    assert not sea.has_coverage((3000, 0))
    assert not sea.rays((3000, 0), np.array([270]), np.array([100, 4000])).any()


def test_roofs_replaced_not_added_to_copernicus_dsm():
    dem = np.zeros((100, 100), np.float32)
    dem[45:55, 45:55] = 12  # blurred Copernicus roof
    terrain = Grid(dem, 0, 100, 1)
    buildings = [{"id": "house", "shape": box(45, 45, 55, 55), "height_m": 12}]
    ground, ids, dsm = build_local_dsm(terrain, buildings, (50, 50), radius_m=30, cell=1)
    assert buildings[0]["base_m"] == 0
    assert np.max(dsm[ids == 0]) == 12  # never 12+12
    assert np.max(ground.values[ids == 0]) == 0


def test_sun_window_uses_monthly_geometry_orientation_and_horizon():
    paths = monthly_paths(39.72, 43.58)
    assert len(paths) == 12
    assert abs(paths[0]["points"][-3]["azimuth_deg"]-paths[5]["points"][-3]["azimuth_deg"]) > 40
    angles = np.arange(0, 360, 3)
    clear = np.zeros((1, len(angles)))
    west = visible_sunset(paths, angles, clear, 270)
    east = visible_sunset(paths, angles, clear, 90)
    blocked = visible_sunset(paths, angles, np.full_like(clear, 8), 270)
    assert any(month["available"][0] for month in west)
    assert not any(month["available"][0] for month in east)
    assert not any(month["available"][0] for month in blocked)


def test_noise_facing_screen_and_energy_addition():
    building = {"id": "own", "shape": box(-10, -10, 10, 10)}
    facades = {facade["code"]: facade for facade in extract_facades(building["shape"])}
    sources = source_samples([{"id": "road", "shape": LineString([(-40, -300), (-40, 300)]), "properties": {"highway": "primary"}}], kind="road")
    west = noise_facade(facades["W"], sources, [building])
    east = noise_facade(facades["E"], sources, [building])
    assert west["night_db_min"] > east["night_db_min"]+8
    assert west["measurement"] is False
    assert energy_sum([50, 50]) == pytest.approx(53.0103)
    assert not source_samples([{"id": "platform", "shape": LineString([(0, 0), (10, 0)]), "properties": {"railway": "platform"}}], kind="rail")


def test_known_daytime_bar_does_not_become_night_source():
    assert night_hours_possible("Mo-Su 09:00-18:00") is False
    assert night_hours_possible("Mo-Su 19:00-02:00") is True
    assert night_hours_possible("24/7") is True
    assert night_hours_possible("sunset-sunrise") is None
    sources = source_samples([{"id": "bar", "shape": Point(0, 0), "properties": {"amenity": "bar", "opening_hours": "09:00-18:00"}}], kind="bar")
    assert sources[0]["night"] is None


def test_tobler_directed_graph_steps_and_return_asymmetry():
    assert travel_minutes(1000, 100) > travel_minutes(1000, -100)
    assert travel_minutes(1000, 0, steps=True) == 2*travel_minutes(1000, 0)
    data = {"nodes": [{"id": "a", "lon": 39.7, "lat": 43.6, "elevation_m": 100},
                      {"id": "b", "lon": 39.7, "lat": 43.59, "elevation_m": 0}],
            "edges": [{"u": "a", "v": "b", "length_m": 1000, "highway": "footway"}]}
    graph = WalkGraph(data)
    assert graph.shortest("a", ["b"])
    assert graph.shortest("b", ["a"]) is None
    data["edges"].append({"u": "b", "v": "a", "length_m": 1000, "highway": "steps"})
    graph = WalkGraph(data)
    outbound = graph.summary(graph.shortest("a", ["b"]))
    inbound = graph.summary(graph.shortest("b", ["a"]))
    assert outbound["minutes"] < inbound["minutes"]
    assert inbound["ascent_m"] == 100
    assert inbound["has_steps"]


def test_curvature_blocks_beyond_sea_horizon():
    terrain = Grid(np.zeros((10, 6001), np.float32), -100, 500, 10)
    result = los_to_point((0, 450), 1.5, (50000, 450), 0, terrain, [], step_m=100)
    assert result["terrain_blocked"]
    assert not result["visible"]


def test_real_route_geometry_retains_rail_tunnel_transition():
    route = {"type": "LineString", "coordinates": [[39.72, 43.58], [39.721, 43.58]]}
    rail = {"id": "rail", "shape": metric(LineString([(39.7205, 43.579), (39.7205, 43.581)])),
            "properties": {"railway": "rail"}}
    result = crossing_details(route, [], [rail], [{"geometry": route, "tunnel": True}])
    assert len(result) == 1
    assert result[0]["properties"]["controlled"] is True
    assert result[0]["properties"]["transition"] == "tunnel"


@pytest.fixture
def synthetic_region(tmp_path):
    seed = tmp_path/"seed"
    seed.mkdir()
    geo = tmp_path/"geo"
    geo.mkdir()
    x, y = TO_UTM.transform(39.72, 43.58)
    building = box(x-10, y-10, x+10, y+10)
    props = {"synthetic": True, "source_id": "test_fixture", "height_m": 12, "height_source": "levels", "levels": 4,
             "observed_at": "2026-10-26T00:00:00Z"}
    def write(name, values):
        (seed/(name+".geojson")).write_text(json.dumps({"type": "FeatureCollection", "features": values}))
    write("buildings", [{"type": "Feature", "id": "test-building", "geometry": mapping(geographic(building)), "properties": props}])
    write("coastline", [{"type": "Feature", "id": "test-coast", "geometry": mapping(geographic(LineString([(x-100, y+30000), (x-100, y-30000)]))), "properties": props}])
    for name in ("pois", "roads", "railways", "beaches"):
        write(name, [])
    np.savez_compressed(geo/"terrain_30m.npz", terrain=np.zeros((2001, 2001), np.float32), x_min=x-30000, y_max=y+30000, cell=30)
    hotel = {"id": "synthetic-hotel", "name": "Synthetic test hotel", "lon": 39.72, "lat": 43.58, "footprint": mapping(geographic(building)), "tags": {"building_id": "test-building"}}
    return hotel, Region(tmp_path), geo


def test_runtime_change_recasts_geometry_and_provenance(synthetic_region, monkeypatch):
    import geo.service as service
    monkeypatch.setattr(service, "MC_ITERATIONS", 8)
    hotel, region, output = synthetic_region
    baseline = compute_with_region(hotel, region, output, computed_at="2026-10-26T00:00:00Z")
    fixture = new_building_fixture(hotel, baseline["geo"])
    changed = compute_with_region(hotel, region, output, new_building=fixture, computed_at="2026-10-26T00:00:00Z")
    def west(result):
        return next(facade for facade in result["geo"]["facades"] if facade["code"] == "W")
    assert west(baseline)["sea_width_by_floor"][0] > west(changed)["sea_width_by_floor"][0]
    assert fixture["id"] in changed["geo"]["dependency_building_ids"]
    assert changed["geo"]["synthetic"] is True
    assert all(claim["synthetic"] for claim in changed["claims"])
    assert changed["claims"]
    for result in (baseline, changed):
        for claim in result["claims"]:
            Claim.model_validate(claim)
            artifact = output/Path(claim["evidence"][0]["artifact"]).name
            assert artifact.exists()
            assert claim["evidence"][0]["inputs"]["input_sha256"]
        with np.load(output/(Path(result["geo"]["artifact"]).stem+".npz"), allow_pickle=False) as arrays:
            assert all(array.dtype.kind in "biuf" for array in arrays.values())
    # Independent runs with the fixed seed produce identical probability matrices.
    repeated = compute_with_region(hotel, region, output, computed_at="2026-10-26T00:00:00Z")
    assert west(baseline)["sea_probability_by_floor"] == west(repeated)["sea_probability_by_floor"]
