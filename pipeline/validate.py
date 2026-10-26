"""Meaningful artifact, contract, identity and graph invariants."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from collections import Counter

from shapely.geometry import shape

from backend.contracts.models import Claim, Hotel, Source
from geo.io import exists, read_bytes
from pipeline.common import CONFIG, ROOT, SEED, dump, read, sha


def run():
    manifest = read(SEED / "manifest.json")
    sources = {s["id"]: Source.model_validate(s) for s in read(SEED / "sources.json")}
    hotels = [Hotel.model_validate(h) for h in read(SEED / "hotels.json")]
    assert 150 <= len(hotels) <= 250
    assert len({h.id for h in hotels}) == len(hotels)
    assert Counter(h.tags["demo_zone"] for h in hotels) == {z: v["hotel_limit"] for z, v in CONFIG["zones"].items()}
    assert all(h.basic_fields_source == "open_data" and h.price_rub is None for h in hotels)
    assert all(h.source_ids and set(h.source_ids) <= sources.keys() and h.tags.get("source_records") for h in hotels)
    assert all(h.id.startswith(("osm-node-", "osm-way-", "overture-")) for h in hotels)
    table_counts = {}
    for name in ["hotels", "sources"]:
        path = SEED / (name + ".csv.gz")
        assert path.read_bytes()[4:8] == b"\0\0\0\0", f"gzip mtime is not reproducible: {path}"
        ids = set()
        with gzip.open(path, "rt", newline="") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == ["id", "payload"]
            for row in reader:
                payload = json.loads(row["payload"])
                assert row["id"] == payload["id"] and row["id"] not in ids
                if name == "hotels":
                    Hotel.model_validate(payload)
                if name == "sources":
                    Source.model_validate(payload)
                ids.add(row["id"])
        table_counts[name] = len(ids)
    for name in ("buildings", "osm_features"):
        assert not (SEED / (name + ".csv.gz")).exists(), f"Duplicate layer export: {name}"
    geometry_counts, heights = {}, Counter()
    feature_ids = set()
    for layer in ["buildings", "pois", "roads", "railways", "beaches", "coastline"]:
        features = read(SEED / (layer + ".geojson"))["features"]
        assert len({f["id"] for f in features}) == len(features)
        for f in features:
            g = shape(f["geometry"])
            assert not g.is_empty and g.is_valid, f"Invalid geometry {f['id']} in {layer}"
            assert f["properties"]["source_id"] in sources
            if layer == "buildings":
                p = f["properties"]
                heights[p["height_source"]] += 1
                assert p["height_source"] in {"height", "levels", "unknown"}
                assert (p["height_m"] is None) == (p["height_source"] == "unknown")
                assert p["height_m"] is None or p["height_m"] > 0
        geometry_counts[layer] = len(features)
        if layer != "buildings":
            feature_ids.update(f["id"] for f in features)
        logical = SEED / (layer + ".geojson")
        physical = logical.with_suffix(".geojson.gz")
        assert not logical.exists() and physical.is_file(), logical
        assert str(physical.relative_to(ROOT)) in manifest["outputs"], physical
    assert geometry_counts["buildings"] == manifest["counts"]["buildings"]
    assert len(feature_ids) == manifest["counts"]["osm_features"]
    with gzip.open(SEED / "walk_graph.json.gz", "rt") as f:
        graph = json.load(f)
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert len(nodes) == len(graph["nodes"])
    parents = {id: id for id in nodes}

    def find(x):
        while parents[x] != x:
            parents[x] = parents[parents[x]]
            x = parents[x]
        return x

    referenced = set()
    for e in graph["edges"]:
        assert e["u"] in nodes and e["v"] in nodes and e["length_m"] > 0
        assert e["access"] not in {"no", "private"}
        assert e["source_id"] in sources
        a, b = e["geometry"]
        assert a == [nodes[e["u"]]["lon"], nodes[e["u"]]["lat"]]
        assert b == [nodes[e["v"]]["lon"], nodes[e["v"]]["lat"]]
        parents[find(e["u"])] = find(e["v"])
        referenced.update([e["u"], e["v"]])
    assert referenced == nodes.keys(), "isolated/unreferenced graph nodes"
    assert all("elevation_m" in n for n in nodes.values()), "DEM sampling must cover every walk node"
    components = Counter(find(id) for id in nodes)
    claim_ids, claim_counts = set(), Counter()
    hotel_ids = {h.id for h in hotels}
    with gzip.open(SEED / "open_claims.jsonl.gz", "rt") as f:
        for line in f:
            c = Claim.model_validate_json(line)
            assert c.id not in claim_ids and c.hotel_id in hotel_ids and c.source in sources and not c.synthetic
            claim_ids.add(c.id)
            claim_counts[c.characteristic_id] += 1
            for ev in c.evidence:
                assert set(ev.source_ids) <= sources.keys()
                if ev.artifact:
                    assert exists(ROOT / ev.artifact.lstrip("/")), ev.artifact
    for path, meta in manifest["outputs"].items():
        p = ROOT / path
        assert p.exists() and p.stat().st_size == meta["bytes"] and sha(p) == meta["sha256"], path
        if path.endswith(".geojson.gz"):
            assert meta["encoding"] == "gzip" and meta["logical_path"] == path.removesuffix(".gz"), path
            assert p.read_bytes()[4:8] == b"\0\0\0\0", f"gzip mtime is not reproducible: {path}"
            logical = ROOT / meta["logical_path"]
            assert not logical.exists(), f"Duplicate plain GeoJSON: {logical}"
            original = read_bytes(logical)
            assert len(original) == meta["uncompressed_bytes"], path
            assert hashlib.sha256(original).hexdigest() == meta["uncompressed_sha256"], path
    for p in list(SEED.rglob("*")) + list((ROOT / "data/tiles").rglob("*")):
        if p.is_file():
            assert p.stat().st_size < 50_000_000, f"Over 50MB: {p}"
    photos = read(SEED / "nearby_photos.json")["photos"]
    assert all(p["license"] and p["author"] and p["source_url"] for p in photos)
    for p in photos:
        if p.get("local_thumbnail"):
            assert (ROOT / p["local_thumbnail"].lstrip("/")).exists()
    from pmtiles.reader import MmapSource, Reader
    with (ROOT / "data/tiles/city.pmtiles").open("rb") as f:
        archive = Reader(MmapSource(f))
        assert archive.header()["max_zoom"] == CONFIG["tiles"]["maxzoom"]
        for zone in CONFIG["zones"].values():
            lat, lon, _ = zone["meteo"]
            z = min(14, CONFIG["tiles"]["maxzoom"])
            x = int((lon + 180) / 360 * 2**z)
            y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2**z)
            assert archive.get(z, x, y), "Missing representative PMTiles tile in a demo zone"
    families = {"OpenStreetMap/Geofabrik": ["osm_geofabrik"], "Overture": ["overture_places", "overture_buildings"], "Copernicus DEM": ["copernicus_dem"],
                "Open-Meteo": ["open_meteo_archive", "open_meteo_marine", "open_meteo_aq"], "Wikimedia Commons": ["wikimedia_commons"],
                "Wikidata": ["wikidata"], "Ookla": ["ookla"], "Protomaps": ["protomaps"]}
    families = [name for name, ids in families.items() if any(id in sources for id in ids)]
    assert len(families) >= 6, "Fewer than six actually used independent external dataset families"
    report = {"valid": True, "hotel_count": len(hotels), "hotel_zones": dict(Counter(h.tags["demo_zone"] for h in hotels)),
        "source_count": len(sources), "external_source_registry_count": sum(s.kind != "computed" for s in sources.values()),
        "external_dataset_families": families,
        "tables": table_counts, "geometry_counts": geometry_counts, "building_height_provenance": dict(heights),
        "walk_graph": {"nodes": len(nodes), "directed_edges": len(graph["edges"]), "nodes_with_dem": sum("elevation_m" in n for n in nodes.values()),
                       "weak_components": len(components), "largest_components": sorted(components.values(), reverse=True)[:10]},
        "open_claims": dict(claim_counts), "photos": {"metadata": len(photos), "local_thumbnails": sum(bool(p.get("local_thumbnail")) for p in photos)},
        "largest_output_bytes": max(m["bytes"] for m in manifest["outputs"].values())}
    dump(SEED / "validation.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
