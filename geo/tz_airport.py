"""Geometric airport exposure; never a decibel or flight-frequency estimate."""
from __future__ import annotations

import numpy as np
from shapely.geometry import LineString, Point

from geo.coordinates import angle_difference, azimuth


def select_airport_runways(runways, aerodromes, *, iata="AER"):
    airports = [a for a in aerodromes if a["properties"].get("aeroway") == "aerodrome" and a["properties"].get("iata") == iata]
    return [r for r in runways if any(a["shape"].intersects(r["shape"]) for a in airports)]


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
    usable = [r for r in runways if r["shape"].geom_type == "LineString" and np.linalg.norm(np.asarray(r["shape"].coords[-1])-r["shape"].coords[0]) >= 100]
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
    """Exact member-position semantics; no direction/role inference."""
    from geo.tz_transit import TransitNetwork
    return TransitNetwork(routes).connect(airport_stops, hotel_stops)
