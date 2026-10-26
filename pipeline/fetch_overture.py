"""Bounded DuckDB/S3 reads against a pinned Overture release."""
from __future__ import annotations

import json

import duckdb
from shapely.geometry import mapping, shape
from shapely.strtree import STRtree

from pipeline.common import BBOX, CONFIG, COVERAGE_BBOXES, RAW, SEED, collection, dump, read, sha, source, utcnow

RELEASE = CONFIG["overture"]["release"]
SNAPSHOT = RELEASE[:10] + "T00:00:00Z"


def query(theme, kind):
    path = RAW / f"overture_{theme}.json"
    metadata = RAW / f"overture_{theme}.metadata.json"
    if path.exists() and metadata.exists() and read(metadata).get("coverage_bboxes") == COVERAGE_BBOXES and read(metadata).get("snapshot") == RELEASE:
        return read(path), read(metadata)
    uri = f"s3://overturemaps-us-west-2/release/{RELEASE}/theme={theme}/type={kind}/*"
    c = duckdb.connect()
    c.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; SET threads=4; SET memory_limit='2GB';")
    columns = "id,names,basic_category,confidence,websites,phones,addresses,sources,operating_status" if theme == "places" else "id,names,height,num_floors,sources"
    filters = [f"(bbox.xmin >= {west} AND bbox.xmax <= {east} AND bbox.ymin >= {south} AND bbox.ymax <= {north})" for west, south, east, north in COVERAGE_BBOXES]
    queries = []
    by_id = {}
    # Separate bbox scans retain strong Parquet row-group pruning. A combined OR
    # predicate can cause excessive remote reads in DuckDB for this partitioning.
    for predicate in filters:
        sql = f"SELECT {columns}, ST_AsGeoJSON(geometry) AS geometry_json FROM read_parquet('{uri}', hive_partitioning=1) WHERE " + predicate
        if theme == "places":
            sql += " AND basic_category IN (" + ",".join("'" + str(v).replace("'", "''") + "'" for v in CONFIG["overture"]["lodging"]) + ")"
        queries.append(sql)
        result = c.execute(sql)
        names = [d[0] for d in result.description]
        rows = [dict(zip(names, row)) for row in result.fetchall()]
        by_id.update({row["id"]: row for row in rows})
        print(f"Overture {theme} bbox result: {len(rows)}", flush=True)
    records = list(by_id.values())
    for row in records:
        row["geometry"] = json.loads(row.pop("geometry_json"))
    dump(path, sorted(records, key=lambda r: r["id"]))
    meta = dict(url=uri, retrieved_at=utcnow(), snapshot=RELEASE, sha256=sha(path), bytes=path.stat().st_size,
                sha_scope="materialized bbox query result, not entire upstream objects", sql=queries, coverage_bboxes=COVERAGE_BBOXES)
    dump(metadata, meta)
    return records, meta


def run():
    places, pmeta = query("places", "place")
    print(f"Overture lodging places: {len(places)}", flush=True)
    buildings, bmeta = query("buildings", "building")
    existing = [f for f in read(SEED / "buildings.geojson")["features"] if f["properties"]["source_id"] == "osm_geofabrik"]
    geoms = [shape(f["geometry"]) for f in existing]
    tree = STRtree(geoms)
    added = []
    for b in buildings:
        g = shape(b["geometry"])
        if not g.is_valid:
            g = g.buffer(0)
        candidates = tree.query(g)
        # Overture already conflates OSM footprints. Keep only genuinely additional footprints.
        if any(g.intersection(geoms[int(i)]).area / max(g.area, 1e-20) > .5 for i in candidates):
            continue
        height, levels = b.get("height"), b.get("num_floors")
        added.append({"type": "Feature", "id": "overture-" + b["id"], "geometry": mapping(g), "properties": {
            "source_id": "overture_buildings", "observed_at": SNAPSHOT, "overture_id": b["id"],
            "height_m": height if height else levels * CONFIG["height"]["floor_height_m"] if levels else None,
            "height_source": "height" if height else "levels" if levels else "unknown",
            "height_uncertainty_m": CONFIG["height"]["tagged_uncertainty_m"] if height else CONFIG["height"]["levels_uncertainty_m"] if levels else None, "levels": levels,
            "upstream_sources": b.get("sources", []), "name": (b.get("names") or {}).get("primary")}})
    dump(SEED / "buildings.geojson", collection(existing + added))
    # Persist the exact downloaded features used to derive additional geometry.
    dump(SEED / "overture_places.json", places)
    sources = [source("overture_places", "Overture Maps Places", pmeta["url"], "CDLA-Permissive-2.0 / Apache-2.0 / CC0-1.0 (per record)",
                      "Overture Maps Foundation and upstream providers (see each record sources)", SNAPSHOT, pmeta["retrieved_at"],
                      "Pinned release; real lodging records only; no hotel claims inferred from category."),
               source("overture_buildings", "Overture Maps Buildings", bmeta["url"], "ODbL-1.0",
                      "Overture Maps Foundation; © OpenStreetMap contributors; Microsoft and other per-record providers", SNAPSHOT, bmeta["retrieved_at"],
                      "Added footprints not substantially overlapped by current OSM way polygons; missing heights stay unknown.")]
    dump(RAW / "overture_stage.json", {"sources": sources, "downloads": [pmeta, bmeta],
                "counts": {"places": len(places), "buildings_downloaded": len(buildings), "buildings_added": len(added)}})
    print(f"Overture buildings: {len(buildings)}, added: {len(added)}", flush=True)


if __name__ == "__main__":
    run()
