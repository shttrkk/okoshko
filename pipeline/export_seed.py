"""Validated seed tables and immutable open-data claims with local evidence."""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import yaml
from shapely import wkt
from shapely.geometry import Point

from backend.contracts.models import Claim, Source
from pipeline.common import (
    CONFIG,
    CONFIG_PATH,
    COVERAGE_BBOXES,
    RAW,
    ROOT,
    SEED,
    ZONE_A,
    ZONE_B,
    dump,
    output_metadata,
    read,
    sha,
    source,
    table,
    utcnow,
)


def load_stages():
    stages = {name: read(RAW / (name + "_stage.json")) for name in ["osm", "overture", "dem", "open", "ookla", "tiles"] if (RAW / (name + "_stage.json")).exists()}
    registry = {}
    for stage in stages.values():
        for s in stage.get("sources", []) + ([stage["source"]] if stage.get("source") else []):
            registry.setdefault(s["id"], Source.model_validate(s).model_dump(mode="json"))
    timestamp = max(s["retrieved_at"] for s in registry.values())
    registry["geo_engine"] = Source.model_validate(source("geo_engine", "О!кно: расчёты геометрии, маршрутов и фасадов", "/api/tools",
        "Расчёт по OSM ODbL и Copernicus DEM", "О!кно geo engine; методы и входы в evidence каждого расчёта", timestamp, timestamp,
        "Computed source requested by the geo integration; not an independent external dataset.", kind="computed")).model_dump(mode="json")
    return stages, registry


def ttl_days(characteristic_id: str) -> int:
    """Claim validity follows the characteristic definition, one source of truth."""
    spec = yaml.safe_load((ROOT / "data" / "characteristics" / (characteristic_id + ".yaml")).read_text())
    return int(spec["ttl_days"])


def claims(hotels, sources):
    records = []
    weather = {"A": read(SEED / "open_meteo.json"), "B": read(SEED / "open_meteo_b.json")}
    photos = read(SEED / "nearby_photos.json").get("photos", []) if (SEED / "nearby_photos.json").exists() else []
    connectivity = read(SEED / "connectivity.json").get("tiles", []) if (SEED / "connectivity.json").exists() else []
    from pyproj import Geod
    geod = Geod(ellps="WGS84")

    def add(h, char, value, source_id, method, artifact, *, confidence=.7, days=None, limitations=None, inputs=None, scope=None, additional_sources=None):
        days = days or ttl_days(char)
        source_ids = [source_id] + (additional_sources or [])
        observed = max(sources[s]["retrieved_at"] for s in source_ids)
        valid = (datetime.fromisoformat(observed.replace("Z", "+00:00")) + timedelta(days=days)).isoformat()
        basis = json.dumps([h["id"], char, scope, value, observed], sort_keys=True, ensure_ascii=False)
        id = "open-" + str(uuid5(NAMESPACE_URL, basis))
        evidence = {"id": id + "-evidence", "kind": "open_data", "label": method, "source_ids": source_ids, "artifact": artifact,
                    "inputs": inputs or {}, "output": value, "limitations": limitations or []}
        claim = Claim(id=id, hotel_id=h["id"], characteristic_id=char, scope=scope or {"type": "hotel"}, value=value,
                      source=source_id, method=method, evidence=[evidence], confidence=confidence, observed_at=observed, valid_until=valid)
        records.append(claim.model_dump(mode="json"))

    for h in hotels:
        region = h["tags"]["demo_zone"]
        weather_file = "/data/seed/open_meteo_b.json" if region == "B" else "/data/seed/open_meteo.json"
        for month in weather[region]["climate_months"]:
            value = {k: month.get(k) for k in ["month", "air_c", "water_c", "sunshine_hours", "precipitation_mm", "period"]}
            ids = month.get("source_ids", [])
            if ids and any(value[k] is not None for k in ["air_c", "water_c", "sunshine_hours", "precipitation_mm"]):
                add(h, "climate", value, ids[0], "Open-Meteo ERA5 and SST monthly sample averages", weather_file,
                    additional_sources=ids[1:], confidence=.75, scope={"type": "hotel", "month": month["month"]},
                    limitations=weather[region]["limitations"][:2], inputs={"region": region, "grid": weather[region]["grids"],
                    "sample_periods": {k: v for k, v in month.items() if k.endswith(("_period", "_sample_days", "_years"))}})
        aq = weather[region]["air_quality"]
        if aq.get("european_aqi") is not None:
            value = {k: aq[k] for k in ["pm2_5", "european_aqi", "model"]}
            value["period"] = " / ".join(aq["period"])
            add(h, "air_quality", value, "open_meteo_aq", "CAMS Europe recent model-window mean", weather_file,
                confidence=.6, limitations=["Model estimate, not measurements at hotel; window includes current-day forecast.", "Recent sample cannot establish seasonal air quality."], inputs={"region": region, "samples": aq.get("european_aqi_samples")})
        radius = CONFIG["photos"]["radius_m"]
        nearby = [p for p in photos if p.get("lat") is not None and p.get("lon") is not None and geod.inv(h["lon"], h["lat"], p["lon"], p["lat"])[2] <= radius]
        if nearby:
            nearby.sort(key=lambda p: geod.inv(h["lon"], h["lat"], p["lon"], p["lat"])[2])
            value = {"available": True, "photos": [{k: p[k] for k in ["url", "thumbnail", "title", "author", "license", "source_url"]} for p in nearby[:5]]}
            add(h, "nearby_photos", value, "wikimedia_commons", "Commons geotagged files within 1500m", "/data/seed/nearby_photos.json", confidence=.8,
                limitations=["Geotagged nearby photographs do not prove a room view or hotel appearance."], inputs={"radius_m": 1500, "photo_ids": [p["id"] for p in nearby[:5]]})
        point = Point(h["lon"], h["lat"])
        for tile in connectivity:
            if wkt.loads(tile["tile"]).covers(point):
                value = {"download_mbps": tile["avg_d_kbps"] / 1000, "upload_mbps": tile["avg_u_kbps"] / 1000,
                         "latency_ms": tile["avg_lat_ms"], "tests": tile["tests"], "kind": tile["kind"], "quarter": tile["quarter"]}
                add(h, "connectivity", value, "ookla", "Hotel point containment in Ookla area tile", "/data/seed/connectivity.json", confidence=.55,
                    limitations=["Area average from Speedtest samples; not a hotel Wi-Fi test or service guarantee."], inputs={"quadkey": tile["quadkey"], "devices": tile["devices"]})
    return sorted(records, key=lambda c: c["id"])


def run():
    stages, sources = load_stages()
    table("sources", sorted(sources.values(), key=lambda s: s["id"]))
    dump(SEED / "sources.json", sorted(sources.values(), key=lambda s: s["id"]))
    hotels = read(SEED / "hotels.json")
    buildings = read(SEED / "buildings.geojson")["features"]
    for f in buildings:
        p = f["properties"]
        if p["height_m"] is not None and p["height_m"] <= 0:
            p.update(height_m=None, height_source="unknown", height_uncertainty_m=None, height_invalid_reason="Nonpositive upstream height tag retained in original properties")
    dump(SEED / "buildings.geojson", {"type": "FeatureCollection", "features": buildings})
    features = {}
    for layer in ["pois", "roads", "railways", "beaches", "coastline"]:
        for f in read(SEED / (layer + ".geojson"))["features"]:
            if f["id"] not in features:
                features[f["id"]] = {**f, "layers": [layer]}
            else:
                features[f["id"]]["layers"].append(layer)
    # Runtime reads these layers from GeoJSON; duplicate DB exports add no data.
    for name in ("buildings", "osm_features"):
        (SEED / (name + ".csv.gz")).unlink(missing_ok=True)
    open_claims = claims(hotels, sources)
    with (SEED / "open_claims.jsonl.gz").open("wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", filename="", mtime=0) as gz:
            for claim in open_claims:
                gz.write((json.dumps(claim, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())
    outputs = {}
    for directory in [SEED, ROOT / "data/tiles"]:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.name not in {"manifest.json", "validation.json"}:
                outputs[str(path.relative_to(ROOT))] = output_metadata(path)
    dump(SEED / "manifest.json", {"schema_version": 1, "seed_version": CONFIG["id"] + "-2026-10-26-ab-v2", "generated_at": utcnow(),
        "region_config": {"path": str(CONFIG_PATH.relative_to(ROOT)) if CONFIG_PATH.is_relative_to(ROOT) else str(CONFIG_PATH), "sha256": sha(CONFIG_PATH), "parameters": CONFIG},
        "bbox": [min(b[0] for b in COVERAGE_BBOXES), min(b[1] for b in COVERAGE_BBOXES), max(b[2] for b in COVERAGE_BBOXES), max(b[3] for b in COVERAGE_BBOXES)], "coverage_bboxes": COVERAGE_BBOXES, "hotel_selection_zones": {"A": ZONE_A, "B": ZONE_B},
        "crs": "EPSG:4326", "metric_crs": "EPSG:32637", "counts": {"hotels": len(hotels), "buildings": len(buildings), "osm_features": len(features),
            "sources": len(sources), "open_claims": len(open_claims), "hotel_zones": {z: sum(h["tags"]["demo_zone"] == z for h in hotels) for z in ["A", "B"]}},
        "sources": list(sources.values()), "stages": stages, "outputs": outputs,
        "licenses": sorted({s["license"] for s in sources.values()}), "limitations": [
            "Source registry lists only downloaded data actually used in exported artifacts or claims.",
            "No price, room type, soundproofing or schedule is invented; absent values remain unknown.",
            "OSM way polygons and tagged nodes are extracted; relation multipolygons are not assembled, so coverage is incomplete.",
            "Copernicus is DSM, not bare-earth DTM; height from levels assumes 3m per level and is explicitly marked.",
            "Merged OSM database is distributed under ODbL; retain upstream attributions and per-file photo licenses.",
            "Noncommercial prototype uses Ookla CC BY-NC-SA and Open-Meteo free noncommercial service."]})
    print(f"Exported {len(sources)} sources and {len(open_claims)} validated open claims", flush=True)


if __name__ == "__main__":
    run()
