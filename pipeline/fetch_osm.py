"""Extract real OSM features and a topological foot graph from Geofabrik PBF."""
from __future__ import annotations

import json

import osmium
from pyproj import Geod
from shapely.geometry import LineString, Point, Polygon, box, mapping
from shapely.ops import unary_union

from pipeline.common import BBOX, CONFIG, COVERAGE_BBOXES, RAW, SEED, collection, dump, fetch, gzip_json, source

SNAPSHOT = CONFIG["osm"]["snapshot"]
URL = CONFIG["osm"]["url"]
GEOD = Geod(ellps="WGS84")
LODGING = set(CONFIG["osm"]["lodging"])


def number(v):
    try:
        value = float(str(v).replace(",", ".").replace(" m", ""))
        return value if value > 0 else None
    except (ValueError, TypeError):
        return None


def run():
    path, meta = fetch(URL, URL.rsplit("/", 1)[-1])
    bounds = unary_union([box(*b) for b in COVERAGE_BBOXES])
    envelope = bounds.bounds
    layers = {n: [] for n in ["buildings", "pois", "roads", "railways", "beaches", "coastline"]}
    hotels, nodes, edges, node_tags = [], {}, [], {}

    def hotel(oid, tags, geom):
        name = tags.get("name") or tags.get("name:ru") or tags.get("name:en")
        if tags.get("tourism") not in LODGING or not name:
            return
        p = geom.representative_point()
        if not bounds.covers(p):
            return
        hotels.append({"id": oid, "name": name, "lon": p.x, "lat": p.y, "tags": tags,
                       "footprint": mapping(geom) if geom.geom_type == "Polygon" else None})

    def append(name, oid, tags, geom):
        props = {**tags, "osm_id": oid, "source_id": "osm_geofabrik", "observed_at": SNAPSHOT}
        if name == "buildings":
            height = number(tags.get("height"))
            levels = number(tags.get("building:levels"))
            props.update(height_m=height if height else levels * CONFIG["height"]["floor_height_m"] if levels else None,
                         height_source="height" if height else "levels" if levels else "unknown",
                         levels=levels, height_uncertainty_m=CONFIG["height"]["tagged_uncertainty_m"] if height else CONFIG["height"]["levels_uncertainty_m"] if levels else None)
            try:
                if float(tags.get("height", "nan")) <= 0:
                    props["height_invalid_reason"] = "Nonpositive upstream height tag retained in original properties"
            except ValueError:
                pass
        layers[name].append({"type": "Feature", "id": oid, "properties": props, "geometry": mapping(geom)})

    class Handler(osmium.SimpleHandler):
        def node(self, n):
            lon, lat = n.location.lon, n.location.lat
            if not any(b[0] <= lon <= b[2] and b[1] <= lat <= b[3] for b in COVERAGE_BBOXES):
                return
            tags = dict(n.tags)
            if tags:
                node_tags[str(n.id)] = tags
            oid = f"osm-node-{n.id}"
            p = Point(lon, lat)
            hotel(oid, tags, p)
            if any(k in tags for k in ("amenity", "shop", "leisure", "tourism", "healthcare", "barrier")) or tags.get("highway") == "crossing":
                append("pois", oid, tags, p)

        def way(self, w):
            tags = dict(w.tags)
            if not any(k in tags for k in ("building", "building:part", "amenity", "shop", "leisure", "tourism", "highway", "railway", "natural", "landuse")):
                return
            try:
                coords = [(n.lon, n.lat) for n in w.nodes]
            except osmium.InvalidLocationError:
                return
            if len(coords) < 2:
                return
            west, south = min(c[0] for c in coords), min(c[1] for c in coords)
            east, north = max(c[0] for c in coords), max(c[1] for c in coords)
            if east < envelope[0] or west > envelope[2] or north < envelope[1] or south > envelope[3]:
                return
            line = LineString(coords)
            if not line.intersects(bounds):
                return
            closed = coords[0] == coords[-1] and len(coords) >= 4
            polygon = Polygon(coords) if closed else None
            if polygon is not None and not polygon.is_valid:
                polygon = polygon.buffer(0)
            geom = polygon if polygon is not None and polygon.geom_type == "Polygon" else line
            oid = f"osm-way-{w.id}"
            hotel(oid, tags, geom)
            if "building" in tags or "building:part" in tags:
                if geom.geom_type == "Polygon":
                    append("buildings", oid, tags, geom)
            if any(k in tags for k in ("amenity", "shop", "leisure", "tourism", "healthcare")) or tags.get("landuse") == "construction":
                append("pois", oid, tags, geom)
            if "highway" in tags:
                append("roads", oid, tags, line)
                self.walk(w, tags, coords)
            if "railway" in tags:
                append("railways", oid, tags, line)
            if tags.get("natural") == "beach":
                append("beaches", oid, tags, geom)
            if tags.get("natural") == "coastline":
                append("coastline", oid, tags, line)

        def walk(self, w, tags, coords):
            allowed = tags.get("foot") in {"yes", "designated", "permissive"}
            if tags.get("foot") in {"no", "private"} or (tags.get("access") in {"no", "private"} and not allowed):
                return
            if tags.get("highway") in {"motorway", "motorway_link", "trunk", "trunk_link", "construction", "proposed", "raceway"} and not allowed:
                return
            # OSM car one-way does not imply foot one-way. Respect explicit foot direction.
            oneway = tags.get("oneway:foot")
            for a, b, ca, cb in zip(w.nodes, list(w.nodes)[1:], coords, coords[1:]):
                if not (bounds.covers(Point(ca)) and bounds.covers(Point(cb))):
                    continue
                u, v = str(a.ref), str(b.ref)
                if any(node_tags.get(k, {}).get("access") in {"no", "private"} and node_tags.get(k, {}).get("foot") not in {"yes", "designated", "permissive"} for k in [u, v]):
                    continue
                length = GEOD.inv(*ca, *cb)[2]
                if length <= 0:
                    continue
                nodes[u] = {"id": u, "lon": ca[0], "lat": ca[1]}
                nodes[v] = {"id": v, "lon": cb[0], "lat": cb[1]}
                crossing = any(node_tags.get(k, {}).get("highway") == "crossing" for k in [u, v])
                base = dict(length_m=round(length, 3), highway=tags["highway"], steps=tags["highway"] == "steps",
                            tunnel=tags.get("tunnel", "no") != "no", bridge=tags.get("bridge", "no") != "no",
                            crossing=crossing, access=tags.get("foot", tags.get("access", "unknown")),
                            osm_way_id=f"osm-way-{w.id}", source_id="osm_geofabrik")
                if oneway != "-1":
                    edges.append(dict(u=u, v=v, geometry=[ca, cb], **base))
                if oneway not in {"yes", "1", "true"}:
                    edges.append(dict(u=v, v=u, geometry=[cb, ca], **base))

    Handler().apply_file(str(path), locations=True, idx="flex_mem")
    dump(RAW / "osm_hotels.json", hotels)
    for name, features in layers.items():
        dump(SEED / (name + ".geojson"), collection(features))
    gzip_json(SEED / "walk_graph.json.gz", {"nodes": sorted(nodes.values(), key=lambda n: int(n["id"])), "edges": edges})
    dump(RAW / "osm_stage.json", {"source": source("osm_geofabrik", "OpenStreetMap / Geofabrik South Federal District", URL,
         "ODbL-1.0", "© OpenStreetMap contributors; extract by Geofabrik", SNAPSHOT, meta["retrieved_at"],
         "Way polygons and tagged nodes; multipolygon relation geometries are not assembled. Missing tags are unknown."),
         "download": meta, "bbox": list(envelope), "coverage_bboxes": COVERAGE_BBOXES, "counts": {**{k: len(v) for k, v in layers.items()}, "hotels": len(hotels), "walk_nodes": len(nodes), "walk_edges": len(edges)}})
    print(json.dumps({"hotels": len(hotels), **{k: len(v) for k, v in layers.items()}, "nodes": len(nodes), "edges": len(edges)}), flush=True)


if __name__ == "__main__":
    run()
