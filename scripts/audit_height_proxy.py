"""Read-only raw DSM screening for five known footprints; never emits claims.

Print JSON, or use --output docs/internal/height_proxy_audit.json. The broad
envelopes are heuristic sensitivity checks, not calibrated confidence intervals
and not measured building heights. No database or network access is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from shapely.geometry import Point, box, shape
from shapely.strtree import STRtree

from geo.coordinates import metric
from geo.io import read_json
from geo.terrain import Grid, SeaClassifier

ROOT = Path(__file__).resolve().parents[1]
HOTEL_IDS = (
    "osm-node-10058556917",
    "osm-node-10118233110",
    "overture-5d220661-b1ca-4461-a03b-c89126a98e02",
    "osm-way-201474330",
    "overture-037ee676-5776-46bd-a4ac-38c828f2a99d",
)


def percentile(values, quantile):
    return float(np.percentile(values, quantile)) if values else None


def rounded(value):
    return round(value, 3) if value is not None else None


def footprint_result(hotel, document, grid, buildings, tree, sea):
    polygon = metric(shape(document["building"]["geometry"]))
    levels = float(document["building"]["properties"]["building:levels"])
    min_x, min_y, max_x, max_y = polygon.bounds
    cell, radius = grid.cell, 180
    c0 = max(0, int((min_x-radius-grid.x_min)//cell))
    c1 = min(grid.values.shape[1], int((max_x+radius-grid.x_min)//cell)+2)
    r0 = max(0, int((grid.y_max-max_y-radius)//cell))
    r1 = min(grid.values.shape[0], int((grid.y_max-min_y+radius)//cell)+2)
    roof, core, coverage, ground = [], [], [], []
    water_excluded = 0
    for row in range(r0, r1):
        for col in range(c0, c1):
            elevation = float(grid.values[row, col])
            if not np.isfinite(elevation):
                continue
            x = grid.x_min+(col+.5)*cell
            y = grid.y_max-(row+.5)*cell
            point = Point(x, y)
            if polygon.covers(point):
                roof.append(elevation)
                fraction = polygon.intersection(box(x-cell/2, y-cell/2, x+cell/2, y+cell/2)).area/cell**2
                coverage.append(fraction)
                if fraction >= .8:
                    core.append(elevation)
            elif 30 <= polygon.distance(point) <= radius and not any(buildings[int(i)].covers(point) for i in tree.query(point)):
                if bool(sea.sample(np.asarray(x), np.asarray(y))):
                    water_excluded += 1
                else:
                    ground.append(elevation)
    proxy = percentile(roof, 50)-percentile(ground, 10) if roof and ground else None
    envelope = [max(0, percentile(roof, 10)-percentile(ground, 20)-15),
                max(0, percentile(roof, 90)-percentile(ground, 5)+15)] if roof and ground else None
    osm_envelope = [max(0, levels*2.8-3), levels*3.5+3]
    disjoint = (envelope[1] < osm_envelope[0] or envelope[0] > osm_envelope[1]) if envelope is not None else None
    sufficient_support = len(core) >= 3 and len(ground) >= 10
    return {
        "hotel_id": hotel["id"], "hotel_name": hotel["name"], "building_id": document["building_id"],
        "geometry_artifact": "/data/geo/"+hotel["id"]+".json",
        "footprint_area_m2": rounded(polygon.area), "osm_levels": levels,
        "osm_nominal_height_m": levels*3, "osm_envelope_m": [rounded(v) for v in osm_envelope],
        "roof_pixel_centers": len(roof), "roof_core80_pixel_centers": len(core),
        "roof_cell_footprint_fractions": [rounded(v) for v in coverage],
        "roof_dsm_p10_p50_p90_m": [rounded(percentile(roof, p)) for p in (10, 50, 90)],
        "land_ground_pixel_centers": len(ground), "sea_pixels_excluded": water_excluded,
        "ground_dsm_p5_p10_p20_p90_m": [rounded(percentile(ground, p)) for p in (5, 10, 20, 90)],
        "raw_roof_minus_ground_proxy_m": rounded(proxy),
        "proxy_heuristic_envelope_m": [rounded(v) for v in envelope] if envelope else None,
        "envelopes_disjoint": disjoint, "sufficient_roof_and_ground_support": sufficient_support,
        "quality_flag": bool(disjoint and sufficient_support),
        "status": "insufficient_roof_support" if not sufficient_support else "screening_mismatch" if disjoint else "envelopes_overlap",
    }


def audit(data_dir=ROOT / "data"):
    data_dir = Path(data_dir)
    hotels = {hotel["id"]: hotel for hotel in read_json(data_dir / "seed" / "hotels.json")}
    features = read_json(data_dir / "seed" / "buildings.geojson")["features"]
    buildings = [metric(shape(feature["geometry"])) for feature in features]
    tree = STRtree(buildings)
    coast = read_json(data_dir / "seed" / "coastline.geojson")["features"]
    sea = SeaClassifier([metric(shape(feature["geometry"])) for feature in coast])
    raster = data_dir / "geo" / "terrain_30m.npz"
    grid = Grid.load(raster)
    results = []
    for hotel_id in HOTEL_IDS:
        document = json.loads((data_dir / "geo" / (hotel_id+".json")).read_text())
        results.append(footprint_result(hotels[hotel_id], document.get("geo", document), grid, buildings, tree, sea))
    return {
        "schema_version": 1, "audit_type": "raw_dsm_height_proxy_screening",
        "terrain": {"path": "/data/geo/terrain_30m.npz", "metadata": "/data/geo/terrain_30m.json",
                    "sha256": hashlib.sha256(raster.read_bytes()).hexdigest(), "cell_m": grid.cell,
                    "source": "copernicus_dem", "surface": "raw reprojected DSM, not local OSM-composed DSM"},
        "method": {
            "roof": "Actual raster pixel centers inside the OSM footprint; core requires at least 80% cell overlap.",
            "ground_proxy": "Raw DSM pixel centers 30–180 m from the footprint, outside known building polygons and mapped sea; P10.",
            "height_proxy": "Median roof DSM minus P10 neighboring land DSM; not true roof-to-foundation height.",
            "osm_envelope": "[max(0, levels*2.8-3), levels*3.5+3] meters",
            "proxy_envelope": "[max(0, roofP10-groundP20-15), max(0, roofP90-groundP5+15)] meters",
            "envelope_interpretation": "Heuristic sensitivity envelope, not a calibrated confidence interval (not CI).",
            "quality_flag_gate": "Disjoint envelopes AND at least 3 core roof pixels AND at least 10 ground pixels.",
        },
        "limitations": [
            "No independent ground surface or measured building height is supplied.",
            "30 m mixed pixels, bilinear reprojection, trees/canopy and sloping terrain may dominate the proxy.",
            "Footprints and roof sections may differ; neighboring low DSM samples need not equal foundation elevation.",
            "The acquisition date of each raster pixel and its temporal match with current OSM floors are unknown.",
            "The chosen 15 m buffer and support gate are audit heuristics, not empirically calibrated uncertainty.",
            "Zero flags do not validate OSM heights or establish field accuracy.",
            "No Claim, Fact, Conflict or geodata is created or modified by this audit.",
        ],
        "results": results,
        "summary": {"hotels_checked": len(results), "quality_flags": sum(row["quality_flag"] for row in results),
                    "height_ground_truth_count": 0, "field_accuracy": None, "blueprint_height_conflict": "unconfirmed"},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional report path under docs/internal; stdout otherwise")
    args = parser.parse_args()
    if args.output and not args.output.resolve().is_relative_to((ROOT / "docs" / "internal").resolve()):
        parser.error("Report output must stay under docs/internal; geodata are read-only")
    started = time.monotonic()
    report = audit()
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
        print(json.dumps({**report["summary"], "elapsed_s": round(time.monotonic()-started, 2), "output": str(args.output)}, ensure_ascii=False))
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
