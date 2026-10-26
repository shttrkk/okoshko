"""Compact LOS envelopes for an additive near-building runtime scenario.

A cached ray retains the nearest already-visible target and its maximum apparent
slope. An added roof can only remove visibility. Comparing its actual angular
profile with both values gives the updated LOS without rebuilding the region.
The same comparison is repeated for every stored Monte Carlo world.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point, shape

from geo.coordinates import TO_UTM, angle_difference, geographic, hotel_center, metric
from geo.sun import visible_sunset
from geo.terrain import Grid
from geo.views import CURVATURE, EARTH_RADIUS_M


def _roof_intervals(points, azimuths, polygon):
    """Exact intersections, computed once for each observer and azimuth."""
    entry = np.full((len(points), len(azimuths)), np.inf)
    exit_distance = np.full_like(entry, np.inf)
    for pi, point in enumerate(points):
        for ri, angle in enumerate(azimuths):
            direction = np.array([np.sin(np.radians(angle)), np.cos(np.radians(angle))])
            line = LineString([point, point+direction*30000])
            intersection = line.intersection(polygon)
            if intersection.is_empty or intersection.geom_type != "LineString":
                continue
            distances = [(np.asarray(coordinate)-point) @ direction for coordinate in intersection.coords]
            start, end = max(2.0, min(distances)), max(distances)
            if end >= start:
                entry[pi, ri], exit_distance[pi, ri] = start, end
    return entry, exit_distance


def _roof_slopes(entry, exit_distance, eyes, roof):
    """Curvature-adjusted maximum slope on every roof interval, vectorized."""
    coefficient = CURVATURE/(2*EARTH_RADIUS_M)
    eye = np.asarray(eyes)[None, :, None]
    start, end = entry[:, None, :], exit_distance[:, None, :]
    with np.errstate(invalid="ignore", divide="ignore"):
        at_start = (roof-eye)/start-coefficient*start
        at_end = (roof-eye)/end-coefficient*end
        optimum = np.sqrt(np.maximum(0, eye-roof)/coefficient)
        optimum = np.minimum(np.maximum(optimum, start), end)
        at_optimum = (roof-eye)/optimum-coefficient*optimum
        result = np.maximum(np.maximum(at_start, at_end), at_optimum)
    return np.where(np.isfinite(start), result, -1e30)


def _visibility(nearest, maximum_slope, roof_slope, entry):
    # This is a LOS comparison against an actual roof angle, never a scenario flag.
    existed = np.isfinite(nearest)
    return existed & ((nearest < entry[..., None, :]) | (maximum_slope >= roof_slope-1e-7))


def compute_cached_change(hotel, data_dir, new_building):
    """Return None if the compact cache cannot represent the requested scenario."""
    from geo.service import AZIMUTH_STEP_DEG, MC_SEED, SEA_MIN_WIDTH_DEG, _digest, _ranges, _write_result
    data_dir = Path(data_dir)
    geo_dir = data_dir/"geo"
    hotel_id = hotel["id"]
    baseline_path = geo_dir/(hotel_id+".json")
    cache_path = geo_dir/(hotel_id+".los.npz")
    if not baseline_path.exists() or not cache_path.exists():
        return None
    if not new_building.get("properties", {}).get("synthetic"):
        raise ValueError("new_building fixture must explicitly carry properties.synthetic=true")
    baseline = json.loads(baseline_path.read_text())
    if baseline.get("landmarks"):
        return None  # Named target LOS needs the full engine, not this sea/ridge cache.
    for input_artifact, expected in baseline["inputs"].items():
        input_path = data_dir/input_artifact.removeprefix("/data/")
        if not input_path.exists() or hashlib.sha256(input_path.read_bytes()).hexdigest() != expected:
            return None
    lon, lat = hotel_center(hotel)
    if np.linalg.norm(np.asarray(baseline["center"])-[lon, lat]) > 1e-7:
        return None
    polygon = metric(shape(new_building["geometry"]))
    center = Point(TO_UTM.transform(lon, lat))
    if polygon.geom_type != "Polygon" or abs(polygon.area-polygon.convex_hull.area) > .001:
        return None  # Non-convex additions use the full raster/ray engine.
    if polygon.distance(center) > 1000:
        return None
    height = float(new_building["properties"].get("height_m") or new_building["properties"].get("height", 0))
    if not np.isfinite(height) or height <= 0:
        raise ValueError("synthetic building needs a positive height_m")
    with np.load(geo_dir/(hotel_id+".npz"), allow_pickle=False) as local:
        ground = Grid(local["near_ground"], float(local["x_min"]), float(local["y_max"]), float(local["cell"]))
        ids = Grid(local["near_building_ids"], ground.x_min, ground.y_max, ground.cell)
        minx, miny, maxx, maxy = polygon.bounds
        xx, yy = np.meshgrid(np.arange(minx-60, maxx+61, 30), np.arange(miny-60, maxy+61, 30))
        samples = ground.sample(xx, yy)
        samples = samples[(ids.sample(xx, yy) < 0) & np.isfinite(samples)]
        if not len(samples):
            return None
        base = max(0, float(np.percentile(samples, 10)))
    with np.load(cache_path, allow_pickle=False) as cached:
        cache = {key: cached[key] for key in cached.files}
    if cache["baseline_sha256"].astype(np.uint8).tobytes().decode() != hashlib.sha256(baseline_path.read_bytes()).hexdigest():
        return None
    geo = copy.deepcopy({key: value for key, value in baseline.items() if key != "claims"})
    timestamp = datetime.now(UTC)
    name = hotel_id+"-change-"+_digest(new_building)
    artifact = "/data/geo/"+name+".json"
    geo.update(synthetic=True, computed_at=timestamp.isoformat(), artifact=artifact, scenario_building=new_building,
               scenario_method="cached_ray_envelope_exact_polygon_los_v1", runtime_cache="/data/geo/"+cache_path.name)
    geo["limitations"] = geo["limitations"] + ["Сценарий использует сохранённые LOS-профили и те же 64 мира Monte Carlo; добавленный полигон пересекается с лучами точно, с кривизной и рефракцией",
        "Шум и пеший граф в этом сценарии сохранены от baseline; их новые claims не создаются",
        "Исторические границы фасадов и точек наблюдения сохранены; источник пересчитывается при изменении исходных данных"]
    geo["dependency_building_ids"] = sorted(set(geo.get("dependency_building_ids", [])) | {new_building["id"]})
    geo["building_index"].append({"id": new_building["id"], "height_m": height, "base_m": base, "height_source": "synthetic_fixture"})
    claims, arrays, maximum_probabilities, minimum_floors = [], {}, [], []
    baseline_digest = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    cache_digest = hashlib.sha256(cache_path.read_bytes()).hexdigest()
    azimuths = cache["azimuths"]
    rng = np.random.default_rng(MC_SEED+1)
    new_heights = rng.normal(height, 1, len(cache["mc_shifts"]))

    def add(characteristic, value, scope, confidence, facade):
        templates = [claim for claim in baseline["claims"] if claim["characteristic_id"] == characteristic and claim["scope"].get("facade") == facade]
        if not templates:
            return
        source = templates[0]
        record = {"hotel_id": hotel_id, "characteristic_id": characteristic, "spec_version": 1,
                  "scope": scope, "value": value, "source": "geo_engine", "method": "cached_ray_envelope_exact_polygon_mc_v1",
                  "confidence": round(float(np.clip(confidence, 0, 1)), 4), "observed_at": timestamp.isoformat(),
                  "valid_until": (timestamp+timedelta(days=90)).isoformat(), "synthetic": True}
        record["id"] = "geo-"+_digest(record)
        record["evidence"] = [{"id": "ev-"+record["id"], "kind": "simulation", "label": "Пересчёт LOS после добавления synthetic здания",
                               "source_ids": source["evidence"][0]["source_ids"], "artifact": artifact,
                               "inputs": {"baseline_artifact": baseline["artifact"], "baseline_sha256": baseline_digest,
                                          "los_cache_artifact": "/data/geo/"+cache_path.name, "los_cache_sha256": cache_digest,
                                          "input_sha256": baseline["inputs"], "synthetic_new_building": new_building,
                                          "new_building_base_m": base, "mc_seed": MC_SEED, "new_height_seed": MC_SEED+1},
                               "output": value, "limitations": geo["limitations"]}]
        claims.append(record)

    sea_ray_masks = []
    new_roof_horizons = []
    cones = []
    for index, facade in enumerate(geo["facades"]):
        prefix = f"f{index}_"
        points, normals, eyes = cache[prefix+"points"], cache[prefix+"normals"], cache["eyes"]
        entries, exits = _roof_intervals(points, azimuths, polygon)
        roof_slopes = _roof_slopes(entries, exits, eyes, base+height)
        sea_visible = _visibility(cache[prefix+"sea_nearest"], cache[prefix+"sea_slope"], roof_slopes, entries)
        mountain_visible = _visibility(cache[prefix+"mountain_nearest"], cache[prefix+"mountain_slope"], roof_slopes, entries)
        facing = angle_difference(azimuths[None, :], normals[:, None]) <= 75
        sea_visible &= facing[:, None, :]
        mountain_visible &= facing[:, None, :]
        sea_width = sea_visible.sum(axis=2).mean(axis=0)*AZIMUTH_STEP_DEG
        mountain_width = mountain_visible.sum(axis=2).mean(axis=0)*AZIMUTH_STEP_DEG
        horizon = np.maximum(cache[prefix+"horizon"], np.degrees(np.arctan(roof_slopes))).mean(axis=0)
        p_sea, p_mountain = [], []
        p_sun = []
        for k, shift in enumerate(cache["mc_shifts"]):
            observer_index = k % len(points)
            trial_entry = entries[observer_index:observer_index+1]
            trial_exit = exits[observer_index:observer_index+1]
            trial_roof = _roof_slopes(trial_entry, trial_exit, cache["mc_eyes"][k], base+new_heights[k]+shift)
            sv = _visibility(cache[prefix+"mc_sea_nearest"][k:k+1], cache[prefix+"mc_sea_slope"][k:k+1], trial_roof, trial_entry)[0]
            mv = _visibility(cache[prefix+"mc_mountain_nearest"][k:k+1], cache[prefix+"mc_mountain_slope"][k:k+1], trial_roof, trial_entry)[0]
            p_sea.append((sv & facing[observer_index]).sum(axis=1)*AZIMUTH_STEP_DEG >= SEA_MIN_WIDTH_DEG)
            p_mountain.append((mv & facing[observer_index]).sum(axis=1)*AZIMUTH_STEP_DEG >= SEA_MIN_WIDTH_DEG)
            trial_horizon = np.maximum(cache[prefix+"mc_horizon"][k], np.degrees(np.arctan(trial_roof[0])))
            p_sun.append([month["available"] for month in visible_sunset(geo["sun_paths"], azimuths, trial_horizon, normals[observer_index])])
        p_sea, p_mountain = np.mean(p_sea, axis=0), np.mean(p_mountain, axis=0)
        p_sun = np.mean(p_sun, axis=0)
        sea_available, mountain_available = sea_width >= SEA_MIN_WIDTH_DEG, mountain_width >= SEA_MIN_WIDTH_DEG
        min_floor = next((i+1 for i, yes in enumerate(sea_available) if yes), None)
        min_p80 = next((i+1 for i, p in enumerate(p_sea) if p >= .8), None)
        lost_sea_rays = np.isfinite(cache[prefix+"sea_nearest"]) & ~sea_visible & facing[:, None, :]
        facade.update(sea_width_by_floor=sea_width.tolist(), sea_probability_by_floor=p_sea.tolist(),
                      mountain_width_by_floor=mountain_width.tolist(), min_floor_sea=min_floor, min_floor_sea_p80=min_p80,
                      blocking_building_ids=sorted(set(facade["blocking_building_ids"]) | ({new_building["id"]} if lost_sea_rays.any() else set())))
        sunsets = visible_sunset(geo["sun_paths"], azimuths, horizon, facade["azimuth_deg"])
        facade["sunset_by_month"] = sunsets
        facade["min_floor_sunset"] = min((i+1 for month in sunsets for i, yes in enumerate(month["available"]) if yes), default=None)
        quality = facade["confidence"]
        for characteristic, flags, widths, probabilities in (("sea_view", sea_available, sea_width, p_sea), ("mountain_view", mountain_available, mountain_width, p_mountain)):
            threshold = next((i+1 for i, yes in enumerate(flags) if yes), None)
            for lo, hi, yes in _ranges(flags):
                value = {"available": yes, "facade": facade["code"], "min_floor": threshold,
                         "probability": round(float(np.mean(probabilities[lo:hi+1])), 4)}
                if characteristic == "sea_view":
                    value.update(sea_width_deg=round(float(np.mean(widths[lo:hi+1])), 1), sea_fraction=round(float(min(1, np.mean(widths[lo:hi+1])/150)), 3), blocking_building_ids=facade["blocking_building_ids"])
                else:
                    value.update(peaks=[], width_deg=round(float(np.mean(widths[lo:hi+1])), 1))
                p = probabilities[lo:hi+1]
                add(characteristic, value, {"type": "floor_range", "facade": facade["code"], "floor_min": lo+1, "floor_max": hi+1}, np.mean(p if yes else 1-p)*quality, facade["code"])
        for month in sunsets:
            first = next((i+1 for i, yes in enumerate(month["available"]) if yes), None)
            for lo, hi, yes in _ranges(month["available"]):
                p = p_sun[month["month"]-1, lo:hi+1]
                value = {"available": yes, "facade": facade["code"], "min_floor": first, "months": [month["month"]], "azimuth_deg": month["azimuth_deg"],
                         "visible_minutes": float(np.mean(month["visible_minutes"][lo:hi+1])), "probability": round(float(np.mean(p)), 4)}
                add("sunset_view", value, {"type": "floor_range", "facade": facade["code"], "floor_min": lo+1, "floor_max": hi+1, "month": month["month"]}, np.mean(p if yes else 1-p)*quality, facade["code"])
        maximum_probabilities.append(float(p_sea[-1]))
        if min_p80:
            minimum_floors.append(min_p80)
        arrays[prefix+"sea_width"] = sea_width.astype(np.float32)
        arrays[prefix+"sea_probability"] = p_sea.astype(np.float32)
        arrays[prefix+"horizon"] = horizon.astype(np.float32)
        mask = np.mean(sea_visible[:, -1], axis=0) > .5
        sea_ray_masks.append(mask)
        origin = np.asarray(TO_UTM.transform(lon, lat))
        for angle in azimuths[mask]:
            end = origin+1500*np.asarray([np.sin(np.radians(angle)), np.cos(np.radians(angle))])
            cones.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[lon, lat], list(geographic(Point(end)).coords)[0]]},
                          "properties": {"facade": facade["code"], "floor": len(eyes), "target": "sea", "synthetic": True}})
        new_roof_horizons.append(np.degrees(np.arctan(roof_slopes[:, -1])).mean(axis=0))
    if new_roof_horizons:
        new_roof_horizon = np.mean(new_roof_horizons, axis=0)
        sea_rays = np.any(sea_ray_masks, axis=0)
        for point, value, is_sea in zip(geo["horizon"], new_roof_horizon, sea_rays, strict=True):
            point["buildings_deg"] = max(point["buildings_deg"], float(value))
            point["sea"] = bool(is_sea)
    # Remove rays that the new geometry blocks; the remaining ones are recalculated.
    geo["view_cones"]["features"] = cones
    geo["monte_carlo"].update(probability=max(maximum_probabilities, default=0), min_floor_p80=min(minimum_floors, default=None))
    geo["dsm"]["scenario_overlay"] = "exact synthetic polygon roof angular LOS, baseline numeric grids preserved"
    return _write_result(geo, claims, arrays, geo_dir, name)
