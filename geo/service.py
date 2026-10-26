"""Public offline geo adapter and reproducible on-disk calculation artifacts.

compute_hotel(hotel, Path('data'), new_building=GeoJSON_feature) returns
{"claims": [Claim JSON], "geo": Geo JSON}. Missing data yields limitations and
no fabricated claim. New buildings must carry properties.synthetic=true.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union

from geo.coordinates import TO_UTM, angle_difference, geographic, hotel_center, metric
from geo.facades import extract_facades
from geo.noise import noise_facade, source_samples
from geo.region import get_region, number
from geo.sun import monthly_paths, visible_sunset
from geo.terrain import build_local_dsm
from geo.views import los_to_point, profile_distances, sample_profiles, scan_profiles
from geo.walk import beach_route

VERSION = "okno-geo-1.0"
MC_ITERATIONS = 64
MC_SEED = 20261026
SEA_MIN_WIDTH_DEG = 10.0
AZIMUTH_STEP_DEG = 3.0


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()[:20]


def _empty_collection():
    return {"type": "FeatureCollection", "features": []}


def _ranges(flags):
    if not len(flags):
        return
    start = 0
    for i in range(1, len(flags)+1):
        if i == len(flags) or flags[i] != flags[start]:
            yield start, i-1, bool(flags[start])
            start = i


def _sample_heights(buildings, prior, rng):
    sampled = []
    for building in buildings:
        height, source = building["height_m"], building["height_source"]
        if source == "levels" and building.get("levels"):
            height = building["levels"]*rng.uniform(2.8, 3.5)+rng.uniform(0, 2)
        elif source in {"empirical_prior", "assumed_prior", "unknown"}:
            height = rng.choice(prior) if len(prior) else rng.uniform(3, 18)
        else:
            height = rng.normal(height, 1)
        sampled.append(max(2, height))
    return np.asarray(sampled)


def compute_hotel(hotel: dict, data_dir: Path, *, new_building: dict | None = None) -> dict:
    """Runtime-safe calculation; data_dir contains seed/ and geo/terrain_30m.npz."""
    data_dir = Path(data_dir)
    return compute_with_region(hotel, get_region(data_dir), data_dir/"geo", new_building=new_building)


def compute_with_region(hotel, region, output_dir, *, new_building=None, computed_at=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    hotel_id = hotel["id"]
    # IDs are file names in our artifacts, never accept traversal from API input.
    if "/" in hotel_id or "\\" in hotel_id or hotel_id in {".", ".."}:
        raise ValueError("unsafe hotel ID")
    lon, lat = hotel_center(hotel)
    xy = TO_UTM.transform(lon, lat)
    synthetic = bool(new_building)
    if new_building and not new_building.get("properties", {}).get("synthetic"):
        raise ValueError("new_building fixture must explicitly carry properties.synthetic=true")
    variant = "-change-"+_digest(new_building) if new_building else ""
    artifact_name = hotel_id+variant
    artifact = "/data/geo/"+artifact_name+".json"
    timestamp = computed_at or datetime.now(UTC)
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    limitation = ["Расчёт видимости не подтверждает наличие номеров или окон на фасаде; это вопрос отельеру",
                  "Copernicus GLO-30 — DSM: земля под зданиями оценена 10-м перцентилем окрестности; деревья и остатки крыш дают неопределённость",
                  "Береговая линия покрывает ограниченный регион; острова и дальние изгибы берега вне выгрузки неизвестны",
                  "Monte Carlo — чувствительность к допущениям, а не эмпирически измеренная точность"]
    geo = {"hotel_id": hotel_id, "center": [lon, lat], "building": None, "facades": [], "horizon": [],
           "sun_paths": [], "view_cones": _empty_collection(), "route": _empty_collection(),
           "crossings": _empty_collection(), "monte_carlo": {"iterations": 0, "seed": MC_SEED},
           "limitations": limitation, "synthetic": synthetic, "calculation_version": VERSION,
           "computed_at": timestamp.isoformat(), "artifact": artifact,
           "inputs": region.inputs, "unknown_characteristics": [], "landmarks": []}
    claims, arrays = [], {}

    def claim(characteristic, value, scope, confidence, method, *, extra_limitations=None, inputs=None):
        confidence = round(float(np.clip(confidence, 0, 1)), 4)
        record = {"hotel_id": hotel_id, "characteristic_id": characteristic, "spec_version": 1,
                  "scope": scope, "value": value, "source": "geo_engine", "method": method,
                  "confidence": confidence, "observed_at": timestamp.isoformat(),
                  "valid_until": (timestamp+timedelta(days=30 if characteristic == "quiet_night" else 90)).isoformat(),
                  "synthetic": synthetic}
        evidence_inputs = {"input_sha256": region.inputs, "version": VERSION, "building_id": geo.get("building_id"),
                           "mc_seed": MC_SEED, **(inputs or {})}
        if new_building:
            evidence_inputs["synthetic_new_building"] = new_building
        record["id"] = "geo-"+_digest(record)
        record["evidence"] = [{"id": "ev-"+record["id"], "kind": "simulation" if synthetic else "calculation",
                               "label": "Воспроизводимый георасчёт "+characteristic, "source_ids": region.source_ids or ["geo_engine"],
                               "artifact": artifact, "inputs": evidence_inputs, "output": value,
                               "limitations": limitation+(extra_limitations or [])}]
        claims.append(record)

    # Routing is independent of the footprint; retain it when visual inputs lack coverage.
    if region.graph and region.terrain:
        beaches = region.nearby("beaches", xy, 5000)
        route, warning = beach_route(region.graph, xy, beaches, region.nearby("roads", xy, 5000), region.nearby("railways", xy, 5000))
        if route:
            geo["route"] = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": route["geometry"],
                                                                       "properties": {"kind": "beach_walk", "minutes": route["minutes"]}}]}
            geo["crossings"] = {"type": "FeatureCollection", "features": route["crossing_features"]}
            geo["route_details"] = route
            value = {key: route[key] for key in ("available", "minutes", "return_minutes", "distance_m", "ascent_m", "return_ascent_m", "has_steps", "crossings", "beach_name")}
            value["route_ref"] = artifact+"#route"
            claim("beach_walk", value, {"type": "hotel"}, .66*route["elevation_coverage"], "directed_graph_dijkstra_tobler_v1",
                  extra_limitations=["Привязка входа отеля и пляжа к ближайшему узлу; короткий соединитель вне графа не подтверждён", "Высоты графа взяты из 30-м DSM, уклон ограничен ±30%; расписаний переходов нет"],
                  inputs={"node_ids": route["node_ids"], "return_node_ids": route["return_node_ids"], "snap_distance_m": route["snap_distance_m"]})
        else:
            geo["limitations"].append(warning)
            geo["unknown_characteristics"].append("beach_walk")
    else:
        geo["limitations"].append("Пеший граф или DEM отсутствует; время и подъём неизвестны")
        geo["unknown_characteristics"].append("beach_walk")

    buildings = region.buildings(xy, 1600)
    own, matched, match_distance = region.hotel_building(hotel, xy, buildings)
    geo["building_match"] = {"method": matched, "distance_m": match_distance}
    if new_building:
        properties = new_building["properties"]
        geometry = metric(shape(new_building["geometry"]))
        height = number(properties.get("height_m") or properties.get("height"))
        if not height or height <= 0:
            raise ValueError("synthetic building needs a positive height_m")
        buildings.append({"id": str(new_building.get("id", "synthetic-new-building")), "shape": geometry,
                          "height_m": height, "height_source": "synthetic_fixture", "levels": None,
                          "base_m": 0, "properties": properties, "feature": new_building})
    if own is None or region.terrain is None or not region.features["coastline"]:
        missing = "Нет надёжной привязки к зданию" if own is None else "DEM или береговая линия отсутствует"
        geo["limitations"].append(missing+"; пофасадные виды и этажи неизвестны")
        geo["unknown_characteristics"].extend(["sea_view", "sunset_view", "mountain_view", "quiet_night"])
        return _write_result(geo, claims, arrays, output_dir, artifact_name)
    geo["building_id"] = own["id"]
    geo["building"] = own["feature"]
    sea_covered = region.sea.has_coverage(xy)
    geo["coastline_coverage"] = sea_covered
    if not sea_covered:
        geo["unknown_characteristics"].append("sea_view")
        geo["limitations"].append("Нет картированной береговой линии в 2 км от наблюдателя: sea_view unknown, морские лучи не экстраполируются")
    floors = own.get("levels") or number(hotel.get("tags", {}).get("building:levels"))
    floor_known = floors is not None
    if floors is None and own["height_source"] in {"height", "overture", "overture_height"}:
        floors = max(1, int(own["height_m"]/3))
        geo["limitations"].append("Этажность выведена из общей высоты здания при 3 м/этаж; это не подтверждённая этажность номеров")
    if floors is None:
        geo["limitations"].append("Этажность здания неизвестна: считаем только условный первый уровень глаза 1,5 м, верхние этажи unknown")
        floors = 1
    floors = max(1, min(100, int(floors)))
    local, bid_grid, dsm = build_local_dsm(region.terrain, buildings, xy, radius_m=1200)
    if not np.isfinite(own["base_m"]):
        geo["limitations"].append("DEM не покрывает точку отеля")
        return _write_result(geo, claims, arrays, output_dir, artifact_name)
    arrays.update(near_ground=local.values, near_building_ids=bid_grid, near_dsm=dsm,
                  x_min=np.asarray(local.x_min), y_max=np.asarray(local.y_max), cell=np.asarray(local.cell),
                  building_heights=np.asarray([building["height_m"] for building in buildings], dtype=np.float32),
                  building_bases=np.asarray([building["base_m"] for building in buildings], dtype=np.float32))
    geo["dsm"] = {"near_cell_m": 2, "near_radius_m": 1200, "far_cell_m": region.terrain.cell, "far_radius_m": 30000,
                  "roof_method": "replace_with_low_percentile_base_plus_height", "artifact": "/data/geo/"+artifact_name+".npz"}
    geo["building_index"] = [{"id": building["id"], "height_m": building["height_m"], "height_source": building["height_source"],
                              "base_m": building["base_m"]} for building in buildings]
    heights = arrays["building_heights"].astype(float)
    bases = arrays["building_bases"].astype(float)
    eyes = own["base_m"]+np.arange(floors)*3+1.5
    geo["floors"] = {"count": floors if floor_known else None, "modeled_count": floors,
                     "floor_height_m": 3, "eye_offset_m": 1.5, "base_m": own["base_m"]}
    facades = extract_facades(own["shape"])
    azimuths = np.arange(0, 360, AZIMUTH_STEP_DEG)
    distances = profile_distances(1200)
    paths = monthly_paths(lon, lat, year=timestamp.year)
    geo["sun_paths"] = paths
    noise_sources = (source_samples(region.nearby("roads", xy, 650), kind="road")+
                     source_samples(region.nearby("railways", xy, 650), kind="rail")+
                     source_samples(region.nearby("pois", xy, 650), kind="bar"))
    targets = []
    for poi in region.nearby("pois", xy, 30000):
        props = poi["properties"]
        is_peak = props.get("natural") == "peak"
        target_height = number(props.get("height_m") or props.get("height"))
        if not is_peak and not (target_height and (props.get("tourism") == "attraction" or props.get("man_made") in {"tower", "lighthouse"})):
            continue
        point = poi["shape"].representative_point()
        ground_height = region.terrain.sample(np.asarray([point.x]), np.asarray([point.y]))[0]
        elevation = number(props.get("ele")) if is_peak else None
        target_z = elevation if elevation is not None else ground_height+(target_height or 0)
        if not np.isfinite(target_z):
            continue
        targets.append((poi, point, float(target_z), "peak" if is_peak else "landmark"))
    geo["target_coverage"] = {"named_peaks": sum(target[3] == "peak" for target in targets),
                              "landmarks_with_height": sum(target[3] == "landmark" for target in targets)}
    if not targets:
        geo["limitations"].append("В выгрузке нет именованных вершин/достопримечательностей с подходящей высотой: вид гор — геометрия рельефа, названия unknown")
    probabilities = []
    min_floors = []
    horizons = []
    terrain_horizons = []
    building_horizons = []
    sea_horizons = []
    rng = np.random.default_rng(MC_SEED)
    sampled_heights = [_sample_heights(buildings, region.height_prior, rng) for _ in range(MC_ITERATIONS)]
    shifts = rng.normal(0, 2.4, MC_ITERATIONS)
    floor_heights = rng.uniform(2.8, 3.5, MC_ITERATIONS)
    dependency_ids = set()
    for facade_index, facade in enumerate(facades):
        # Three distributed observers per compass facade, selected deterministically.
        observer_indices = np.linspace(0, len(facade["points"])-1, min(3, len(facade["points"]))).astype(int)
        profiles = []
        sea_masks, mountain_masks, blockers_all, horizon_all, coverage_all = [], [], [], [], []
        terrain_parts, building_parts = [], []
        for pi in observer_indices:
            point, normal = facade["points"][pi], facade["normals"][pi]
            ground, bids, sea = sample_profiles(point, azimuths, distances, local, bid_grid, region.terrain, region.sea)
            scan = scan_profiles(ground, bids, sea, distances, heights, bases, eyes)
            facing = angle_difference(azimuths, normal) <= 75
            sea_masks.append(scan[0] & facing)
            mountain_masks.append(scan[1] & facing)
            blockers_all.append(scan[2])
            horizon_all.append(scan[6])
            coverage_all.append(scan[5])
            terrain_parts.append(scan[3])
            building_parts.append(scan[4])
            profiles.append((ground, bids, sea, facing, normal))
            dependency_ids.update(buildings[int(i)]["id"] for i in np.unique(bids) if i >= 0)
        sea_width = np.mean([mask.sum(axis=1)*AZIMUTH_STEP_DEG for mask in sea_masks], axis=0)
        mountain_width = np.mean([mask.sum(axis=1)*AZIMUTH_STEP_DEG for mask in mountain_masks], axis=0)
        horizon = np.mean(horizon_all, axis=0)
        sea_available = sea_width >= SEA_MIN_WIDTH_DEG
        mountain_available = mountain_width >= SEA_MIN_WIDTH_DEG
        # Select one of the actual facade observers per trial to include window position.
        sea_trials = np.zeros((MC_ITERATIONS, floors), dtype=bool)
        mountain_trials = np.zeros_like(sea_trials)
        sunset_trials = np.zeros((MC_ITERATIONS, 12, floors), dtype=bool)
        for k in range(MC_ITERATIONS):
            ground, bids, sea, facing, normal = profiles[k % len(profiles)]
            trial_eyes = own["base_m"]+shifts[k]+np.arange(floors)*floor_heights[k]+1.5
            scan = scan_profiles(ground, bids, sea, distances, sampled_heights[k], bases, trial_eyes, shifts[k])
            sea_trials[k] = (scan[0] & facing).sum(axis=1)*AZIMUTH_STEP_DEG >= SEA_MIN_WIDTH_DEG
            mountain_trials[k] = (scan[1] & facing).sum(axis=1)*AZIMUTH_STEP_DEG >= SEA_MIN_WIDTH_DEG
            sun = visible_sunset(paths, azimuths, scan[6], normal)
            sunset_trials[k] = np.asarray([month["available"] for month in sun])
        p_sea, p_mountain = sea_trials.mean(axis=0), mountain_trials.mean(axis=0)
        probability_sun = sunset_trials.mean(axis=0)
        coverage = float(np.mean(coverage_all))
        min_floor = int(np.flatnonzero(sea_available)[0])+1 if np.any(sea_available) else None
        min_p80 = int(np.flatnonzero(p_sea >= .8)[0])+1 if np.any(p_sea >= .8) else None
        if min_floor:
            min_floors.append(min_floor)
        probabilities.append(float(p_sea[-1]))
        outward = angle_difference(azimuths, float(np.mean(facade["normals"]))) <= 75
        blocked_ids = sorted({buildings[int(index)]["id"] for blocker in blockers_all for index in np.unique(blocker[:, outward])
                              if index >= 0 and buildings[int(index)]["id"] != own["id"]})
        influencing_ids = set(blocked_ids)
        if not influencing_ids:
            for _, bids, _, facing, _ in profiles:
                influencing_ids.update(buildings[int(index)]["id"] for index in np.unique(bids[facing])
                                       if index >= 0 and buildings[int(index)]["id"] != own["id"])
        blockers = [building for building in buildings if building["id"] in influencing_ids]
        height_coverage = float(np.mean([building["height_source"] in {"height", "levels", "overture", "overture_height", "synthetic_fixture"}
                                        for building in blockers])) if blockers else 1.0
        quality = (.4+.5*height_coverage)*min(1, coverage/.75)*(.85 if matched == "nearest_30m" else 1)
        if not floor_known:
            quality *= .7
        normal = float(np.degrees(np.arctan2(np.mean(np.sin(np.radians(facade["normals"]))), np.mean(np.cos(np.radians(facade["normals"])))))) % 360
        sunset = visible_sunset(paths, azimuths, horizon, normal)
        visible_peaks = []
        for poi, target_point, target_z, target_kind in targets:
            from geo.coordinates import azimuth
            if angle_difference(azimuth(target_point.x-xy[0], target_point.y-xy[1]), normal) > 75:
                continue
            observer = facade["points"][observer_indices[0]]
            result = los_to_point(observer, float(eyes[-1]), (target_point.x, target_point.y), target_z,
                                  region.terrain, buildings)
            geo["landmarks"].append({"id": poi["id"], "name": poi["properties"].get("name"), "kind": target_kind,
                                     "facade": facade["code"], "floor": floors, "target_elevation_m": target_z, **result})
            if target_kind == "peak" and result["visible"]:
                visible_peaks.append({"osm_id": poi["id"], "name": poi["properties"].get("name")})
        public_facade = {"code": facade["code"], "azimuth_deg": normal,
                         "geometry": mapping(geographic(unary_union(facade["edges"]))),
                         "observer_points": [list(geographic(Point(facade["points"][i])).coords)[0] for i in observer_indices],
                         "min_floor_sea": min_floor, "min_floor_sea_p80": min_p80,
                         "min_floor_sunset": min((i+1 for month in sunset for i, available in enumerate(month["available"]) if available), default=None),
                         "sea_width_by_floor": np.round(sea_width, 1).tolist(), "sea_probability_by_floor": p_sea.tolist(),
                         "mountain_width_by_floor": np.round(mountain_width, 1).tolist(), "sunset_by_month": sunset,
                         "confidence": round(quality, 3), "blocking_building_ids": blocked_ids,
                         "height_coverage": float(height_coverage), "influencing_building_ids": sorted(influencing_ids),
                         "nearby_height_coverage": float(np.mean([building["height_source"] in {"height", "levels", "overture", "overture_height", "synthetic_fixture"} for building in buildings])) if buildings else 0,
                         "terrain_coverage": coverage}
        noise = noise_facade(facade, noise_sources, buildings)
        if noise:
            public_facade.update({key: noise[key] for key in ("night_db_min", "night_db_max", "day_db_min", "day_db_max")})
            value = {key: noise[key] for key in ("available", "facade", "night_db_min", "night_db_max", "day_db_min", "day_db_max", "index", "sources")}
            claim("quiet_night", value, {"type": "facade", "facade": facade["code"]}, .38,
                  "outdoor_noise_proxy_v1", extra_limitations=["Приближённая модель наружного шума, не измерение дБ и не характеристика шумоизоляции", "Нет реальных потоков машин/поездов, расписаний и калибровки; диапазон ±8 дБ — допущение", "Бары с неизвестными часами учитываются как возможные ночные источники"],
                  inputs={"source_details": noise["source_details"], "reference_model": "geo/noise.py:ROAD_REFERENCE"})
        else:
            public_facade["noise_unknown"] = True
        for characteristic, available, widths, probs in (("sea_view", sea_available, sea_width, p_sea), ("mountain_view", mountain_available, mountain_width, p_mountain)):
            if characteristic == "sea_view" and not sea_covered:
                continue
            threshold = int(np.flatnonzero(available)[0])+1 if np.any(available) else None
            for lo, hi, yes in _ranges(available):
                value = {"available": yes, "facade": facade["code"], "min_floor": threshold,
                         "probability": round(float(np.mean(probs[lo:hi+1])), 4)}
                if characteristic == "sea_view":
                    value.update(sea_width_deg=round(float(np.mean(widths[lo:hi+1])), 1),
                                 sea_fraction=round(float(min(1, np.mean(widths[lo:hi+1])/150)), 3), blocking_building_ids=blocked_ids)
                else:
                    value.update(peaks=visible_peaks if hi == floors-1 else [], width_deg=round(float(np.mean(widths[lo:hi+1])), 1))
                outcome_probability = float(np.mean(probs[lo:hi+1] if yes else 1-probs[lo:hi+1]))
                claim(characteristic, value, {"type": "floor_range", "facade": facade["code"], "floor_min": lo+1, "floor_max": hi+1},
                      outcome_probability*quality, "two_scale_dsm_los_mc_v1", inputs={"floor_height_m": 3, "eye_offset_m": 1.5,
                      "curvature_coefficient": 6/7, "azimuth_step_deg": AZIMUTH_STEP_DEG, "window_half_angle_deg": 75,
                      "minimum_angular_width_deg": SEA_MIN_WIDTH_DEG, "iterations": MC_ITERATIONS,
                      "building_height_coverage": float(height_coverage)})
        for month in sunset:
            flags = month["available"]
            first = next((i+1 for i, flag in enumerate(flags) if flag), None)
            for lo, hi, yes in _ranges(flags):
                p = probability_sun[month["month"]-1, lo:hi+1]
                value = {"available": yes, "min_floor": first, "facade": facade["code"], "months": [month["month"]],
                         "azimuth_deg": month["azimuth_deg"], "visible_minutes": round(float(np.mean(month["visible_minutes"][lo:hi+1])), 1),
                         "probability": round(float(np.mean(p)), 4)}
                claim("sunset_view", value, {"type": "floor_range", "facade": facade["code"], "floor_min": lo+1, "floor_max": hi+1, "month": month["month"]},
                      np.mean(p if yes else 1-p)*quality, "astral_monthly_horizon_mc_v1",
                      extra_limitations=["Репрезентативный день — 15-е число месяца; облачность не моделируется; солнечный диск радиусом 0,27°"],
                      inputs={"representative_date": month["date"], "sampling_minutes": 2, "minimum_visible_minutes": 4})
        geo["facades"].append(public_facade)
        arrays[f"facade_{facade_index}_sea_width"] = sea_width.astype(np.float32)
        arrays[f"facade_{facade_index}_sea_probability"] = p_sea.astype(np.float32)
        arrays[f"facade_{facade_index}_horizon"] = horizon.astype(np.float32)
        arrays[f"facade_{facade_index}_sunset_probability"] = probability_sun.astype(np.float32)
        # All azimuths are sampled, but only outward half-spaces enter room claims.
        horizons.append(horizon[-1])
        terrain_horizons.append(np.mean(terrain_parts, axis=0))
        building_horizons.append(np.mean(building_parts, axis=0))
        visible_rays = np.mean(sea_masks, axis=0)[-1] > .5
        sea_horizons.append(visible_rays)
        for angle in azimuths[visible_rays]:
            end_xy = (xy[0]+1500*np.sin(np.radians(angle)), xy[1]+1500*np.cos(np.radians(angle)))
            geo["view_cones"]["features"].append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[lon, lat], list(geographic(Point(end_xy)).coords)[0]]},
                                                      "properties": {"facade": facade["code"], "floor": floors, "target": "sea", "synthetic": synthetic}})
    if horizons:
        terrain_horizon = np.mean(terrain_horizons, axis=0)
        building_horizon = np.mean(building_horizons, axis=0)
        sea_horizon = np.any(sea_horizons, axis=0)
        geo["horizon"] = [{"azimuth_deg": float(angle), "terrain_deg": float(terrain_value), "buildings_deg": float(building_value), "sea": bool(water)}
                           for angle, terrain_value, building_value, water in zip(azimuths, terrain_horizon, building_horizon, sea_horizon, strict=True)]
    geo["dependency_building_ids"] = sorted(dependency_ids)
    geo["monte_carlo"] = {"iterations": MC_ITERATIONS, "seed": MC_SEED,
                          "probability": max(probabilities, default=0), "min_floor_p80": min((facade["min_floor_sea_p80"] for facade in geo["facades"] if facade["min_floor_sea_p80"]), default=None),
                          "note": "Чувствительность модели к неопределённым высотам, DEM и положению окна; не измеренная точность"}
    return _write_result(geo, claims, arrays, output_dir, artifact_name)


def _write_result(geo, claims, arrays, output_dir, name):
    geo["claim_ids"] = [claim["id"] for claim in claims]
    # JSON contains the exact claim inputs/outputs; evidence can be followed offline.
    document = {**geo, "claims": claims}
    path = output_dir/(name+".json")
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(_json(document)+"\n")
    temporary.replace(path)
    np.savez_compressed(output_dir/(name+".npz"), **(arrays or {"calculation_available": np.asarray(False)}))
    return {"claims": claims, "geo": geo}
