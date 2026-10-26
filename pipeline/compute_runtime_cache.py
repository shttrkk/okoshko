"""Offline compact LOS cache for instantaneous additive-building scenarios."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from geo.coordinates import TO_UTM, hotel_center
from geo.facades import extract_facades
from geo.region import Region
from geo.service import MC_ITERATIONS, MC_SEED, _sample_heights
from geo.terrain import Grid
from geo.views import profile_distances, sample_profiles, scan_profiles


def build_cache(hotel, region, geo_dir):
    path = geo_dir/(hotel["id"]+".json")
    metadata = json.loads(path.read_text())
    if not metadata["facades"]:
        return None
    with np.load(geo_dir/(hotel["id"]+".npz"), allow_pickle=False) as local:
        ground = Grid(local["near_ground"], float(local["x_min"]), float(local["y_max"]), float(local["cell"]))
        ids = local["near_building_ids"]
        heights, bases = local["building_heights"].astype(float), local["building_bases"].astype(float)
    xy = TO_UTM.transform(*hotel_center(hotel))
    buildings = region.buildings(xy, 1600)
    lookup = {building["id"]: building for building in buildings}
    buildings = [lookup[building["id"]] for building in metadata["building_index"]]
    own = lookup[metadata["building_id"]]
    facades = extract_facades(own["shape"])
    floors = metadata["floors"]["modeled_count"]
    own_base = metadata["floors"]["base_m"]
    eyes = own_base+np.arange(floors)*3+1.5
    azimuths, distances = np.arange(0, 360, 3), profile_distances(1200)
    rng = np.random.default_rng(MC_SEED)
    sampled_heights = [_sample_heights(buildings, region.height_prior, rng) for _ in range(MC_ITERATIONS)]
    shifts = rng.normal(0, 2.4, MC_ITERATIONS)
    floor_heights = rng.uniform(2.8, 3.5, MC_ITERATIONS)
    trial_eyes = own_base+shifts[:, None]+np.arange(floors)[None, :]*floor_heights[:, None]+1.5
    output = {"eyes": eyes, "azimuths": azimuths, "mc_eyes": trial_eyes, "mc_shifts": shifts,
              "baseline_sha256": np.frombuffer(hashlib.sha256(path.read_bytes()).hexdigest().encode(), dtype=np.uint8)}
    for fi, facade in enumerate(facades):
        selected = np.linspace(0, len(facade["points"])-1, min(3, len(facade["points"]))).astype(int)
        points = np.asarray(facade["points"])[selected]
        normals = np.asarray(facade["normals"])[selected]
        prefix = f"f{fi}_"
        output[prefix+"points"], output[prefix+"normals"] = points, normals
        profiles = [sample_profiles(point, azimuths, distances, ground, ids, region.terrain, region.sea) for point in points]
        deterministic = [scan_profiles(p[0], p[1], p[2], distances, heights, bases, eyes) for p in profiles]
        keys = {"horizon": 6, "sea_nearest": 7, "sea_slope": 8, "mountain_nearest": 9, "mountain_slope": 10}
        for key, index in keys.items():
            output[prefix+key] = np.asarray([result[index] for result in deterministic], dtype=np.float32)
        trials = []
        for k in range(MC_ITERATIONS):
            p = profiles[k % len(profiles)]
            trials.append(scan_profiles(p[0], p[1], p[2], distances, sampled_heights[k], bases, trial_eyes[k], shifts[k]))
        for key, index in keys.items():
            output[prefix+"mc_"+key] = np.asarray([result[index] for result in trials], dtype=np.float32)
    cache_path = geo_dir/(hotel["id"]+".los.npz")
    np.savez_compressed(cache_path, **output)
    return cache_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--geo-dir", type=Path, default=Path("data/geo"))
    parser.add_argument("--hotel-id", action="append", required=True)
    args = parser.parse_args()
    region = Region(args.data_dir, terrain_path=args.geo_dir/"terrain_30m.npz")
    seed = args.data_dir/"seed" if (args.data_dir/"seed").exists() else args.data_dir
    hotels = json.loads((seed/"hotels.json").read_text())
    for hotel in hotels:
        if hotel["id"] in args.hotel_id:
            result = build_cache(hotel, region, args.geo_dir)
            print(str(result), flush=True)
