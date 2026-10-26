"""Isolated source-to-geo rebuild, or read-only validation of committed outputs.

The default run may download open datasets and overwrites derived data. Use
--dry-run to inspect the DAG, --from-cache to recompute geo from existing seed
and cached DEM TIFFs, or --validate-only to check existing outputs without writes.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
GEO = DATA / "geo"
HERO = "osm-node-10058556917"
STAGES = (
    "fetch_osm", "fetch_overture", "merge_hotels", "fetch_dem", "build_dsm",
    "fetch_open", "fetch_ookla", "fetch_tiles", "export_seed", "compute_views", "compact_geo",
    "compute_runtime_cache", "compact_seed", "validate", "compute_validate",
)
DEPENDENCIES = {
    "fetch_osm": (), "fetch_overture": ("fetch_osm",), "merge_hotels": ("fetch_overture",),
    "fetch_dem": ("merge_hotels",), "build_dsm": ("fetch_dem",),
    "fetch_open": ("merge_hotels",), "fetch_ookla": ("fetch_overture",), "fetch_tiles": (),
    "export_seed": ("fetch_dem", "fetch_open", "fetch_ookla", "fetch_tiles"),
    "compute_views": ("build_dsm", "export_seed"), "compact_geo": ("compute_views",),
    "compute_runtime_cache": ("compute_views",),
    "compact_seed": ("export_seed", "compute_runtime_cache"), "validate": ("compact_seed",),
    "compute_validate": ("validate", "compute_views", "compute_runtime_cache"),
}
CACHED_STAGES = ("build_dsm", "compute_views", "compact_geo", "compute_runtime_cache", "compact_seed", "validate", "compute_validate")


def dem_paths(config):
    return [DATA / "raw" / (f"Copernicus_DSM_COG_10_{'N' if lat >= 0 else 'S'}{abs(lat):02d}_00_"
            f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}_00_DEM.tif") for lat, lon in config["dem"]["tiles"]]


def stage_plan(args, config):
    selected = list(("validate", "compute_validate") if args.validate_only else CACHED_STAGES if args.from_cache else STAGES)
    if "compact_geo" in selected and not args.compact_geo:
        selected.remove("compact_geo")
    if args.from_stage:
        selected = selected[selected.index(args.from_stage):]
    if args.to_stage:
        selected = selected[:selected.index(args.to_stage) + 1]
    plan = []
    for name in selected:
        item = {"stage": name, "depends_on": list(DEPENDENCIES[name])}
        if name == "compute_runtime_cache" and "compact_geo" in selected:
            item["depends_on"] = ["compact_geo"]
        if name == "build_dsm":
            item.update(inputs=[str(p.relative_to(ROOT)) for p in dem_paths(config)],
                        output="data/geo/terrain_30m.npz", bbox_wgs84=args.terrain_bbox, resolution_m=30,
                        call="pipeline.build_dsm.convert(inputs, output, bbox=bbox_wgs84, resolution=30)")
        elif name == "compute_views":
            item.update(hotels="all configured hotels", workers=args.workers, terrain="data/geo/terrain_30m.npz")
        elif name == "compact_geo":
            item["command"] = ["python", "-m", "pipeline.compact_geo", "--data-dir", "data", "--apply"]
        elif name == "compute_runtime_cache":
            item["hotel_ids"] = "all hotels with facades" if args.all_runtime_caches else args.cache_hotel_id or [HERO]
        plan.append(item)
    return plan


def hotels(config):
    values = json.loads((DATA / "seed/hotels.json").read_text())
    expected = sum(zone["hotel_limit"] for zone in config["zones"].values())
    if len(values) != expected or len({h["id"] for h in values}) != expected:
        raise ValueError(f"Expected {expected} unique configured hotels, got {len(values)}")
    return values


def verify_dem(config):
    paths = dem_paths(config)
    for path in paths:
        meta = path.with_suffix(path.suffix + ".metadata.json")
        if not path.is_file() or not meta.is_file():
            raise ValueError(f"Cached DEM and download metadata required: {path}")
        record = json.loads(meta.read_text())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if record["sha256"] != digest or record["bytes"] != path.stat().st_size:
            raise ValueError(f"DEM cache checksum/size mismatch: {path}")
    return paths


def cache_ids(args, values):
    ids = {h["id"] for h in values}
    requested = ids if args.all_runtime_caches else set(args.cache_hotel_id or [HERO, "osm-node-11866202895"])
    if requested - ids:
        raise ValueError(f"Unknown runtime cache hotels: {sorted(requested - ids)}")
    return requested


def validate_caches(values, *, required=()):
    import numpy as np
    checked, eligible = 0, 0
    for hotel in values:
        baseline = GEO / (hotel["id"] + ".json")
        document = json.loads(baseline.read_text())
        cache = GEO / (hotel["id"] + ".los.npz")
        eligible += bool(document["facades"])
        if not cache.is_file():
            if hotel["id"] in required and document["facades"]:
                raise ValueError(f"Missing requested runtime cache: {cache}")
            continue
        with np.load(cache, allow_pickle=False) as arrays:
            assert all(a.dtype.kind in "biuf" for a in arrays.values()), str(cache)
            digest = arrays["baseline_sha256"].astype(np.uint8).tobytes().decode()
            assert digest == hashlib.sha256(baseline.read_bytes()).hexdigest(), str(cache)
        checked += 1
    print(json.dumps({"runtime_caches_validated": checked, "hotels_with_facades": eligible,
                      "limitation": "Hotels without a saved LOS cache use the full regional calculation."}), flush=True)


@contextlib.contextmanager
def offline_network_guard():
    """Fail rather than silently download when reusing cached inputs."""
    import socket
    from unittest.mock import patch

    def unavailable(*args, **kwargs):
        raise RuntimeError("Network is disabled in --from-cache/--validate-only mode")

    with patch.object(socket.socket, "connect", unavailable), patch.object(socket.socket, "connect_ex", unavailable):
        yield


def validate_seed(*, read_only):
    module = importlib.import_module("pipeline.validate")
    if not read_only:
        module.run()
        return
    # The legacy validator writes only its report through this helper.
    from unittest.mock import patch
    with patch.object(module, "dump", lambda path, value: None):
        module.run()


def validate_geo(*, read_only):
    module = importlib.import_module("pipeline.compute_validate")
    if read_only:
        # Redirect its report into a temporary directory; artifacts remain read-only.
        with tempfile.TemporaryDirectory(prefix="okno-validate-") as name:
            temporary = Path(name)
            for path in GEO.iterdir():
                if path.is_file() and path.name != "validation.json":
                    (temporary / path.name).symlink_to(path)
            report = module.validate(DATA, temporary)
    else:
        report = module.validate(DATA, GEO)
    print(json.dumps({"validated_hotels": report["hotels"], "validated_geo_claims": report["claims"]}), flush=True)


def execute_stage(name, args, config):
    if name == "build_dsm":
        module = importlib.import_module("pipeline.build_dsm")
        module.convert(verify_dem(config), GEO / "terrain_30m.npz", bbox=args.terrain_bbox, resolution=30)
    elif name == "compute_views":
        hotels(config)
        module = importlib.import_module("pipeline.compute_views")
        module.compute_all(DATA, GEO, workers=args.workers)
    elif name == "compact_geo":
        subprocess.run([sys.executable, "-m", "pipeline.compact_geo", "--data-dir", "data", "--apply"], cwd=ROOT, check=True)
    elif name == "compute_runtime_cache":
        from geo.region import Region
        module = importlib.import_module("pipeline.compute_runtime_cache")
        values = hotels(config)
        requested = cache_ids(args, values)
        region = Region(DATA, terrain_path=GEO / "terrain_30m.npz")
        for hotel in values:
            if hotel["id"] in requested:
                print(json.dumps({"runtime_cache": str(module.build_cache(hotel, region, GEO)), "hotel_id": hotel["id"]}), flush=True)
        validate_caches(values, required=requested)
    elif name == "validate":
        hotels(config)
        validate_seed(read_only=args.validate_only)
    elif name == "compute_validate":
        validate_geo(read_only=args.validate_only)
        validate_caches(hotels(config))
    elif name == "fetch_ookla":
        # Overture can hit its JSON cache and skip extension installation. A
        # fresh pipeline image still needs these before fetch_ookla's LOAD.
        import duckdb
        connection = duckdb.connect()
        try:
            connection.execute("INSTALL httpfs; INSTALL spatial;")
        finally:
            connection.close()
        importlib.import_module("pipeline.fetch_ookla").run()
    else:
        importlib.import_module("pipeline." + name).run()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=os.environ.get("REGION", "pipeline/regions/sochi.yaml"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--from-cache", action="store_true", help="Reuse existing exported seed/tiles and cached DEM TIFFs; rebuild all geo outputs offline (not a raw-source rebuild)")
    mode.add_argument("--validate-only", action="store_true", help="Validate existing seed, geo claims and saved LOS caches offline without changing outputs")
    parser.add_argument("--dry-run", action="store_true", help="Print the ordered DAG and parameters without importing stages or changing data")
    parser.add_argument("--from-stage", choices=STAGES, help="Start here; preceding artifacts must already exist")
    parser.add_argument("--to-stage", choices=STAGES, help="Stop after this stage")
    parser.add_argument("--workers", type=int, default=1, help="Offline compute_views fork workers (default: 1)")
    parser.add_argument("--compact-geo", action="store_true", help="Optional offline pipeline.compact_geo CLI hook after compute_views and before LOS caches; requires that module")
    cache = parser.add_mutually_exclusive_group()
    cache.add_argument("--cache-hotel-id", action="append", help=f"Save LOS cache for this hotel (repeatable; default: {HERO})")
    cache.add_argument("--all-runtime-caches", action="store_true", help="Explicitly save LOS caches for all hotels with facades; consumes substantially more disk")
    parser.add_argument("--terrain-bbox", nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"), help="Long-range DSM bbox; required for regions other than sochi")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be positive")
    import yaml
    region = Path(args.region)
    region = region if region.is_absolute() else ROOT / region
    config = yaml.safe_load(region.read_text())
    if args.terrain_bbox is None:
        if config["id"] != "sochi":
            parser.error("--terrain-bbox is required for a region other than sochi")
        args.terrain_bbox = [39.35, 43.10, 40.45, 43.98]
    west, south, east, north = args.terrain_bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        parser.error("--terrain-bbox must be WEST SOUTH EAST NORTH in WGS84")
    if args.validate_only and (args.from_stage or args.to_stage):
        parser.error("--validate-only cannot be combined with stage boundaries")
    os.environ["REGION"] = str(region)
    try:
        plan = stage_plan(args, config)
    except ValueError:
        parser.error("Stage boundaries must occur in the selected mode and in increasing order")
    print(json.dumps({"mode": "validate-only" if args.validate_only else "from-cache" if args.from_cache else "full-rebuild",
                      "region": str(region), "expected_hotels": sum(z["hotel_limit"] for z in config["zones"].values()),
                      "optional_compact_geo": {"enabled": any(s["stage"] == "compact_geo" for s in plan),
                                               "after": "compute_views", "before": "compute_runtime_cache"},
                      "stages": plan}, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return
    guard = offline_network_guard() if args.from_cache or args.validate_only else contextlib.nullcontext()
    with guard:
        if args.from_cache:
            # Reject stale/corrupt cached inputs before writing new calculations.
            hotels(config)
            verify_dem(config)
            validate_seed(read_only=True)
        for item in plan:
            print("Starting " + item["stage"], flush=True)
            execute_stage(item["stage"], args, config)


if __name__ == "__main__":
    main()
