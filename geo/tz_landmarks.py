"""Curated landmark LOS using the existing terrain and facade observations."""
from __future__ import annotations

import numpy as np
from shapely.geometry import Point

from geo.coordinates import TO_UTM, TO_WGS, angle_difference, azimuth
from geo.views import los_to_point


def contour_target(geometry, terrain, *, height_m=None):
    """A DSM cell must have its centre inside the exact OSM contour."""
    if terrain is None or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        return None
    west, south, east, north = geometry.bounds
    cell = terrain.cell
    c0, c1 = int((west-terrain.x_min)//cell), int((east-terrain.x_min)//cell)
    r0, r1 = int((terrain.y_max-north)//cell), int((terrain.y_max-south)//cell)
    candidates = []
    for row in range(max(0, r0), min(terrain.values.shape[0]-1, r1)+1):
        for col in range(max(0, c0), min(terrain.values.shape[1]-1, c1)+1):
            x, y = terrain.x_min+(col+.5)*cell, terrain.y_max-(row+.5)*cell
            z = float(terrain.values[row, col])
            if geometry.covers(Point(x, y)) and np.isfinite(z):
                candidates.append((z, x, y))
    if not candidates:
        return None
    dsm_z, x, y = max(candidates)
    if height_m is None:
        return {"xy": [x, y], "z_m": dsm_z, "height_source": "copernicus_dsm_contour_max", "height_m": None}
    ring = geometry.buffer(60).difference(geometry.buffer(5))
    west, south, east, north = ring.bounds
    xs, ys = np.meshgrid(np.arange(west, east, cell), np.arange(south, north, cell))
    ground = terrain.sample(xs.ravel(), ys.ravel())
    inside = np.asarray([ring.covers(Point(px, py)) for px, py in zip(xs.ravel(), ys.ravel(), strict=True)])
    known = ground[np.isfinite(ground) & inside]
    if not len(known):
        return None
    base = float(np.percentile(known, 10))
    return {"xy": [x, y], "z_m": max(dsm_z, base+height_m), "height_m": height_m,
            "base_m": base, "height_source": "osm_height_and_dsm_ground"}


def landmark_visibility(snapshot, landmark, terrain, buildings, *, sector_deg=60, max_distance_m=30000, ray_records=None):
    floors = snapshot.get("floors", {})
    if floors.get("count") is None or not snapshot.get("building") or not landmark.get("target"):
        return []
    target = landmark["target"]
    xy, z = target["xy"], target["z_m"]
    results = []
    own_id = snapshot["building"]["id"]
    # Only the target's contour is excluded. Other wings of the observer's own
    # building can still obstruct the ray.
    obstacles = [dict(b, base_m=floors["base_m"]) if b["id"] == own_id else b for b in buildings if b["id"] != landmark["osm_id"]]
    for facade in snapshot.get("facades", []):
        points = [TO_UTM.transform(*p) for p in facade.get("observer_points", [])]
        points = [p for p in points if angle_difference(facade["azimuth_deg"], azimuth(xy[0]-p[0], xy[1]-p[1])) <= sector_deg]
        if not points or min(np.linalg.norm(np.asarray(p)-xy) for p in points) > max_distance_m:
            continue
        values = []
        for floor in range(1, floors["count"]+1):
            eye = floors["base_m"]+(floor-1)*floors["floor_height_m"]+floors["eye_offset_m"]
            checks = [los_to_point(p, eye, xy, z, terrain, obstacles, step_m=10) for p in points]
            if ray_records is not None:
                for point, check in zip(points, checks, strict=True):
                    ray_records.append({'landmark_id': landmark['id'], 'facade': facade['code'], 'floor': floor,
                                        'start': list(TO_WGS.transform(*point)), 'target': list(TO_WGS.transform(*xy)),
                                        'eye_z_m': eye, 'target_z_m': z, 'available': check['visible'] if check.get('coverage') == 1 else None,
                                        'blocking_building_ids': check['blocking_building_ids'], 'terrain_blocked': check.get('terrain_blocked'),
                                        'terrain_coverage': check.get('coverage')})
            known = all(c.get("coverage", 0) == 1 for c in checks)
            values.append({"available": any(c["visible"] for c in checks) if known else None,
                           "blocking_building_ids": sorted({oid for c in checks for oid in c["blocking_building_ids"]}),
                           "terrain_blocked": any(c.get("terrain_blocked", False) for c in checks),
                           "distance_m": round(min(c["distance_m"] for c in checks), 1), "terrain_coverage": min(c.get("coverage", 0) for c in checks)})
        start = 0
        for index in range(1, len(values)+1):
            if index == len(values) or values[index]["available"] != values[start]["available"]:
                interval = values[start:index]
                results.append({"scope": {"type": "floor_range", "facade": facade["code"], "floor_min": start+1, "floor_max": index},
                                "value": {"available": interval[0]["available"], "landmark_id": landmark["id"], "name": landmark["name"],
                                          "target": target, "distance_m": interval[0]["distance_m"], "terrain_coverage": min(v["terrain_coverage"] for v in interval),
                                          "blocking_building_ids": sorted({b for v in interval for b in v["blocking_building_ids"]}),
                                          "terrain_blocked": any(v["terrain_blocked"] for v in interval), "min_floor": start+1 if interval[0]["available"] else None}})
                start = index
    return results


class TwoScaleGround:
    def __init__(self, near, far):
        self.near, self.far = near, far

    def sample(self, x, y):
        local = self.near.sample(x, y)
        return np.where(np.isfinite(local), local, self.far.sample(x, y))


def line_obstacles(region, start_xy, target_xy, observer_points=()):
    from shapely.geometry import MultiPoint
    envelope = MultiPoint([start_xy, target_xy, *observer_points]).convex_hull.buffer(2)
    indices = region.indexes["buildings"].query(envelope, predicate="intersects")
    selected = []
    for index in indices:
        feature = region.features["buildings"][int(index)]
        properties = feature["properties"]
        height = properties.get("height_m")
        if height is None:
            # No invented height prior: the coarse DSM still contains these
            # roofs, and completeness is explicitly reported by the caller.
            continue
        boundary = feature["shape"].buffer(30).difference(feature["shape"])
        point = boundary.representative_point()
        base = terrain_base(region.terrain, point.x, point.y)
        if base is not None:
            selected.append({**feature, "height_m": height, "base_m": base})
    return selected


def terrain_base(terrain, x, y):
    xs, ys = np.meshgrid(np.arange(x-30, x+31, 30), np.arange(y-30, y+31, 30))
    values = terrain.sample(xs, ys)
    values = values[np.isfinite(values)]
    return float(np.percentile(values, 10)) if len(values) else None
