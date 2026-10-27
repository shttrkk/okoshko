"""Targeted relation polygons: airport terminals and landmark contours."""
from __future__ import annotations

import json

from shapely.geometry import box, shape

from pipeline.common import CONFIG, SEED, collection, dump, fetch, output_metadata, utcnow


def run():
    import osmium

    path, metadata = fetch(CONFIG["osm"]["url"], CONFIG["osm"]["url"].rsplit("/", 1)[-1])
    bbox = json.loads((SEED / "tz_osm_manifest.json").read_text())["bbox"]
    bounds = box(*bbox)
    tags = [("aeroway", "aerodrome"), ("aeroway", "terminal"), ("amenity", "theatre"),
            ("leisure", "stadium"), ("man_made", "lighthouse"), ("landuse", "construction"), ("building", "construction")]
    processor = (osmium.FileProcessor(str(path)).with_areas(osmium.filter.TagFilter(*tags))
                 .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA)).with_filter(osmium.filter.TagFilter(*tags)))
    if processor.header.get("osmosis_replication_timestamp") != CONFIG["osm"]["snapshot"]:
        raise ValueError("area PBF snapshot differs from configured provenance")
    factory, features = osmium.geom.GeoJSONFactory(), []
    for area in processor:
        if area.from_way():
            continue  # original way-only extra layers already retain these
        try:
            geometry = json.loads(factory.create_multipolygon(area))
            polygon = shape(geometry)
        except (ValueError, RuntimeError):
            continue
        if polygon.is_empty or not polygon.is_valid or not polygon.intersects(bounds):
            continue
        oid = f"osm-relation-{area.orig_id()}"
        features.append({"type": "Feature", "id": oid, "geometry": geometry,
                         "properties": {**dict(area.tags), "osm_id": oid, "source_id": "tz_osm_geofabrik", "observed_at": CONFIG["osm"]["snapshot"]}})
    dump(SEED / "tz_areas.geojson", collection(features))
    manifest = {"stage": "tz_targeted_osm_areas@1", "generated_at": utcnow(), "snapshot": CONFIG["osm"]["snapshot"],
                "download": metadata, "bbox": bbox, "count": len(features), "ids": [f["id"] for f in features],
                "limitations": ["Targeted multipolygon relations only; this does not rebuild original seed buildings/POIs", "No inference from missing relations"],
                "outputs": {"tz_areas.geojson.gz": output_metadata(SEED / "tz_areas.geojson.gz")}}
    dump(SEED / "tz_areas_manifest.json", manifest)
    print(json.dumps({"areas": len(features), "ids": manifest["ids"]}), flush=True)
    return manifest


if __name__ == "__main__":
    run()
