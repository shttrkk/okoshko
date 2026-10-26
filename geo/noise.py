"""Uncalibrated outdoor-noise proxy. Never a soundproofing measurement."""
from __future__ import annotations

import math
import re

import numpy as np
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from geo.coordinates import angle_difference, azimuth

# Assumptions at 10m from a long road, not observations of Sochi traffic.
ROAD_REFERENCE = {"motorway": (77, -7), "trunk": (74, -7), "primary": (70, -8),
                  "secondary": (64, -7), "tertiary": (59, -7), "unclassified": (58, -7),
                  "residential": (54, -7), "living_street": (49, -7), "service": (49, -7)}


def night_hours_possible(hours):
    """Conservative interval subset, not a complete OSM opening_hours parser.

    False means every supplied clock interval is outside 23:00–03:00.
    Unsupported expressions stay unknown and widen the noise limitation.
    """
    if not hours:
        return None
    if hours.strip() == "24/7":
        return True
    if hours.strip() in {"off", "closed"}:
        return False
    if any(token in hours for token in ("sunrise", "sunset", "PH", "SH", '"', "+", "||")):
        return None
    intervals = re.findall(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", hours)
    if not intervals:
        return None
    for sh, sm, eh, em in intervals:
        start, end = int(sh)*60+int(sm), int(eh)*60+int(em)
        if start > 1440 or end > 1440 or int(sm) > 59 or int(em) > 59:
            return None
        if end < start:
            end += 1440
        if (start < 180 and end > 0) or (start < 1620 and end > 1380):
            return True
    return False


def energy_sum(levels):
    return 10*math.log10(sum(10**(level/10) for level in levels)) if levels else None


def source_samples(features, *, kind):
    sources = []
    for feature in features:
        properties, geom = feature["properties"], feature["shape"]
        if kind == "road":
            highway = properties.get("highway", "")
            if highway not in ROAD_REFERENCE:
                continue
            day, night_delta = ROAD_REFERENCE[highway]
        elif kind == "rail":
            if properties.get("railway") not in {"rail", "light_rail", "tram", "narrow_gauge"}:
                continue
            day, night_delta = 68.0, -5.0
        else:
            if properties.get("amenity") not in {"bar", "pub", "nightclub"}:
                continue
            night_possible = night_hours_possible(properties.get("opening_hours"))
            sources.append({"point": geom.representative_point(), "day": 55, "night": None if night_possible is False else 65,
                            "length_m": 1, "kind": "bar", "id": feature["id"],
                            "hours_known": bool(properties.get("opening_hours")),
                            "opening_hours": properties.get("opening_hours"), "night_hours_possible": night_possible})
            continue
        lines = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
        for line in lines:
            if line.geom_type != "LineString":
                continue
            count = max(1, math.ceil(line.length/25))
            for i in range(count):
                sources.append({"point": line.interpolate((i+.5)/count, normalized=True), "day": day,
                                "night": day+night_delta, "length_m": line.length/count,
                                "kind": kind, "id": feature["id"], "hours_known": False})
    return sources


def noise_facade(facade, sources, buildings, *, radius_m=500):
    point_levels_day, point_levels_night, used = [], [], {}
    tree = STRtree([building["shape"] for building in buildings])
    observers = np.linspace(0, len(facade["points"])-1, min(3, len(facade["points"]))).astype(int)
    for observer_index in observers:
        xy, normal = facade["points"][observer_index], facade["normals"][observer_index]
        observer = Point(xy)
        day, night = [], []
        for source in sources:
            distance = observer.distance(source["point"])
            if distance > radius_m:
                continue
            direction = azimuth(source["point"].x-xy[0], source["point"].y-xy[1])
            screen = 10.0 if angle_difference(direction, normal) > 90 else 0.0
            line = LineString([observer, source["point"]])
            blocker_ids = [buildings[int(index)]["id"] for index in tree.query(line, predicate="intersects")
                           if line.intersection(buildings[int(index)]["shape"]).length > 2]
            if blocker_ids:
                screen = max(screen, 8.0)
            if source["kind"] == "bar":
                attenuation = 20*math.log10(max(distance, 5)/10)
            else:
                attenuation = 20*math.log10(max(distance, 5)/10)-10*math.log10(max(source["length_m"], 1)/(math.pi*10))
            day.append(source["day"]-attenuation-screen)
            if source["night"] is not None:
                night.append(source["night"]-attenuation-screen)
            used[source["id"]] = {"id": source["id"], "kind": source["kind"],
                                  "distance_m": round(distance, 1), "screen_db": screen,
                                  "blocking_building_ids": blocker_ids,
                                  "opening_hours": source.get("opening_hours"), "night_hours_possible": source.get("night_hours_possible")}
        if day:
            point_levels_day.append(energy_sum(day))
            if night:
                point_levels_night.append(energy_sum(night))
    if not point_levels_day or not point_levels_night:
        return None
    # Wide range represents missing traffic counts, schedules and calibration.
    day = float(np.mean(point_levels_day))
    night = float(np.mean(point_levels_night))
    return {"available": night+8 <= 50, "facade": facade["code"],
            "night_db_min": round(max(20, night-8), 1), "night_db_max": round(night+8, 1),
            "day_db_min": round(max(20, day-8), 1), "day_db_max": round(day+8, 1),
            "index": "quiet" if night+8 <= 50 else "noisy" if night-8 >= 55 else "moderate",
            "sources": list(used), "source_details": list(used.values()),
            "model": "outdoor_proxy_v1", "measurement": False}
