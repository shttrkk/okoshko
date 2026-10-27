"""Exact Fisht relation multipolygon and Wikidata height availability evidence."""
from __future__ import annotations

import json
import math

from shapely.geometry import shape

from pipeline.common import CONFIG, SEED, collection, dump, fetch, output_metadata, read, source, utcnow

RELATION_ID = 3405854
QID = "Q1149601"


def confirmed_height(tags, entity):
    try:
        height = float(str(tags.get("height", "")).removesuffix(" m"))
        if math.isfinite(height) and height > 0:
            return {"height_m": height, "height_source": "osm_height"}
    except (ValueError, TypeError):
        pass
    quantities = []
    for statement in entity.get("claims", {}).get("P2048", []):
        snak = statement.get("mainsnak", {})
        if statement.get("rank") == "deprecated" or snak.get("snaktype") != "value":
            continue
        value = snak.get("datavalue", {}).get("value", {})
        if value.get("unit") == "http://www.wikidata.org/entity/Q11573":
            try:
                height = float(value["amount"])
                if math.isfinite(height) and height > 0:
                    quantities.append((height, statement))
            except (ValueError, KeyError):
                continue
    if quantities and len({h for h, _ in quantities}) == 1:
        return {"height_m": quantities[0][0], "height_source": "wikidata_p2048", "statement": quantities[0][1]}
    return None  # no levels prior, stadium-name inference or neighbouring arena


def run():
    import osmium

    path, pbf_metadata = fetch(CONFIG["osm"]["url"], CONFIG["osm"]["url"].rsplit("/", 1)[-1])
    processor = (osmium.FileProcessor(str(path)).with_areas(osmium.filter.IdFilter([RELATION_ID]))
                 .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA)).with_filter(osmium.filter.TagFilter(("building", "stadium"))))
    if processor.header.get("osmosis_replication_timestamp") != CONFIG["osm"]["snapshot"]:
        raise ValueError("Fisht PBF snapshot mismatch")
    factory, features = osmium.geom.GeoJSONFactory(), []
    for area in processor:
        if area.from_way() or area.orig_id() != RELATION_ID:
            continue
        tags = dict(area.tags)
        if tags.get("wikidata") != QID:
            raise ValueError("Fisht exact OSM/Wikidata identity mismatch")
        geometry = json.loads(factory.create_multipolygon(area))
        if not shape(geometry).is_valid:
            raise ValueError("invalid Fisht multipolygon")
        features.append({"type": "Feature", "id": f"osm-relation-{RELATION_ID}", "geometry": geometry,
                         "properties": {**tags, "source_id": "tz_osm_geofabrik", "observed_at": CONFIG["osm"]["snapshot"]}})
    entity_path, wd_metadata = fetch(f"https://www.wikidata.org/wiki/Special:EntityData/{QID}.json", f"tz_wikidata_fisht_{QID}.json")
    entity = read(entity_path)["entities"][QID]
    height = confirmed_height(features[0]["properties"], entity) if features else None
    dump(SEED / "tz_fisht.geojson", collection(features))
    manifest = {"stage": "tz_fisht_identity_height@1", "generated_at": utcnow(), "snapshot": CONFIG["osm"]["snapshot"],
                "osm_id": f"osm-relation-{RELATION_ID}", "wikidata_id": QID, "exact_identity": bool(features),
                "pbf_download": pbf_metadata, "wikidata_download": wd_metadata,
                "wikidata_revision": entity.get("lastrevid"), "wikidata_modified": entity.get("modified"),
                "osm_height": features[0]["properties"].get("height") if features else None,
                "osm_building_levels": features[0]["properties"].get("building:levels") if features else None,
                "wikidata_p2048": entity.get("claims", {}).get("P2048", []), "confirmed_height": height,
                "los_enabled": bool(features and height), "unknown_reason": None if height else "Exact contour and QID exist, but neither OSM height nor Wikidata P2048 supplies a confirmed height; no LOS activation",
                "outputs": {"tz_fisht.geojson.gz": output_metadata(SEED / "tz_fisht.geojson.gz")}}
    dump(SEED / "tz_fisht_manifest.json", manifest)
    dump(SEED / "tz_fisht_sources.json", [source("tz_wikidata_fisht", "Wikidata exact Fisht height availability", wd_metadata["url"], "CC0-1.0", "Wikidata contributors, structured data CC0", entity.get("modified", utcnow()), wd_metadata["retrieved_at"], "Exact OSM wikidata tag; P2048 only. Empty P2048 does not license an inferred height. https://www.wikidata.org/wiki/Wikidata:Licensing")])
    print({"contours": len(features), "confirmed_height": height, "los_enabled": manifest["los_enabled"]}, flush=True)
    return manifest


if __name__ == "__main__":
    run()
