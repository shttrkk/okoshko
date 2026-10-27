"""Precompute curated top-of-contour LOS without rebuilding existing geo NPZ."""
from __future__ import annotations

import numpy as np
import yaml

from geo.coordinates import TO_UTM
from geo.io import read_json
from geo.region import Region, load_features
from geo.terrain import Grid
from geo.tz_landmarks import TwoScaleGround, contour_target, landmark_visibility, line_obstacles
from pipeline.common import SEED, dump, gzip_json, sha
from pipeline.tz_common import baseline_sources, observation, parameters, spec_input, write_stage


def run():
    catalog_path = SEED.parent / "landmarks/sochi.yaml"
    catalog = yaml.safe_load(catalog_path.read_text())
    region = Region(SEED.parent)
    features = {f["id"]: f for f in region.features["buildings"]}
    features.update({f["id"]: f for f in load_features(SEED / "tz_areas.geojson")})
    landmarks, rejected = [], []
    for landmark in catalog["landmarks"]:
        feature = features.get(landmark["osm_id"])
        if feature is None or not landmark["osm_id"].startswith("osm-"):
            rejected.append({"id": landmark["id"], "reason": "exact OSM contour missing"})
            continue
        properties = feature["properties"]
        if landmark["height_source"] == "osm_height" and float(properties.get("height", "nan")) != landmark["height_m"]:
            raise ValueError("curated height does not match the original OSM tag")
        if landmark["height_source"] == "osm_building_levels_3m" and float(properties.get("building:levels", "nan"))*3 != landmark["height_m"]:
            raise ValueError("curated levels do not match the original OSM tag")
        target = contour_target(feature["shape"], region.terrain, height_m=landmark["height_m"])
        if target is None:
            rejected.append({"id": landmark["id"], "reason": "no finite DSM cell centre within exact contour"})
            continue
        target["height_source"] = landmark["height_source"]+"_and_copernicus_dsm"
        landmarks.append({**landmark, "target": target})
    rows, rays = [], {}
    thresholds = parameters("landmark_view")
    inputs = {"/data/landmarks/sochi.yaml": sha(catalog_path),
              "/data/seed/buildings.geojson.gz": sha(SEED / "buildings.geojson.gz"), "/data/geo/terrain_30m.npz": sha(SEED.parent / "geo/terrain_30m.npz")}
    inputs["/data/seed/tz_areas.geojson.gz"] = sha(SEED / "tz_areas.geojson.gz")
    inputs.update([spec_input("landmark_view")])
    fisht = read_json(SEED / 'tz_fisht_manifest.json')
    if fisht.get('confirmed_height') is not None:
        raise ValueError('Fisht confirmed height is now available; review curated target before publishing LOS')
    inputs.update({'/data/seed/'+name: sha(SEED / name) for name in ('tz_fisht_manifest.json', 'tz_fisht.geojson.gz')})
    for hotel in read_json(SEED / "hotels.json"):
        path = SEED.parent / "geo" / (hotel["id"]+".json")
        arrays_path = path.with_suffix(".npz")
        snapshot = read_json(path) if path.exists() else {}
        emitted_targets, hotel_rays = set(), []
        if snapshot.get("floors", {}).get("count") and arrays_path.exists():
            with np.load(arrays_path, allow_pickle=False) as arrays:
                local = Grid(arrays["near_ground"], float(arrays["x_min"]), float(arrays["y_max"]), float(arrays["cell"]))
            ground = TwoScaleGround(local, region.terrain)
            xy = TO_UTM.transform(hotel["lon"], hotel["lat"])
            observers = [TO_UTM.transform(*point) for facade in snapshot.get("facades", []) for point in facade.get("observer_points", [])]
            for landmark in landmarks:
                obstacles = line_obstacles(region, xy, landmark["target"]["xy"], observers)
                for result in landmark_visibility(snapshot, landmark, ground, obstacles, ray_records=hotel_rays, **thresholds):
                    result["value"]["calculation_parameters"] = thresholds
                    result["scope"]["landmark_id"] = result["value"]["landmark_id"]
                    rows.append(observation(hotel["id"], "landmark_view", result["value"], scope=result["scope"], source="tz_landmark_engine", ttl_days=90, confidence=.45,
                                input_sha256={"/data/geo/"+p.name: sha(p) for p in (path, arrays_path)},
                                source_ids=["tz_landmark_engine", "osm_geofabrik", "copernicus_dem"]))
                    emitted_targets.add(landmark['id'])
        rays[hotel['id']] = hotel_rays
        for landmark in catalog['landmarks']:
            if landmark['id'] not in emitted_targets:
                reason = ('Нет подтверждённой этажности или сохранённой геометрии наблюдателя' if not snapshot.get('floors', {}).get('count') or not arrays_path.exists()
                          else 'Цель вне рассчитанного сектора фасадов/дальности либо недостаточно DSM; видимость неизвестна')
                rows.append(observation(hotel['id'], 'landmark_view', {'available': None, 'landmark_id': landmark['id'], 'name': landmark['name'],
                                        'height_available': True, 'record_type': 'target_metadata', 'unknown_reason': reason},
                                        scope={'type': 'hotel', 'landmark_id': landmark['id']}, source='tz_landmark_engine',
                                        method='tz_landmark_metadata@1', informational_observation=True, confidence=.45,
                                        source_ids=['tz_landmark_engine', 'osm_geofabrik', 'copernicus_dem']))
        rows.append(observation(hotel['id'], 'landmark_view', {'available': None, 'landmark_id': 'fisht', 'name': 'Фишт',
                                'height_available': False, 'record_type': 'target_metadata', 'osm_id': fisht['osm_id'], 'wikidata_id': fisht['wikidata_id'],
                                'unknown_reason': 'Нет подтверждённой высоты OSM/Wikidata; видимость не рассчитана'},
                                scope={'type': 'hotel', 'landmark_id': 'fisht'}, source='tz_landmark_engine', method='tz_landmark_metadata@1',
                                informational_observation=True, confidence=.45, source_ids=['tz_landmark_engine', 'osm_geofabrik', 'tz_wikidata_fisht']))
    gzip_json(SEED / 'tz_landmark_rays.json.gz', {'rays': rays, 'inputs': inputs, 'method': 'tz_landmark_engine@1',
              'semantics': 'Actual saved LOS checks per observer/floor; 2D lines with separate approximate DSM elevation metadata; no ray for Fisht'})
    sources = baseline_sources()
    engine = {**sources["geo_engine"], "id": "tz_landmark_engine", "name": "О!кно curated landmark LOS", "notes": "Exact curated OSM target contours, existing per-hotel near-ground raster and Copernicus DSM; no invented heights"}
    manifest = write_stage("landmarks", rows, [engine, sources["osm_geofabrik"], sources["copernicus_dem"], *read_json(SEED / 'tz_fisht_sources.json')], inputs=inputs, limitations=[
        "Видимость верхней части моделируемого контура, не всего объекта и не наличия окна/номера",
        "Сектор фасада ±60°, дальность 30 км; этажи только с известной этажностью, одинаковые интервалы объединены",
        "Ближняя земля из исходного geo NPZ, дальняя из того же Copernicus DSM; здания без высоты остаются только в грубом DSM, высота не выдумывается",
        "Высоты зданий и DSM приблизительны; результат не является полевой проверкой вида"])
    manifest.update(landmarks=landmarks, rejected=rejected, excluded_candidates=catalog.get("excluded_candidates", []))
    dump(SEED / "tz_landmarks_manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    run()
