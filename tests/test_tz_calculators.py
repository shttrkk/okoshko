"""Synthetic, inspectable known answers; no real-world accuracy is claimed."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
from shapely.geometry import LineString, Point, box, mapping

from geo.coordinates import TO_UTM
from geo.terrain import Grid
from geo.tz_airport import airport_exposure, select_airport_runways, transit_connections
from geo.tz_climate import climate_days, water_comfort
from geo.tz_environment import CarGraph, car_allowed, mapped_nearby
from geo.tz_history import exact_history
from geo.tz_landmarks import contour_target, landmark_visibility
from geo.tz_routes import NearbyRoutes, open_at_two, pediatric
from geo.walk import WalkGraph
from pipeline.fetch_osm_extra import categories
from pipeline.fetch_tz_history import entity_history


def feature(oid, geometry, **properties):
    return {"id": oid, "properties": properties, "shape": geometry,
            "feature": {"id": oid, "geometry": mapping(geometry), "properties": properties}}


def test_runway_axis_extensions_not_infinite_stripe():
    runway = feature("r", LineString([(0, 0), (1000, 0)]))
    assert airport_exposure((-2000, 0), [runway])["in_corridor"] is True
    assert airport_exposure((-2000, 501), [runway])["in_corridor"] is False
    assert airport_exposure((500, 400), [runway])["in_corridor"] is False
    assert airport_exposure((-6001, 0), [runway])["available"] is False
    assert airport_exposure((0, 0), []) is None
    assert airport_exposure((500, 0), [runway])["noise_db"] is None


def test_only_runways_in_exact_iata_airport_are_used():
    runway = feature("aer", LineString([(0, 0), (1000, 0)]))
    short_unrelated = feature("unassociated", LineString([(2000, 0), (2120, 0)]))
    airport = feature("airport", box(-10, -10, 1010, 10), aeroway="aerodrome", iata="AER")
    assert select_airport_runways([runway, short_unrelated], [airport]) == [runway]
    assert select_airport_runways([runway], [airport], iata="XXX") == []


def route(oid, stops):
    return {"id": oid, "tags": {"route": "bus", "ref": oid}, "members": [{"id": s, "role": "stop"} for s in stops]}


def test_transit_direct_one_transfer_and_direction_unknown():
    assert transit_connections([route("a", ["airport", "hotel"])], ["airport"], ["hotel"])["transfers"] == 0
    assert transit_connections([route("a", ["airport", "x"]), route("b", ["x", "hotel"])], ["airport"], ["hotel"])["transfers"] == 1
    reverse = transit_connections([route("a", ["hotel", "airport"])], ["airport"], ["hotel"])
    assert reverse["transfers"] is None and reverse["available"] is None
    assert reverse["scheduled_minutes"] is None and reverse["night_service"] is None
    no_boarding = route("exit", ["airport", "hotel"])
    no_boarding["members"][0]["role"] = "stop_exit_only"
    assert transit_connections([no_boarding], ["airport"], ["hotel"])["transfers"] is None


@pytest.mark.parametrize(("hours", "expected"), [("24/7", True), ("21:00-04:00", True), ("02:00-03:00", True),
    ("08:00-22:00", False), ("00:00-02:00", False), ("closed", False), (None, None),
    ("Mo-Fr 00:00-05:00", None), ("sunset-sunrise", None), ("25:00-29:00", None), ("24/7; PH off", None)])
def test_opening_hours_conservative_two_am(hours, expected):
    assert open_at_two(hours) is expected


def test_children_hospital_requires_profile_not_any_hospital():
    assert pediatric({"healthcare:speciality": "paediatrics"})
    assert pediatric({"name": "Детская городская больница № 9"})
    assert not pediatric({"amenity": "hospital", "name": "Городская больница"})


def test_exact_history_not_nearest_name_or_wrong_assigned_contour():
    hotel = {"id": "osm-node-1", "lon": 39.72, "lat": 43.58, "tags": {"building_id": "osm-way-2"}}
    a = {"id": "osm-way-2", "geometry": mapping(box(39.719, 43.579, 39.721, 43.581)), "properties": {"start_date": "1905", "building:architecture": "art_nouveau"}}
    assert exact_history(hotel, [a])["inception"] == "1905"
    assert exact_history(hotel, [a])["inception_tag"] == "start_date"
    assert exact_history(hotel, [a])["explicit_building_date"] is None
    assert exact_history(hotel, [{**a, "id": "osm-way-3"}])["available"] is None
    wrong = {**a, "geometry": mapping(box(39.73, 43.59, 39.74, 43.60))}
    assert exact_history(hotel, [wrong])["available"] is None


def test_wikidata_ranks_references_and_no_fabricated_statement():
    data = {"claims": {"P571": [{"id": "Q1$real", "rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"value": {"time": "+1905-00-00T00:00:00Z", "precision": 9}}}, "references": [{"hash": "abc"}]},
                                    {"rank": "deprecated", "mainsnak": {"snaktype": "value", "datavalue": {"value": "wrong"}}}]}}
    value = entity_history(data)
    assert len(value["inception"]) == 1 and value["inception"][0]["references"] == [{"hash": "abc"}]
    assert value["architect"] == []


def test_climate_daily_thresholds_complete_month_only_and_water_independent():
    days = [(date(2025, 1, 1)+timedelta(days=i)).isoformat() for i in range(31)]
    daily = {"time": days, "sunshine_duration": [6*3600]*15+[5*3600]*16, "precipitation_sum": [1]*12+[.9]*19}
    january = climate_days(daily)[0]
    assert january["sunny_days"] == 15 and january["rainy_days"] == 12
    assert january["wet_month_proxy"] is True
    assert climate_days({"time": days[:1], "sunshine_duration": [36000], "precipitation_sum": [5]})[0]["available"] is None
    assert water_comfort(None) is None
    assert "комфортно" in water_comfort(22) and water_comfort(18) == "прохладная вода"


def test_access_hierarchy_and_zero_mapped_is_unknown():
    assert not car_allowed({"highway": "footway"})
    assert not car_allowed({"highway": "service", "access": "private"})
    assert car_allowed({"highway": "service", "access": "private", "motor_vehicle": "yes"})
    value = mapped_nearby((0, 0), [], radius_m=500)
    assert value["mapped_count"] == 0 and value["available"] is None


def test_real_directed_graph_uses_bend_and_reverse_stays_unknown():
    nodes = [{"id": str(i), "lon": x, "lat": y, "elevation_m": 0} for i, (x, y) in enumerate([(39.72, 43.58), (39.721, 43.58), (39.721, 43.581)])]
    graph = WalkGraph({"nodes": nodes, "edges": [{"u": "0", "v": "1", "length_m": 100}, {"u": "1", "v": "2", "length_m": 100}]})
    planner = NearbyRoutes(graph, snap_limit_m=5)
    goal_xy = TO_UTM.transform(nodes[2]["lon"], nodes[2]["lat"])
    poi = feature("dog", Point(goal_xy), leisure="dog_park")
    result = planner.nearest(planner.from_hotel(TO_UTM.transform(nodes[0]["lon"], nodes[0]["lat"])), [poi], kind="dog_park")
    assert result["distance_m"] == 200 and result["node_ids"] == ["0", "1", "2"]
    assert planner.nearest(planner.from_hotel(goal_xy), [feature("back", Point(TO_UTM.transform(nodes[0]["lon"], nodes[0]["lat"])), leisure="dog_park")], kind="dog_park") is None
    assert planner.from_hotel((0, 0)) is None


def test_car_graph_oneway_not_an_undirected_shortcut():
    road = feature("w", LineString([(39.72, 43.58), (39.721, 43.58)]), highway="service", oneway="yes", node_ids=["1", "2"])
    graph = CarGraph([road])
    assert graph.distances("1")["2"] > 50
    assert "1" not in graph.distances("2")


def test_dsm_target_requires_cell_centre_inside_exact_contour():
    grid = Grid(np.zeros((10, 10)), 0, 100, 10)
    grid.values[4, 4] = 50
    grid.values[3, 3] = 100  # taller neighbouring object must not become target
    assert contour_target(box(40, 50, 50, 60), grid)["z_m"] == 50
    assert contour_target(box(41, 51, 42, 52), grid) is None


def test_landmark_los_known_blocker_and_unknown_terrain():
    lon, lat = 39.72, 43.58
    x, y = TO_UTM.transform(lon, lat)
    grid = Grid(np.zeros((100, 100)), x-100, y+100, 2)
    snapshot = {"building": {"id": "own"}, "floors": {"count": 3, "base_m": 0, "floor_height_m": 3, "eye_offset_m": 1.5},
                "facades": [{"code": "E", "azimuth_deg": 90, "observer_points": [[lon, lat]]}]}
    target = {"id": "tower", "osm_id": "target", "name": "Fixture", "target": {"xy": [x+70, y], "z_m": 10}}
    visible = landmark_visibility(snapshot, target, grid, [])
    assert len(visible) == 1 and visible[0]["value"]["available"] is True and visible[0]["scope"]["floor_max"] == 3
    blocked = landmark_visibility(snapshot, target, grid, [{"id": "blocker", "shape": box(x+30, y-10, x+40, y+10), "base_m": 0, "height_m": 20}])
    assert blocked[0]["value"]["available"] is False and blocked[0]["value"]["blocking_building_ids"] == ["blocker"]
    grid.values[:] = np.nan
    assert landmark_visibility(snapshot, target, grid, [])[0]["value"]["available"] is None


def test_extra_osm_selectors_keep_missing_categories_and_original_tags():
    assert "aeroways" in categories({"aeroway": "runway"})
    assert "transit_stops" in categories({"railway": "halt"})
    assert "construction" in categories({"highway": "construction"})
    assert "communication_masts" not in categories({"man_made": "tower"})
    assert "communication_masts" in categories({"man_made": "tower", "tower:type": "communication"})
