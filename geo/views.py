"""Two-scale ray profiles and curvature-aware line-of-sight."""
from __future__ import annotations

import numpy as np
from numba import njit

EARTH_RADIUS_M = 6_371_000.0
CURVATURE = 1.0 - 1.0 / 7.0


def profile_distances(near_radius_m=1000.0, far_radius_m=30000.0):
    return np.concatenate((np.arange(4, near_radius_m, 2), np.arange(near_radius_m, far_radius_m + 1, 30)))


def sample_profiles(point, azimuths, distances, local_ground, building_ids, terrain, sea):
    angles = np.radians(azimuths)
    x = point[0] + np.sin(angles)[:, None] * distances
    y = point[1] + np.cos(angles)[:, None] * distances
    ground = terrain.sample(x, y)
    near = distances < min(local_ground.values.shape) * local_ground.cell / 2 - 5
    local = local_ground.sample(x[:, near], y[:, near])
    valid = np.isfinite(local)
    near_ground = ground[:, near]
    near_ground[valid] = local[valid]
    ground[:, near] = near_ground
    from geo.terrain import Grid
    bid = Grid(building_ids, local_ground.x_min, local_ground.y_max, local_ground.cell).sample(x, y)
    bid = np.where(np.isfinite(bid), bid, -1).astype(np.int32)
    water = sea.rays(point, azimuths, distances)
    # A coastline extrapolation cannot turn positive-height inland DSM into sea.
    # Native water elevations may have small noise; 1m is explicitly a tolerance.
    water &= np.isfinite(ground) & (ground <= 1.0)
    ground[water] = 0
    return ground.astype(np.float32), bid, water


@njit(cache=True)
def scan_profiles(ground, building_ids, sea, distances, heights, bases, eyes, dem_shift=0.0):
    """Each ray is visible only when an actual sea/terrain cell clears its horizon.

    Returns floor×azimuth sea/ridge visibility and first blocking building IDs,
    plus separate terrain/building horizon angles for the highest observer.
    A shared DEM shift models correlated error, not independent pixel noise.
    """
    nf, nr, nd = len(eyes), ground.shape[0], ground.shape[1]
    visible = np.zeros((nf, nr), np.bool_)
    mountain = np.zeros((nf, nr), np.bool_)
    blockers = np.full((nf, nr), -1, np.int32)
    terrain_horizon = np.full(nr, -90.0)
    building_horizon = np.full(nr, -90.0)
    valid_fraction = np.zeros(nr)
    full_horizon = np.full((nf, nr), -90.0)
    sea_nearest = np.full((nf, nr), np.inf)
    sea_slope = np.full((nf, nr), -1e30)
    mountain_nearest = np.full((nf, nr), np.inf)
    mountain_slope = np.full((nf, nr), -1e30)
    for ray in range(nr):
        valid_count = 0
        for floor in range(nf):
            horizon = -1e30
            blocking_id = -1
            for j in range(nd):
                z = ground[ray, j]
                if not np.isfinite(z):
                    continue
                if floor == 0:
                    valid_count += 1
                distance = distances[j]
                curvature = CURVATURE * distance * distance / (2 * EARTH_RADIUS_M)
                bid = building_ids[ray, j]
                terrain_z = z + (0.0 if sea[ray, j] else dem_shift) - curvature
                surface = terrain_z
                if bid >= 0:
                    surface = bases[bid] + heights[bid] + dem_shift - curvature
                slope = (surface - eyes[floor]) / distance
                if sea[ray, j] and bid < 0:
                    if slope >= horizon - 1e-7:
                        visible[floor, ray] = True
                        sea_nearest[floor, ray] = min(sea_nearest[floor, ray], distance)
                        sea_slope[floor, ray] = max(sea_slope[floor, ray], slope)
                    elif blocking_id >= 0:
                        blockers[floor, ray] = blocking_id
                if not sea[ray, j] and bid < 0 and z >= 200 and distance >= 1500 and slope >= horizon - 1e-7:
                    mountain[floor, ray] = True
                    mountain_nearest[floor, ray] = min(mountain_nearest[floor, ray], distance)
                    mountain_slope[floor, ray] = max(mountain_slope[floor, ray], slope)
                if slope > horizon:
                    horizon = slope
                    if bid >= 0:
                        blocking_id = bid
                if floor == nf - 1:
                    terrain_horizon[ray] = max(terrain_horizon[ray], np.degrees(np.arctan((terrain_z-eyes[floor])/distance)))
                    if bid >= 0:
                        building_horizon[ray] = max(building_horizon[ray], np.degrees(np.arctan(slope)))
            if visible[floor, ray]:
                blockers[floor, ray] = -1
            full_horizon[floor, ray] = np.degrees(np.arctan(horizon))
        valid_fraction[ray] = valid_count / nd
    return visible, mountain, blockers, terrain_horizon, building_horizon, valid_fraction, full_horizon, sea_nearest, sea_slope, mountain_nearest, mountain_slope


def los_to_point(observer, eye_z, target, target_z, terrain, buildings, *, step_m=10):
    """LOS to a named peak/landmark, with source building IDs retained."""
    from shapely.geometry import LineString
    origin, goal = np.asarray(observer), np.asarray(target)
    distance = float(np.linalg.norm(goal-origin))
    if distance < 1:
        return {"visible": True, "blocking_building_ids": [], "distance_m": distance}
    ds = np.arange(step_m, distance, step_m)
    xy = origin + ds[:, None] / distance * (goal-origin)
    z = terrain.sample(xy[:, 0], xy[:, 1])
    target_effective = target_z-CURVATURE*distance**2/(2*EARTH_RADIUS_M)
    sight = eye_z + ds/distance*(target_effective-eye_z)
    terrain_eff = z-CURVATURE*ds**2/(2*EARTH_RADIUS_M)
    terrain_blocked = bool(np.any(terrain_eff > sight))
    blockers = []
    line = LineString([origin, goal])
    for building in buildings:
        intersection = line.intersection(building["shape"])
        if intersection.is_empty:
            continue
        d = line.project(intersection.representative_point())
        if d <= 1 or d >= distance-1:
            continue
        roof = building["base_m"] + building["height_m"] - CURVATURE*d*d/(2*EARTH_RADIUS_M)
        if roof > eye_z+d/distance*(target_effective-eye_z):
            blockers.append(building["id"])
    return {"visible": not (terrain_blocked or blockers), "blocking_building_ids": blockers,
            "terrain_blocked": terrain_blocked, "distance_m": distance,
            "coverage": float(np.isfinite(z).mean()) if len(z) else 1.0}
