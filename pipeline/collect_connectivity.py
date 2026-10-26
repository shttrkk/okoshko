"""Offline OSM property internet observations, without throughput claims.

Reprocess the shipped extract without PBF/raw inputs:
    python -m pipeline.collect_connectivity --data-dir data

Rebuild the small curated extract from the pinned local OSM materialization:
    python -m pipeline.collect_connectivity --data-dir data --rebuild-extract \
      --raw-hotels data/raw/osm_hotels.json \
      --download-metadata data/raw/south-fed-district-261025.osm.pbf.metadata.json

No network calls, registrations, neighboring POI inference or speed estimates.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from shapely.geometry import Point, shape

from backend.contracts import CharacteristicSpec, Claim
from geo.coordinates import TO_UTM, metric

METHOD = "osm_property_internet_access_v1"
CONFIDENCE = .55
EXTRACT_MAX_BYTES = 100_000
LODGING = {"hotel", "guest_house", "hostel", "apartment", "motel", "resort"}
ALIAS_MARGIN_M = 1.0
WLAN_STATEMENT = "OSM сообщает WLAN, скорость/работоспособность не измерена"
UNSPECIFIED_STATEMENT = "OSM сообщает наличие интернета, тип подключения неизвестен; скорость/работоспособность не измерена"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _name(value):
    value = re.sub(r"\b(?:hotel|отель|гостиница)\b", "", str(value).casefold())
    return "".join(character for character in value if character.isalnum())


def _osm_url(osm_id):
    match = re.fullmatch(r"osm-(node|way)-(\d+)", osm_id)
    if not match:
        raise ValueError("observation requires an exact OSM node/way ID")
    return "https://www.openstreetmap.org/"+match[1]+"/"+match[2]


def _property(row):
    tags = row["tags"]
    return {"osm_id": row["id"], "name": row["name"], "tourism": tags.get("tourism"),
            "lon": row["lon"], "lat": row["lat"],
            "address": {key: tags.get(key) for key in ("addr:street", "addr:housenumber")},
            "footprint": row.get("footprint")}


def _alias_proof(canonical, observation, pinned_id):
    """Business identity plus robust interior, never a nearest-POI association."""
    if (canonical["tourism"] not in LODGING or observation["tourism"] != canonical["tourism"]
            or pinned_id != observation["osm_id"] or not _name(canonical["name"])
            or _name(canonical["name"]) != _name(observation["name"])):
        return None
    address = canonical["address"]
    if not all(address.get(key) and address[key] == observation["address"].get(key)
               for key in ("addr:street", "addr:housenumber")):
        return None
    try:
        footprint = shape(canonical["footprint"])
        if footprint.geom_type not in {"Polygon", "MultiPolygon"} or not footprint.is_valid:
            return None
        point = Point(observation["lon"], observation["lat"])
        projected = metric(footprint)
        projected_point = Point(TO_UTM.transform(point.x, point.y))
        margin = projected.boundary.distance(projected_point)
        if not footprint.contains(point) or not projected.contains(projected_point) or margin <= ALIAS_MARGIN_M:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return {"normalized_names_equal": True, "same_street_house_number": True,
            "strict_contains_wgs84_and_utm": True, "boundary_margin_m": round(margin, 6),
            "minimum_margin_m": ALIAS_MARGIN_M}


def build_extract(hotels, raw_hotels, source, download_metadata, *, input_hashes, synthetic=False):
    """Curate only canonical hotel elements and re-checkable pinned aliases."""
    raw = {row["id"]: row for row in raw_hotels}
    records = []
    rejected = []
    for hotel in sorted(hotels, key=lambda row: row["id"]):
        canonical_row = raw.get(hotel["id"])
        if not canonical_row or canonical_row["tags"].get("tourism") not in LODGING:
            continue
        canonical = _property(canonical_row)
        candidates = [(canonical_row, {"kind": "canonical_osm_hotel", "canonical_osm_id": hotel["id"]})]
        for reference in hotel.get("tags", {}).get("source_records", []):
            alias = raw.get(reference["id"])
            if not alias or alias["id"] == hotel["id"] or "internet_access" not in alias["tags"]:
                continue
            if reference.get("source_id") != source["id"]:
                continue
            observation = _property(alias)
            proof = _alias_proof(canonical, observation, reference["id"])
            if proof is None:
                rejected.append({"hotel_id": hotel["id"], "osm_id": alias["id"], "reason": "property_identity_not_established"})
                continue
            candidates.append((alias, {"kind": "reviewed_osm_alias", "canonical_property": canonical,
                                       "observation_property": observation, "pinned_source_record_id": reference["id"],
                                       "proof": proof}))
        for row, identity in candidates:
            if "internet_access" not in row["tags"]:
                continue
            tags = {key: row["tags"][key] for key in ("internet_access", "internet_access:fee") if key in row["tags"]}
            record = {"hotel_id": hotel["id"], "hotel_name": hotel["name"], "osm_id": row["id"],
                      "osm_name": row["name"], "tourism": row["tags"]["tourism"], "raw_tags": tags,
                      "source_url": _osm_url(row["id"]), "identity": identity}
            record["sha256"] = _digest(record)
            records.append(record)
    return {"version": 1, "method": METHOD, "synthetic": bool(synthetic),
            "source": {"id": source["id"], "name": source["name"], "url": download_metadata["url"],
                       "license": source["license"], "attribution": source["attribution"],
                       "observed_at": source["observed_at"], "retrieved_at": download_metadata["retrieved_at"],
                       "pbf_sha256": download_metadata["sha256"], "input_hashes": input_hashes},
            "selection": {"hotel_portfolio_count": len(hotels),
                          "canonical": "Exact canonical OSM hotel element ID, not a nearby building or cafe",
                          "aliases": "Pinned source record; same lodging type/name/address; strict interior with >1m margin in WGS84/UTM",
                          "missing_tags": "Unknown; not a negative service or throughput observation"},
            "records": sorted(records, key=lambda row: (row["hotel_id"], row["osm_id"])),
            "rejected_aliases": rejected}


def _validate_record(record):
    expected = _digest({key: value for key, value in record.items() if key != "sha256"})
    if record.get("sha256") != expected:
        raise ValueError("curated observation hash mismatch")
    if record.get("tourism") not in LODGING or not isinstance(record["raw_tags"].get("internet_access"), str):
        raise ValueError("internet observation must come from a tagged lodging property")
    if record["source_url"] != _osm_url(record["osm_id"]):
        raise ValueError("OSM ID/source URL mismatch")
    identity = record["identity"]
    if identity["kind"] == "canonical_osm_hotel":
        if not record["hotel_id"] == record["osm_id"] == identity["canonical_osm_id"]:
            raise ValueError("canonical property identity mismatch")
    elif identity["kind"] == "reviewed_osm_alias":
        proof = _alias_proof(identity["canonical_property"], identity["observation_property"],
                             identity["pinned_source_record_id"])
        if (proof is None or identity["canonical_property"]["osm_id"] != record["hotel_id"]
                or identity["observation_property"]["osm_id"] != record["osm_id"]
                or proof != identity["proof"]):
            raise ValueError("alias property identity proof failed")
    else:
        raise ValueError("unsupported property identity proof")


def _value(tags):
    raw = tags["internet_access"]
    types = {part.strip().casefold() for part in raw.split(";")}
    wifi = True if "wlan" in types else False if types == {"no"} else None
    statement = WLAN_STATEMENT if wifi is True else UNSPECIFIED_STATEMENT
    if types == {"no"}:
        statement = "OSM сообщает отсутствие публичного доступа к интернету; скорость не измерена"
    return {"internet_access": raw, "wifi_reported": wifi, "fee": tags.get("internet_access:fee"),
            "download_mbps": None, "upload_mbps": None, "latency_ms": None,
            "kind": "osm_property_availability", "measurement": False, "statement": statement}


def claims_from_extract(extract_bytes: bytes, spec: CharacteristicSpec, *, artifact="/data/seed/connectivity_observations.json"):
    if spec.id != "connectivity":
        raise ValueError("OSM internet observations require the connectivity spec")
    extract = json.loads(extract_bytes)
    if len(extract_bytes) > EXTRACT_MAX_BYTES or extract.get("version") != 1 or extract.get("method") != METHOD:
        raise ValueError("unsupported or oversized connectivity extract")
    if _collisions(extract["records"]):
        # Resolver keeps the latest observation per source/method/scope. Never
        # let contradictory same-source records turn into an arbitrary winner.
        raise ValueError("conflicting OSM property reports require source review before publication")
    source = extract["source"]
    observed = datetime.fromisoformat(source["observed_at"].replace("Z", "+00:00"))
    if observed.tzinfo is None:
        raise ValueError("source observation needs a timezone")
    extract_sha = hashlib.sha256(extract_bytes).hexdigest()
    claims = []
    seen = set()
    for record in extract["records"]:
        _validate_record(record)
        identity = record["hotel_id"], record["osm_id"]
        if identity in seen:
            raise ValueError("duplicate property observation")
        seen.add(identity)
        value = _value(record["raw_tags"])
        claim_id = "connectivity-osm-"+_digest([METHOD, spec.version, spec.ttl_days, extract_sha, record["sha256"]])[:24]
        row = {"id": claim_id, "hotel_id": record["hotel_id"], "characteristic_id": "connectivity",
               "spec_version": spec.version, "scope": {"type": "hotel"}, "value": value,
               "source": source["id"], "method": METHOD, "confidence": CONFIDENCE,
               "observed_at": observed.isoformat(), "valid_until": (observed+timedelta(days=spec.ttl_days)).isoformat(),
               "synthetic": bool(extract.get("synthetic")),
               "evidence": [{"id": "ev-"+claim_id, "kind": "simulation" if extract.get("synthetic") else "open_data",
                             "label": "Сообщённое наличие интернета на OSM-объекте отеля",
                             "source_ids": [source["id"]], "url": record["source_url"], "artifact": artifact,
                             "inputs": {"extract_sha256": extract_sha, "observation_sha256": record["sha256"],
                                        "pbf_sha256": source["pbf_sha256"], "input_hashes": source["input_hashes"],
                                        "osm_id": record["osm_id"], "identity": record["identity"]},
                             "output": value, "limitations": [
                                 "OSM-тег сообщает наличие услуги на объекте, не скорость, стабильность или работоспособность сети",
                                 "Наличие WLAN не подтверждает покрытие конкретного номера или пригодность для работы",
                                 "internet_access=yes не указывает тип подключения; неизвестное не заменено WLAN",
                                 "Дата — снимок OSM, а не дата полевого теста; сведения могли устареть",
                                 "Теги соседних кафе и неподтверждённых соседних зданий не используются"]}]}
        Claim.model_validate(row)
        claims.append(row)
    return claims


def _collisions(records):
    grouped = defaultdict(list)
    for record in records:
        grouped[record["hotel_id"]].append(record)
    collisions = []
    for hotel_id, observations in grouped.items():
        for field in ("internet_access", "internet_access:fee"):
            values = {record["raw_tags"][field] for record in observations if record["raw_tags"].get(field) is not None}
            if len(values) > 1:
                collisions.append({"hotel_id": hotel_id, "field": field, "values": sorted(values),
                                   "osm_ids": sorted(record["osm_id"] for record in observations)})
    return collisions


def collect(data_dir: Path, *, extract_path=None, output_path=None, rebuild=False, raw_hotels_path=None,
            download_metadata_path=None):
    data_dir = Path(data_dir)
    extract_path = Path(extract_path) if extract_path else data_dir/"seed"/"connectivity_observations.json"
    output_path = Path(output_path) if output_path else data_dir/"seed"/"connectivity_claims.jsonl.gz"
    spec = CharacteristicSpec.model_validate(yaml.safe_load((data_dir/"characteristics"/"connectivity.yaml").read_text()))
    if rebuild:
        if raw_hotels_path is None or download_metadata_path is None:
            raise ValueError("rebuilding requires the pinned local raw hotels and download metadata")
        raw_hotels_path, download_metadata_path = Path(raw_hotels_path), Path(download_metadata_path)
        hotels_path = data_dir/"seed"/"hotels.json"
        source = next(row for row in json.loads((data_dir/"seed"/"sources.json").read_text()) if row["id"] == "osm_geofabrik")
        extract = build_extract(json.loads(hotels_path.read_text()), json.loads(raw_hotels_path.read_text()), source,
                                json.loads(download_metadata_path.read_text()), input_hashes={
                                    "hotels_json_sha256": hashlib.sha256(hotels_path.read_bytes()).hexdigest(),
                                    "raw_osm_hotels_sha256": hashlib.sha256(raw_hotels_path.read_bytes()).hexdigest()})
        encoded = (json.dumps(extract, ensure_ascii=False, sort_keys=True, indent=2)+"\n").encode()
        if len(encoded) > EXTRACT_MAX_BYTES:
            raise ValueError("curated source extract exceeds 100KB")
        extract_path.parent.mkdir(parents=True, exist_ok=True)
        extract_path.write_bytes(encoded)
    extract_bytes = extract_path.read_bytes()
    extract = json.loads(extract_bytes)
    claims = claims_from_extract(extract_bytes, spec)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix+".tmp")
    with temporary.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as stream:
        for claim in claims:
            stream.write((_json(claim)+"\n").encode())
    temporary.replace(output_path)
    ids = {claim["hotel_id"] for claim in claims}
    existing = []
    open_ledger = data_dir/"seed"/"open_claims.jsonl.gz"
    if open_ledger.is_file():
        with gzip.open(open_ledger, "rt") as stream:
            existing = [row for line in stream if (row := json.loads(line))["characteristic_id"] == "connectivity"]
    return {"method": METHOD, "claims": len(claims), "hotels": len(ids),
            "canonical_records": sum(r["identity"]["kind"] == "canonical_osm_hotel" for r in extract["records"]),
            "reviewed_alias_records": sum(r["identity"]["kind"] == "reviewed_osm_alias" for r in extract["records"]),
            "wifi_reported_true": sum(c["value"]["wifi_reported"] is True for c in claims),
            "wifi_reported_false": sum(c["value"]["wifi_reported"] is False for c in claims),
            "wifi_reported_unknown": sum(c["value"]["wifi_reported"] is None for c in claims),
            "fee_tags": dict(Counter(c["value"]["fee"] for c in claims if c["value"]["fee"] is not None)),
            "speed_observations": 0, "property_tag_collisions": _collisions(extract["records"]),
            "existing_seed_connectivity_claims": len(existing),
            "existing_seed_source_overlap_hotels": sorted(ids & {row["hotel_id"] for row in existing}),
            "extract": {"path": str(extract_path), "bytes": len(extract_bytes), "sha256": hashlib.sha256(extract_bytes).hexdigest()},
            "ledger": {"path": str(output_path), "bytes": output_path.stat().st_size,
                       "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--extract", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rebuild-extract", action="store_true")
    parser.add_argument("--raw-hotels", type=Path)
    parser.add_argument("--download-metadata", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = collect(args.data_dir, extract_path=args.extract, output_path=args.output, rebuild=args.rebuild_extract,
                     raw_hotels_path=args.raw_hotels, download_metadata_path=args.download_metadata)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
