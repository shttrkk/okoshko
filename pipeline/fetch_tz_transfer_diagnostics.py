"""Audit airport-transfer unknowns and one-at-a-time approach sensitivity."""
from __future__ import annotations

import gzip
import json
import math
from collections import Counter

from geo.coordinates import TO_UTM
from geo.io import read_json
from geo.region import load_features
from geo.tz_routes import NearbyRoutes
from geo.tz_transit import TransitNetwork
from geo.walk import WalkGraph
from pipeline.common import SEED, dump, gzip_json, sha, utcnow
from pipeline.tz_common import observation, parameters, spec_input, write_stage


def access_diagnostics(origin, snap_distance, stop_records, limits, *, airport=False):
    snap_limit = limits["snap_limit_m"]
    threshold = limits["airport_walk_minutes" if airport else "hotel_walk_minutes"]
    counters, ids = Counter(), []
    for stop in stop_records:
        if airport and (stop["terminal_distance_m"] is None or stop["terminal_distance_m"] > limits["airport_radius_m"]):
            counters["outside_radius"] += 1
            continue
        if stop["snap_m"] is None or stop["snap_m"] > snap_limit:
            counters["stop_snap_missing"] += 1
        elif origin is None or stop["node"] not in origin[2]:
            counters["no_directed_walk_within_90_minute_search"] += 1
        elif origin[2][stop["node"]] > threshold:
            counters["walk_exceeds_threshold"] += 1
        else:
            counters["eligible"] += 1
            ids.append(stop["id"])
    anchor = origin is not None and snap_distance is not None and snap_distance <= snap_limit
    return {"anchor_snap_known": anchor, "anchor_snap_m": snap_distance,
            "counts": dict(counters), "eligible_stop_ids": sorted(ids) if anchor else [], "threshold_minutes": threshold,
            "semantics": "Only actual directed graph minutes; snap connectors excluded; count=0 is not absence of transport"}


def primary_reason(airport, hotel, network_audit, *, terminal_known):
    if not terminal_known:
        return "terminal_geometry_missing"
    if not airport["anchor_snap_known"]:
        return "terminal_anchor_snap_missing"
    if not airport["eligible_stop_ids"]:
        return "airport_stop_access_not_established"
    if not hotel["anchor_snap_known"]:
        return "hotel_anchor_snap_missing"
    if not hotel["eligible_stop_ids"]:
        if hotel["counts"].get("walk_exceeds_threshold"):
            return "hotel_stop_walk_exceeds_threshold"
        if hotel["counts"].get("stop_snap_missing"):
            return "hotel_stop_snap_missing_or_walk_disconnected"
        return "hotel_stop_directed_walk_not_established"
    return network_audit["reason"]


def run():
    base = parameters("airport_transfer")
    scenarios = {"baseline": base}
    for field, values in {"snap_limit_m": [60, 200], "airport_walk_minutes": [5, 15],
                          "hotel_walk_minutes": [10, 20], "airport_radius_m": [300, 800]}.items():
        for value in values:
            scenarios[f"{field}={value}"] = {**base, field: value}
    graph = WalkGraph(read_json(SEED / "walk_graph.json"))
    planner = NearbyRoutes(graph, snap_limit_m=200)
    terminals = [f for f in load_features(SEED / "tz_areas.geojson") if f["id"] == "osm-relation-7027998" and f["properties"].get("aeroway") == "terminal"]
    terminal = terminals[0] if terminals else None
    terminal_xy = terminal["shape"].representative_point().coords[0] if terminal else None
    airport_origin = planner.from_hotel(terminal_xy) if terminal_xy else None
    airport_snap = planner.snap(terminal_xy, limit=1e9)[1] if terminal_xy else None
    stops = load_features(SEED / "tz_transit_stops.geojson")
    records = []
    for stop in stops:
        xy = stop["shape"].representative_point().coords[0]
        node, snap = planner.snap(xy, limit=1e9)
        records.append({"id": stop["id"], "node": node, "snap_m": snap, "xy": xy,
                        "terminal_distance_m": stop["shape"].distance(terminal["shape"]) if terminal else None})
    network = TransitNetwork(read_json(SEED / "tz_transit_routes.json"), {r["id"] for r in records})
    airport_by_scenario = {name: access_diagnostics(airport_origin, airport_snap, records, limits, airport=True) for name, limits in scenarios.items()}
    summaries = {name: {"parameters": limits, "status": Counter(), "unknown_reasons": Counter()} for name, limits in scenarios.items()}
    audits = {}
    hotels = read_json(SEED / "hotels.json")
    for index, hotel in enumerate(hotels, 1):
        xy = TO_UTM.transform(hotel["lon"], hotel["lat"])
        origin = planner.from_hotel(xy, reverse=True)
        snap = planner.snap(xy, limit=1e9)[1]
        # At most 6 km/h in the Tobler model; 20 min + two 200m snaps is
        # a diagnostic envelope, not an alternative availability threshold.
        local = [r for r in records if math.dist(xy, r["xy"]) <= 2400]
        for name, limits in scenarios.items():
            airport = airport_by_scenario[name]
            hotel_access = access_diagnostics(origin, snap, local, limits)
            result = network.connect(airport["eligible_stop_ids"], hotel_access["eligible_stop_ids"])
            audit = network.diagnose(airport["eligible_stop_ids"], hotel_access["eligible_stop_ids"], result)
            reason = primary_reason(airport, hotel_access, audit, terminal_known=terminal is not None)
            if reason is not None:
                result.update(available=None, transfers=None, routes=[], witnesses=[])
            summaries[name]["status"]["yes" if result["available"] is True else "unknown"] += 1
            if reason:
                summaries[name]["unknown_reasons"][reason] += 1
            if name == "baseline":
                audits[hotel["id"]] = {"hotel_id": hotel["id"], "primary_unknown_reason": reason, "hotel_access": hotel_access,
                                        "network": audit, "result": result, "parameters": limits}
        if index % 50 == 0:
            print({"diagnosed": index, "total": len(hotels)}, flush=True)
    filenames = ["hotels.json", "walk_graph.json.gz", "tz_areas.geojson.gz", "tz_transit_stops.geojson.gz", "tz_transit_routes.json.gz"]
    inputs = {"/data/seed/"+name: sha(SEED / name) for name in filenames}
    inputs.update([spec_input("airport_transfer")])
    output = {"method": "tz_airport_transfer_diagnostics@2", "generated_at": utcnow(), "input_sha256": inputs,
              "terminal_id": terminal["id"] if terminal else None, "airport_access": airport_by_scenario, "relation_coverage": network.stats,
              "scenarios": {name: {**row, "status": dict(row["status"]), "unknown_reasons": dict(row["unknown_reasons"])} for name, row in summaries.items()},
              "hotels": audits, "source_ids": ["tz_osm_geofabrik", "osm_geofabrik", "copernicus_dem"],
              "limitations": ["Sensitivity is diagnostic only; baseline thresholds are not increased for coverage", "Missing mapped zero/one join is unknown, never a claim of 2+ transfers", "All 250 scheduled times/night operations remain unknown without licensed GTFS", "Raw-member/order-relaxed probes diagnose gaps but never create routes"]}
    path = SEED / "tz_transfer_diagnostics.json.gz"
    gzip_json(path, output)
    with gzip.open(SEED / "tz_routes_observations.json.gz", "rt") as stream:
        old = json.load(stream)
    rows = []
    for row in old["observations"]:
        if row["kind"] == "airport_transfer":
            audit = audits[row["hotel_id"]]
            value = {**row["value"], **audit["result"], "primary_unknown_reason": audit["primary_unknown_reason"],
                     "diagnostic_ref": "/data/seed/"+path.name, "calculation_parameters": base,
                     "hotel_stop_count": len(audit["hotel_access"]["eligible_stop_ids"])}
            row.update(observation(row["hotel_id"], row["kind"], value), method="tz_osm_airport_connectivity@2")
            row["input_sha256"] = {**row.get("input_sha256", {}), "/data/seed/"+path.name: sha(path)}
        rows.append(row)
    old_inputs = read_json(SEED / "tz_routes_manifest.json")["inputs"]
    old_inputs.update([spec_input("airport_transfer")])
    write_stage("routes", rows, read_json(SEED / "tz_routes_sources.json"), inputs=old_inputs, limitations=old["limitations"]+output["limitations"])
    dump(SEED / "tz_transfer_diagnostics_manifest.json", {"stage": output["method"], "inputs": inputs,
        "relation_coverage": network.stats, "scenarios": output["scenarios"], "outputs": {path.name: {"sha256": sha(path), "bytes": path.stat().st_size}}})
    print(json.dumps(output["scenarios"], ensure_ascii=False), flush=True)
    return output


if __name__ == "__main__":
    run()
