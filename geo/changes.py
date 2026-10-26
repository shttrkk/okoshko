"""Explicit synthetic new-building scenario used by /changes."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon, mapping, shape

from geo.coordinates import TO_UTM, geographic, hotel_center, metric


def new_building_fixture(hotel, geo=None):
    template = json.loads((Path(__file__).parent/"fixtures"/"new_building.json").read_text())
    lon, lat = hotel_center(hotel)
    xy = np.asarray(TO_UTM.transform(lon, lat))
    facades = (geo or {}).get("facades", [])
    candidate = max(facades, key=lambda facade: max(facade.get("sea_width_by_floor", [0])), default=None)
    angle = candidate["azimuth_deg"] if candidate else 225
    direction = np.array([np.sin(np.radians(angle)), np.cos(np.radians(angle))])
    tangent = np.array([direction[1], -direction[0]])
    footprint = hotel.get("footprint") or (geo or {}).get("building", {}).get("geometry")
    extent = 30.0
    if footprint:
        polygon = metric(shape(footprint))
        vertices = np.asarray(polygon.exterior.coords) if polygon.geom_type == "Polygon" else np.asarray(polygon.convex_hull.exterior.coords)
        extent = max(0, float(np.max((vertices-xy) @ direction)))
    center = xy+direction*(extent+template["distance_from_facade_m"])
    half_width, half_depth = template["frontage_m"]/2, template["depth_m"]/2
    polygon = Polygon([center-half_width*tangent-half_depth*direction,
                       center+half_width*tangent-half_depth*direction,
                       center+half_width*tangent+half_depth*direction,
                       center-half_width*tangent+half_depth*direction])
    return {"type": "Feature", "id": "synthetic-new-building-"+hotel["id"],
            "properties": {"synthetic": True, "height_m": template["height_m"], "building:levels": template["levels"],
                           "source_id": "geo_engine", "scenario": "new_building_fixture_v1", "description": template["description"]},
            "geometry": mapping(geographic(polygon))}
