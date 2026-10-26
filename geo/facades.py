"""Observers outside actual exterior walls, retaining other wings as occluders."""
from __future__ import annotations

import numpy as np
from shapely.geometry import LineString, Point
from shapely.geometry.polygon import orient

from geo.coordinates import azimuth, side


def extract_facades(footprint, *, offset_m: float = 1.5, points_per_edge: int = 3) -> list[dict]:
    polygons = list(footprint.geoms) if footprint.geom_type == "MultiPolygon" else [footprint]
    groups = {}
    for polygon in polygons:
        polygon = orient(polygon, sign=1.0)
        ring = np.asarray(polygon.exterior.coords)
        for a, b in zip(ring[:-1], ring[1:], strict=True):
            vector = b - a
            length = np.linalg.norm(vector)
            if length < 2:
                continue
            normal = np.array([vector[1], -vector[0]]) / length
            angle = azimuth(*normal)
            code = side(angle)
            group = groups.setdefault(code, {"code": code, "edges": [], "points": [], "normals": [], "length_m": 0.0})
            group["edges"].append(LineString([a, b]))
            group["length_m"] += float(length)
            for fraction in np.linspace(.2, .8, points_per_edge):
                point = a + fraction * vector + offset_m * normal
                # Concave wings can contain an outward-offset point: reject it.
                if not footprint.covers(Point(point)):
                    group["points"].append(point.tolist())
                    group["normals"].append(angle)
    return [group for group in groups.values() if group["points"]]
