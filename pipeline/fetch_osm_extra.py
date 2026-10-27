"""Incremental OSM extraction. Never writes the original seed or geo inputs."""
from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import LineString, Point, Polygon, box, mapping

from pipeline.common import CONFIG, SEED, collection, dump, fetch, output_metadata, source, utcnow

LAYERS = ("aeroways", "transit_stops", "construction", "entrances", "vehicle_roads", "communication_masts", "history", "landmarks")
TRANSPORT = {"bus", "train", "light_rail", "tram", "trolleybus"}
HISTORY = {"start_date", "building:start_date", "construction_date", "heritage", "heritage:operator", "heritage:ref", "historic", "architect", "building:architecture", "wikidata", "wikipedia", "old_name"}


def categories(tags):
    result = []
    if tags.get("aeroway") in {"runway", "aerodrome", "terminal"}:
        result.append("aeroways")
    if tags.get("public_transport") in {"platform", "stop_position", "station"} or tags.get("highway") == "bus_stop" or tags.get("railway") in {"station", "halt", "tram_stop"}:
        result.append("transit_stops")
    if tags.get("landuse") == "construction" or tags.get("building") == "construction" or tags.get("highway") == "construction" or tags.get("construction"):
        result.append("construction")
    if tags.get("entrance") and tags.get("entrance") != "no":
        result.append("entrances")
    if tags.get("highway"):
        result.append("vehicle_roads")
    if tags.get("man_made") in {"mast", "tower", "communications_tower"} and (tags.get("tower:type") == "communication" or tags.get("communication:mobile_phone") not in {None, "no"} or tags.get("man_made") == "communications_tower"):
        result.append("communication_masts")
    if HISTORY.intersection(tags):
        result.append("history")
    if tags.get("name") and (tags.get("tourism") == "attraction" or tags.get("man_made") in {"lighthouse", "tower"} or tags.get("leisure") == "stadium" or tags.get("amenity") == "theatre" or "морской вокзал" in tags["name"].lower()):
        result.append("landmarks")
    return result


def extract(path, *, output_dir=SEED, download=None):
    import osmium

    with osmium.io.Reader(str(path)) as reader:
        actual_snapshot = reader.header().get("osmosis_replication_timestamp")
    if actual_snapshot != CONFIG["osm"]["snapshot"]:
        raise ValueError(f"PBF snapshot {actual_snapshot!r} differs from the configured snapshot; record and approve provenance first")

    # Continuous transport envelope includes the roads between the two original
    # disjoint zones. It does not extend the original pedestrian graph's coverage.
    bboxes = [z["coverage_bbox"] for z in CONFIG["zones"].values()]
    bbox = [min(b[0] for b in bboxes)-.02, min(b[1] for b in bboxes)-.02,
            max(b[2] for b in bboxes)+.02, max(b[3] for b in bboxes)+.02]
    bounds = box(*bbox)
    layers, seen, routes = {name: [] for name in LAYERS}, set(), []
    snapshot = CONFIG["osm"]["snapshot"]

    def append(oid, tags, geom, nodes=None):
        if geom.is_empty or not geom.intersects(bounds):
            return
        names = categories(tags)
        if not names:
            return
        props = {**tags, "osm_id": oid, "source_id": "tz_osm_geofabrik", "observed_at": snapshot}
        if nodes:
            props["node_ids"] = nodes
        feature = {"type": "Feature", "id": oid, "properties": props, "geometry": mapping(geom)}
        for name in names:
            layers[name].append(feature)
        seen.add(oid)

    class Handler(osmium.SimpleHandler):
        def node(self, node):
            if not node.tags:
                return
            if node.location.valid():
                lon, lat = node.location.lon, node.location.lat
                if bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]:
                    append(f"osm-node-{node.id}", dict(node.tags), Point(lon, lat))

        def way(self, way):
            tags = dict(way.tags)
            if not categories(tags):
                return
            try:
                coords = [(n.lon, n.lat) for n in way.nodes]
            except osmium.InvalidLocationError:
                return
            if len(coords) < 2:
                return
            if (max(c[0] for c in coords) < bbox[0] or min(c[0] for c in coords) > bbox[2]
                    or max(c[1] for c in coords) < bbox[1] or min(c[1] for c in coords) > bbox[3]):
                return
            geometry = LineString(coords)
            if coords[0] == coords[-1] and len(coords) >= 4 and tags.get("area") != "no" and not tags.get("highway") and tags.get("aeroway") != "runway":
                polygon = Polygon(coords)
                geometry = polygon if polygon.is_valid else polygon.buffer(0)
            append(f"osm-way-{way.id}", tags, geometry, [str(n.ref) for n in way.nodes])

        def relation(self, relation):
            tags = dict(relation.tags)
            if tags.get("type") != "route" or tags.get("route") not in TRANSPORT:
                return
            members = [{"id": f"osm-{'node' if m.type == 'n' else 'way' if m.type == 'w' else 'relation'}-{m.ref}", "role": m.role} for m in relation.members]
            if any(m["id"] in seen for m in members):
                routes.append({"id": f"osm-relation-{relation.id}", "tags": tags, "members": members,
                               "source_id": "tz_osm_geofabrik", "observed_at": snapshot})

    Handler().apply_file(str(path), locations=True, idx="flex_mem")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, features in layers.items():
        dump(output_dir / f"tz_{name}.geojson", collection(features))
    dump(output_dir / "tz_transit_routes.json", sorted(routes, key=lambda r: r["id"]))
    record = {"stage": "tz_osm_extra@1", "generated_at": utcnow(), "snapshot": snapshot, "bbox": bbox,
              "download": download, "counts": {**{k: len(v) for k, v in layers.items()}, "transit_routes": len(routes)},
              "limitations": ["Multipolygon relations are not assembled", "OSM omissions are unknown, never evidence of absence", "Route relations describe mapped connectivity, not a timetable or operational service", "Continuous road envelope does not extend the original foot graph"],
              "license_verified_url": "https://www.openstreetmap.org/copyright",
              "outputs": {str(p.name): output_metadata(p) for p in sorted(output_dir.glob("tz_*.geojson*"))}}
    record["outputs"]["tz_transit_routes.json"] = output_metadata(output_dir / "tz_transit_routes.json")
    dump(output_dir / "tz_osm_manifest.json", record)
    dump(output_dir / "tz_osm_sources.json", [source("tz_osm_geofabrik", "OSM additional layers / Geofabrik South Federal District", CONFIG["osm"]["url"], "ODbL-1.0", "© OpenStreetMap contributors; extract by Geofabrik", snapshot, (download or {}).get("retrieved_at", utcnow()), "Incremental extract; original seed and facts unchanged. License checked at https://www.openstreetmap.org/copyright")])
    print(json.dumps(record["counts"], sort_keys=True), flush=True)
    return record


def run():
    path, metadata = fetch(CONFIG["osm"]["url"], CONFIG["osm"]["url"].rsplit("/", 1)[-1])
    return extract(path, download=metadata)


if __name__ == "__main__":
    run()
