"""Real graph routes for night venues, pets, families and airport access."""
from __future__ import annotations

from shapely.geometry import Point

from geo.coordinates import TO_UTM
from geo.io import read_json
from geo.region import load_features
from geo.tz_airport import transit_connections
from geo.tz_environment import CarGraph
from geo.tz_routes import NearbyRoutes
from geo.walk import WalkGraph
from pipeline.common import SEED, sha
from pipeline.tz_common import baseline_sources, observation, parameters, spec_input, write_stage

KINDS = ["night_shop", "night_food", "dog_park", "park", "veterinary", "pet_shop", "playground", "hospital", "children_hospital", "promenade", "station", "bus_stop", "parking", "ev"]


def compact(value):
    return {k: v for k, v in value.items() if k != "node_ids"} if value else None


def run():
    graph = WalkGraph(read_json(SEED / "walk_graph.json"))
    roads, railways = load_features(SEED / "roads.geojson"), load_features(SEED / "railways.geojson")
    route_parameters = parameters("night_open")
    planner = NearbyRoutes(graph, roads, railways, snap_limit_m=route_parameters["snap_limit_m"])
    features = load_features(SEED / "pois.geojson")+load_features(SEED / "tz_transit_stops.geojson")+roads
    # Duplicate IDs from original POIs and added transit extract are one place.
    features = list({f["id"]: f for f in features}.values())
    car = CarGraph(load_features(SEED / "tz_vehicle_roads.geojson"))
    terminals = [f for f in load_features(SEED / "tz_aeroways.geojson") if f["properties"].get("aeroway") == "terminal"]
    terminal = max(terminals, key=lambda f: f["shape"].area) if terminals else None
    terminal_xy = terminal["shape"].representative_point().coords[0] if terminal else None
    car_start, airport_snap = car.snap(terminal_xy) if terminal_xy else (None, None)
    car_costs = car.distances(car_start)
    stops = load_features(SEED / "tz_transit_stops.geojson")
    airport_origin = planner.from_hotel(terminal_xy) if terminal_xy else None
    airport_stops = []
    for stop in stops:
        if not terminal or stop["shape"].distance(terminal["shape"]) > 500:
            continue
        node, _ = planner.snap(stop["shape"].representative_point().coords[0])
        if airport_origin and node in airport_origin[2] and airport_origin[2][node] <= 10:
            airport_stops.append(stop["id"])
    relations = read_json(SEED / "tz_transit_routes.json")
    rows = []
    filenames = ["hotels.json", "walk_graph.json.gz", "pois.geojson.gz", "roads.geojson.gz", "railways.geojson.gz", "tz_transit_stops.geojson.gz", "tz_vehicle_roads.geojson.gz", "tz_aeroways.geojson.gz", "tz_transit_routes.json"]
    inputs = {"/data/seed/"+name: sha(SEED / name) for name in filenames}
    for kind in ("night_open", "dog_walk", "veterinary_nearby", "pet_shop_nearby", "airport_transfer"):
        inputs.update([spec_input(kind)])
    sources = baseline_sources()
    for hotel in read_json(SEED / "hotels.json"):
        xy = TO_UTM.transform(hotel["lon"], hotel["lat"])
        origin = planner.from_hotel(xy)
        arrival = planner.from_hotel(xy, reverse=True)
        nearby = [f for f in features if f["shape"].distance(Point(xy)) <= 6500]
        found = {kind: planner.nearest(origin, nearby, kind=kind, max_minutes=route_parameters["near_minutes"]) for kind in KINDS}
        for kind, category in [("night_open", "night_shop"), ("dog_walk", "dog_park"), ("veterinary_nearby", "veterinary"), ("pet_shop_nearby", "pet_shop")]:
            value = found[category]
            if kind == "night_open":
                value = {"available": True if any(v and v["available"] for v in [found["night_shop"], found["night_food"]]) else None,
                         "shop": compact(found["night_shop"]), "food": compact(found["night_food"]), "open_at": "02:00", "threshold_minutes": 15}
            if kind == "dog_walk":
                value = {"available": found["dog_park"]["available"] if found["dog_park"] else None,
                         "dog_park": compact(found["dog_park"]), "ordinary_park_fallback": compact(found["park"]) if not found["dog_park"] or not found["dog_park"]["available"] else None}
            if value is not None:
                value["calculation_parameters"] = parameters(kind)
            rows.append(observation(hotel["id"], kind, value, source_ids=["osm_geofabrik", "copernicus_dem", "tz_osm_geofabrik"],
                                    emit_claim=value is not None, method="tz_directed_poi_routes@1"))
        family = {key: compact(found[key]) for key in ("playground", "hospital", "children_hospital", "promenade")}
        family["available"] = True if family["playground"] and family["playground"]["available"] else None
        rows.append(observation(hotel["id"], "family_routes", family, source_ids=["osm_geofabrik", "copernicus_dem"], method="tz_directed_family_routes@1"))
        reachable_stops = []
        if arrival:
            for stop in stops:
                target, _ = planner.snap(stop["shape"].representative_point().coords[0])
                if target in arrival[2] and arrival[2][target] <= 15:
                    reachable_stops.append(stop["id"])
        transfer = transit_connections(relations, airport_stops, reachable_stops)
        car_target, hotel_snap = car.snap(xy)
        transfer.update(airport_stop_ids=airport_stops, hotel_stop_count=len(reachable_stops),
                        airport_walk_anchor="terminal representative point; ≤10 graph minutes and ≤500 m map proximity", airport_walk_anchor_known=airport_origin is not None,
                        airport_car_distance_km=round(car_costs[car_target]/1000, 3) if car_target in car_costs else None,
                        airport_car_snap_m=airport_snap, hotel_car_snap_m=hotel_snap,
                        car_route_semantics="directed OSM distance, excludes snap connectors; turn restrictions not assembled; no travel time",
                        nearest_station=compact(found["station"]), nearest_bus_stop=compact(found["bus_stop"]),
                        parking_walk=compact(found["parking"]), ev_walk=compact(found["ev"]))
        rows.append(observation(hotel["id"], "airport_transfer", transfer, source_ids=["tz_osm_geofabrik", "osm_geofabrik", "copernicus_dem"], method="tz_osm_airport_connectivity@1"))
    return write_stage("routes", rows, [*read_json(SEED / "tz_osm_sources.json"), sources["osm_geofabrik"], sources["copernicus_dem"]], inputs=inputs, limitations=[
        "Walking minutes use only the existing directed OSM graph with DEM slopes/Tobler; two disjoint zones limit connectivity",
        "Hotel and destination snap ≤120 m; straight snap connectors are unmapped and excluded from time/distance; not door-to-door estimates",
        "A route absent from this graph is unknown, not physical inaccessibility. Nearby means ≤15 graph minutes; search bound 90 graph minutes",
        "Unparsed/missing opening_hours are unknown, never closed; supported hours are 24/7 and unqualified daily clock ranges at 02:00",
        "Ordinary park fallback is not a dog park; no pet admission policy is inferred",
        "Children hospital requires paediatrics/pediatrics tag or explicit child hospital/clinic name; emergency flag is separate",
        "OSM route member order supports connectivity only. No openly licensed Sochi GTFS was located; schedule, frequency and night service remain unknown",
        "Car route is an approximate directed road-graph distance; no turn restrictions, temporary closures or travel-time estimate"])


if __name__ == "__main__":
    run()
