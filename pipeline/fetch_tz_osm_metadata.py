"""Original PBF element metadata, never confused with tag survey dates."""
from __future__ import annotations

from geo.io import read_json
from pipeline.common import CONFIG, SEED, dump, fetch, sha, utcnow


def run():
    import osmium

    path, metadata = fetch(CONFIG["osm"]["url"], CONFIG["osm"]["url"].rsplit("/", 1)[-1])
    paths = [SEED / "tz_construction.geojson", SEED / "tz_areas.geojson"]
    ids = {f["id"] for p in paths for f in read_json(p)["features"]}
    ids.add("osm-relation-3405854")
    processor = osmium.FileProcessor(str(path)).with_filter(osmium.filter.IdFilter([int(oid.rsplit("-", 1)[1]) for oid in ids]))
    if processor.header.get("osmosis_replication_timestamp") != CONFIG["osm"]["snapshot"]:
        raise ValueError("PBF metadata snapshot mismatch")
    elements = {}
    for item in processor:
        kind = {"n": "node", "w": "way", "r": "relation"}[item.type_str()]
        oid = f"osm-{kind}-{item.id}"
        if oid in ids:
            elements[oid] = {"osm_id": oid, "version": item.version, "element_timestamp": item.timestamp.isoformat(),
                             "tags": dict(item.tags), "tag_changed_at": None,
                             "semantics": "timestamp is last edit of whole element, not construction-tag age or survey"}
    output = {"snapshot": CONFIG["osm"]["snapshot"], "generated_at": utcnow(), "download": metadata,
              "elements": elements, "missing_ids": sorted(ids-elements.keys()),
              "limitations": ["No history diff: construction tag's modification date is unknown", "Download/retrieval date does not prove activity", "Geofabrik excludes contributor personal metadata"]}
    dump(SEED / "tz_osm_element_metadata.json", output)
    dump(SEED / "tz_metadata_manifest.json", {"stage": "tz_osm_metadata@1", "snapshot": CONFIG["osm"]["snapshot"],
        "generated_at": utcnow(), "elements": len(elements), "missing": output["missing_ids"],
        "outputs": {"tz_osm_element_metadata.json": {"sha256": sha(SEED / "tz_osm_element_metadata.json"), "bytes": (SEED / "tz_osm_element_metadata.json").stat().st_size}}})
    print({"elements": len(elements), "missing": len(output["missing_ids"])}, flush=True)


if __name__ == "__main__":
    run()
