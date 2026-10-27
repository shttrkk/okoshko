"""Offline OSM address supplement; never fetch, geocode, or choose a nearest address.

    python -m pipeline.enrich_addresses --data-dir data
    python -m pipeline.enrich_addresses --data-dir data --build-snapshot-from-cache /path/to/cache

The second form selects only the four reviewed administrative relation IDs. Geometry is
retained verbatim as JSON coordinates. Existing hotel/source seeds are never rewritten.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from collections import Counter
from datetime import timedelta
from pathlib import Path

from shapely.geometry import Point, shape

from backend.addresses import (
    MAX_ADDRESS_BYTES,
    METHOD,
    _timestamp,
    canonical_bytes,
    digest,
    generic_address,
    validate_document,
)
from backend.contracts import Evidence, Source

ROOT = Path(__file__).resolve().parents[1]
MAX_SNAPSHOT_BYTES = 50_000_000
DISTRICTS = {1116490: "Центральный район", 907728: "Хостинский район",
             5650614: "Адлерский район", 11865346: "Сириус"}
DISTRICT_SOURCE = "osm_admin_districts"
SNAPSHOT = "/data/seed/admin_districts.geojson.gz"
LIMITATIONS = [
    "Улица и дом используются только из связанного OSM-контура, строго содержащего точку отеля; ближайший адрес не подставляется.",
    "Граница района и адресные теги относятся к сохранённым снимкам OSM; вход в отель и табличка на здании не проверены.",
    "Confidence — условная оценка происхождения, не измеренная точность адресов.",
    "Сириус — отдельный городской округ; отсутствие однозначного полигона не заменяется ближайшим районом.",
]


def write_json(path: Path, value, *, compressed=False):
    payload = canonical_bytes(value) + b"\n"
    if compressed:
        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0) as stream:
            stream.write(payload)
        payload = buffer.getvalue()
    if len(payload) >= MAX_SNAPSHOT_BYTES:
        raise ValueError("Address artifact exceeds 50 MB")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def read_json(path: Path):
    if path.name.endswith(".gz"):
        with gzip.open(path, "rb") as stream:
            payload = stream.read(MAX_SNAPSHOT_BYTES + 1)
    else:
        with path.open("rb") as stream:
            payload = stream.read(MAX_SNAPSHOT_BYTES + 1)
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Address input exceeds 50 MB")
    return json.loads(payload)


def snapshot_from_cache(cache_dir: Path):
    selected = {}
    for path in sorted(cache_dir.glob("*.json")):
        document = read_json(path)
        retrieved = document.get("retrieved_at")
        _timestamp(retrieved)
        for row in document.get("results", []):
            osm_id = row.get("osm_id")
            if row.get("osm_type") != "relation" or osm_id not in DISTRICTS:
                continue
            geometry = row.get("geojson")
            polygon = shape(geometry)
            if row.get("type") != "administrative" or polygon.geom_type not in {"Polygon", "MultiPolygon"} or not polygon.is_valid or polygon.is_empty:
                raise ValueError("Reviewed relation is not an administrative polygon")
            feature = {"type": "Feature", "id": f"osm-relation-{osm_id}", "geometry": geometry,
                       "properties": {"name": row["name"], "display_name": DISTRICTS[osm_id], "osm_id": osm_id,
                                      "source_url": f"https://www.openstreetmap.org/relation/{osm_id}",
                                      "retrieved_at": retrieved, "raw_display_name": row.get("display_name"),
                                      "nominatim_url": document["url"], "response_sha256": document["sha256"],
                                      "cache_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
            if osm_id in selected and selected[osm_id] != feature:
                raise ValueError("Conflicting cached administrative relations")
            selected[osm_id] = feature
    if set(selected) != set(DISTRICTS):
        raise ValueError("The explicit cache must contain all four reviewed administrative relations")
    retrieved = max((feature["properties"]["retrieved_at"] for feature in selected.values()), key=_timestamp)
    source = Source(id=DISTRICT_SOURCE, name="Административные границы OpenStreetMap: Сочи и Сириус", kind="open_data",
                    url=SNAPSHOT, license="ODbL-1.0", attribution="© OpenStreetMap contributors",
                    observed_at=retrieved, retrieved_at=retrieved,
                    notes="Четыре исходных OSM relation-полигона, полученных через публичный Nominatim и сохранённых офлайн. observed_at — время получения снимка, не редактирования OSM.")
    return {"type": "FeatureCollection", "schema_version": 1, "source": source.model_dump(mode="json"),
            "features": [selected[key] for key in sorted(selected)]}


def read_snapshot(path: Path):
    if not path.exists():
        return None
    snapshot = read_json(path)
    if snapshot.get("type") != "FeatureCollection" or snapshot.get("schema_version") != 1:
        raise ValueError("Invalid administrative snapshot")
    source = Source.model_validate(snapshot["source"])
    seen = set()
    for feature in snapshot["features"]:
        if feature["id"] in seen or feature["id"] != f"osm-relation-{feature['properties']['osm_id']}":
            raise ValueError("Invalid administrative feature id")
        seen.add(feature["id"])
        polygon = shape(feature["geometry"])
        if polygon.geom_type not in {"Polygon", "MultiPolygon"} or not polygon.is_valid or polygon.is_empty:
            raise ValueError("Invalid administrative geometry")
        _timestamp(feature["properties"]["retrieved_at"])
    if source.id != DISTRICT_SOURCE:
        raise ValueError("Unexpected administrative source")
    if seen != {f"osm-relation-{osm_id}" for osm_id in DISTRICTS}:
        raise ValueError("Unexpected administrative relation selection")
    return snapshot


def enrich(hotels, buildings, sources, snapshot, *, region="unknown", input_hashes=None):
    """Topology only. A linked building outside the point is never an address witness."""
    source_map = {row["id"]: Source.model_validate(row).model_dump(mode="json") for row in sources}
    if snapshot:
        source_map[snapshot["source"]["id"]] = snapshot["source"]
    features = snapshot["features"] if snapshot else []
    districts = [(feature, shape(feature["geometry"])) for feature in features]
    linked = {hotel.get("tags", {}).get("building_id") for hotel in hotels}
    by_building = {feature["id"]: feature for feature in buildings if feature["id"] in linked}
    entries, used_sources = [], set()
    counts = Counter(total_hotels=len(hotels), base_specific=sum(not generic_address(h.get("address")) for h in hotels),
                     base_generic=sum(generic_address(h.get("address")) for h in hotels),
                     district_covered=0, district_missing=0, district_ambiguous=0,
                     linked_building_contains=0, linked_building_outside_or_invalid=0,
                     new_building_address=0, precision_source=0, precision_building=0, precision_street=0, precision_area=0,
                     unchanged_unknown=0, unchanged_without_provenance=0)
    for hotel in sorted(hotels, key=lambda row: row["id"]):
        point = Point(hotel["lon"], hotel["lat"])
        containing = [feature for feature, polygon in districts if polygon.contains(point)]
        district = containing[0] if len(containing) == 1 else None
        counts["district_covered" if district else "district_ambiguous" if len(containing) > 1 else "district_missing"] += 1
        source_ids = [source for source in hotel.get("source_ids", []) if source in source_map]
        if not source_ids and not district:
            counts["unchanged_without_provenance"] += 1
            continue
        source_id = source_ids[0] if source_ids else DISTRICT_SOURCE
        base = hotel.get("address")
        address, precision = base, "source"
        observed = source_map[source_id]["retrieved_at"]
        evidence = []
        if source_ids:
            evidence.append(Evidence(id="address-base-" + hotel["id"], kind="open_data", label="Исходная запись отеля",
                                     source_ids=source_ids, artifact="/data/seed/hotels.json",
                                     inputs={"hotel_id": hotel["id"], "lat": hotel["lat"], "lon": hotel["lon"],
                                             "base_address": base, "hotel_record_sha256": digest(hotel),
                                             "input_sha256": (input_hashes or {}).get("hotels")}))
        if district:
            p = district["properties"]
            evidence.append(Evidence(id="address-district-" + hotel["id"], kind="open_data", label=p["name"],
                                     source_ids=[DISTRICT_SOURCE], artifact=SNAPSHOT, url=p["source_url"],
                                     inputs={"district_id": district["id"], "osm_id": p["osm_id"], "name": p["name"],
                                             "retrieved_at": p["retrieved_at"], "geometry_sha256": digest(district["geometry"]),
                                             "input_sha256": (input_hashes or {}).get("districts"), "contains_point": True}))
        building = by_building.get(hotel.get("tags", {}).get("building_id"))
        building_address = None
        if generic_address(base) and building:
            try:
                polygon = shape(building.get("geometry"))
                contains = polygon.geom_type in {"Polygon", "MultiPolygon"} and polygon.is_valid and polygon.contains(point)
            except (AttributeError, TypeError, ValueError):
                contains = False
            if contains:
                counts["linked_building_contains"] += 1
                props = building.get("properties", {})
                street, house = props.get("addr:street"), props.get("addr:housenumber")
                if isinstance(street, str) and street.strip() and props.get("source_id") == "osm_geofabrik" and "osm_geofabrik" in source_map:
                    source_id = "osm_geofabrik"
                    observed = source_map[source_id]["retrieved_at"]
                    house = house.strip() if isinstance(house, str) else None
                    street = street.strip()
                    building_address = [street, *([house] if house else [])]
                    precision = "building" if house else "street"
                    evidence.append(Evidence(id="address-building-" + hotel["id"], kind="open_data", label="Адрес связанного контура OSM",
                                             source_ids=[source_id], artifact="/data/seed/buildings.geojson.gz",
                                             url="https://www.openstreetmap.org/" + building["id"].removeprefix("osm-").replace("-", "/", 1),
                                             inputs={"building_id": building["id"], "addr:street": props.get("addr:street"),
                                                     "addr:housenumber": props.get("addr:housenumber"), "contains_point": True,
                                                     "feature_sha256": digest(building), "input_sha256": (input_hashes or {}).get("buildings")},
                                             limitations=["Вход в отель не проверен; отсутствующий номер дома не угадывается"]))
            else:
                counts["linked_building_outside_or_invalid"] += 1
        if generic_address(base):
            city = "Сириус" if district and district["properties"]["osm_id"] == 11865346 else base
            if building_address:
                parts = [city, *building_address]
                if district and district["properties"]["display_name"] != city:
                    parts.append(district["properties"]["display_name"])
                address = ", ".join(str(part) for part in parts if part)
                counts["new_building_address"] += 1
            elif district:
                label = district["properties"]["display_name"]
                address = ", ".join(part for part in (city, label if label != city else None) if part)
                precision, source_id, observed = "area", DISTRICT_SOURCE, district["properties"]["retrieved_at"]
            else:
                counts["unchanged_unknown"] += 1
        entry = {"hotel_id": hotel["id"], "lat": hotel["lat"], "lon": hotel["lon"], "base_address": base,
                 "address": address, "precision": precision, "district": district["id"] if district else None,
                 "district_display_name": district["properties"]["display_name"] if district else None,
                 "source": source_id, "method": METHOD, "evidence": [item.model_dump(mode="json", exclude_none=True) for item in evidence],
                 "confidence": {"source": .75, "building": .85, "street": .8, "area": .7}[precision]
                               if district or not generic_address(address) else 0,
                 "observed_at": observed, "valid_until": (_timestamp(observed) + timedelta(days=365)).isoformat()}
        entries.append(entry)
        used_sources.add(source_id)
        used_sources.update(source for item in evidence for source in item.source_ids)
        counts["precision_" + precision] += 1
    counts["entries"] = len(entries)
    result = {"schema_version": 1, "method": METHOD, "region": region,
              "sources": [source_map[key] for key in sorted(used_sources)], "entries": entries,
              "counts": dict(sorted(counts.items())), "limitations": LIMITATIONS}
    return validate_document(result, hotels)


def output_metadata(path: Path):
    payload = path.read_bytes()
    result = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    if path.name.endswith(".geojson.gz"):
        original = gzip.decompress(payload)
        result.update(encoding="gzip", logical_path="data/seed/" + path.name.removesuffix(".gz"),
                      uncompressed_bytes=len(original), uncompressed_sha256=hashlib.sha256(original).hexdigest())
    return result


def run(data_dir: Path, *, cache_dir: Path | None = None):
    seed = data_dir / "seed"
    snapshot_path = seed / "admin_districts.geojson.gz"
    if cache_dir is not None:
        write_json(snapshot_path, snapshot_from_cache(cache_dir), compressed=True)
    snapshot = read_snapshot(snapshot_path)
    hotels, sources = read_json(seed / "hotels.json"), read_json(seed / "sources.json")
    buildings_path = seed / "buildings.geojson.gz"
    buildings = read_json(buildings_path)["features"] if buildings_path.exists() else []
    manifest_path = seed / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    hashes = {"hotels": hashlib.sha256((seed / "hotels.json").read_bytes()).hexdigest(),
              "buildings": hashlib.sha256(buildings_path.read_bytes()).hexdigest() if buildings_path.exists() else None,
              "districts": hashlib.sha256(snapshot_path.read_bytes()).hexdigest() if snapshot_path.exists() else None}
    region = manifest.get("region_config", {}).get("parameters", {}).get("id", "unknown")
    document = enrich(hotels, buildings, sources, snapshot, region=region, input_hashes=hashes)
    addresses = seed / "hotel_addresses.json"
    if len(canonical_bytes(document)) + 1 > MAX_ADDRESS_BYTES:
        raise ValueError("Address sidecar exceeds its runtime byte budget")
    write_json(addresses, document)
    if manifest_path.exists():
        for path in (addresses, snapshot_path):
            if path.exists():
                manifest.setdefault("outputs", {})["data/seed/" + path.name] = output_metadata(path)
        supplemental = {source["id"]: source for source in manifest.get("supplemental_sources", [])}
        if snapshot:
            supplemental[DISTRICT_SOURCE] = snapshot["source"]
        manifest["supplemental_sources"] = [supplemental[key] for key in sorted(supplemental)]
        write_json(manifest_path, manifest)
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--build-snapshot-from-cache", type=Path, metavar="CACHE_DIR")
    args = parser.parse_args()
    print(json.dumps(run(args.data_dir, cache_dir=args.build_snapshot_from_cache)["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
