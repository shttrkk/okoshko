"""Geometric airport exposure; never a decibel or flight-frequency estimate."""
from __future__ import annotations

import numpy as np
from shapely.geometry import LineString, Point

from geo.coordinates import angle_difference, azimuth


def runway_corridors(line, *, extension_m=6000, half_width_m=500):
    coords = np.asarray(line.coords)
    a, b = coords[0], coords[-1]
    length = float(np.linalg.norm(b-a))
    if length < 100:
        return []
    direction = (b-a)/length
    return [LineString([a-direction*extension_m, a]).buffer(half_width_m, cap_style="flat"),
            LineString([b, b+direction*extension_m]).buffer(half_width_m, cap_style="flat")]


def airport_exposure(xy, runways, *, extension_m=6000, half_width_m=500, nearby_m=3000, facades=()):
    point = Point(xy)
    usable = [r for r in runways if r["shape"].geom_type == "LineString" and r["shape"].length >= 100]
    if not usable:
        return None
    nearest = min(usable, key=lambda r: r["shape"].distance(point))
    distance = nearest["shape"].distance(point)
    corridors = [(r["id"], c) for r in usable for c in runway_corridors(r["shape"], extension_m=extension_m, half_width_m=half_width_m)]
    under = [oid for oid, corridor in corridors if corridor.covers(point)]
    target = nearest["shape"].interpolate(nearest["shape"].project(point))
    facing = [f["code"] for f in facades if angle_difference(f["azimuth_deg"], azimuth(target.x-xy[0], target.y-xy[1])) <= 60]
    return {"available": bool(under or distance <= nearby_m), "runway_distance_m": round(distance, 1),
            "runway_id": nearest["id"], "in_corridor": bool(under), "corridor_runway_ids": sorted(set(under)),
            "facing_facades": facing, "corridor_extension_m": extension_m, "corridor_half_width_m": half_width_m,
            "nearby_threshold_m": nearby_m, "flight_frequency": None, "night_flights": None, "noise_db": None}


def transit_connections(routes, airport_stops, hotel_stops):
    """Respect member order. Missing/unsupported stop ordering remains unknown."""
    usable = []
    for route in routes:
        stops = [m["id"] for m in route["members"] if m.get("role", "").startswith("stop")]
        if len(stops) < 2:
            stops = [m["id"] for m in route["members"] if m.get("role", "").startswith("platform")]
        if len(stops) >= 2:
            usable.append((route, stops))
    starts, ends = set(airport_stops), set(hotel_stops)
    direct, transfer = [], []
    for route, stops in usable:
        roles = {m["id"]: m.get("role", "") for m in route["members"]}
        for i, stop in enumerate(stops):
            if stop in starts and not roles[stop].endswith("exit_only") and any(s in ends and not roles[s].endswith("entry_only") for s in stops[i+1:]):
                direct.append(route)
                break
    if direct:
        selected = direct[:5]
        count = 0
    else:
        for first, a in usable:
            a_roles = {m["id"]: m.get("role", "") for m in first["members"]}
            positions = [i for i, stop in enumerate(a) if stop in starts and not a_roles[stop].endswith("exit_only")]
            if not positions:
                continue
            reachable = {stop for stop in a[min(positions)+1:] if not a_roles[stop].endswith("entry_only")}
            for second, b in usable:
                if first["id"] == second["id"]:
                    continue
                b_roles = {m["id"]: m.get("role", "") for m in second["members"]}
                ends_at = [i for i, stop in enumerate(b) if stop in ends and not b_roles[stop].endswith("entry_only")]
                if ends_at and reachable.intersection(stop for stop in b[:max(ends_at)] if not b_roles[stop].endswith("exit_only")):
                    transfer.append((first, second))
        selected = list({r["id"]: r for pair in transfer[:5] for r in pair}.values())
        count = 1 if transfer else None
    return {"available": True if count is not None else None, "transfers": count,
            "routes": [{"id": r["id"], "ref": r["tags"].get("ref"), "mode": r["tags"].get("route"),
                        "opening_hours": r["tags"].get("opening_hours"), "interval": r["tags"].get("interval"),
                        "by_night": r["tags"].get("by_night")} for r in selected],
            "scheduled_minutes": None, "night_service": None,
            "coverage": "OSM ordered route members; shared exact stop IDs for one transfer; no GTFS"}
