"""Bounded offline address sidecar and conservative, compare-and-set seed projection."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from backend.contracts import Evidence, Source

METHOD = "osm_building_containment_and_district_boundaries_v1"
ARTIFACT = "/data/seed/hotel_addresses.json"
MAX_ADDRESS_BYTES = 4_000_000
MAX_BASE_HOTELS_BYTES = 50_000_000
MAX_ENTRIES = 10_000
MAX_EVIDENCE_BYTES = 20_000
MAX_ADDRESS_LENGTH = 1000


def canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_bounded(path: Path, limit: int):
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError(f"Address input exceeds {limit} bytes: {path.name}")
    return json.loads(payload)


def generic_address(value) -> bool:
    return value is None or isinstance(value, str) and value.strip().casefold() in {"", "сочи"}


def binding(entry) -> dict:
    return {key: entry[key] for key in ("hotel_id", "lat", "lon", "base_address")}


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Address timestamps must be ISO strings")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Address timestamps must include a timezone")
    return parsed


def validate_document(document, base_hotels):
    if (not isinstance(document, dict) or document.get("schema_version") != 1
            or document.get("method") != METHOD or not isinstance(document.get("region"), str)):
        raise ValueError("Invalid address supplement envelope")
    if not isinstance(document.get("sources"), list) or len(document["sources"]) > 64:
        raise ValueError("Invalid supplemental source registry")
    sources = [Source.model_validate(row) for row in document["sources"]]
    source_ids = {source.id for source in sources}
    if len(source_ids) != len(sources):
        raise ValueError("Duplicate address source")
    for source in sources:
        if (source.observed_at.tzinfo is None or source.retrieved_at.tzinfo is None
                or not all(math.isfinite(value) and value > 0 for value in (source.alpha, source.beta))):
            raise ValueError("Invalid address source dates or priors")
    entries = document.get("entries")
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ValueError("Too many or invalid address entries")
    hotels = {row["id"]: row for row in base_hotels}
    seen = set()
    for entry in entries:
        required = {"hotel_id", "lat", "lon", "base_address", "address", "precision", "district",
                    "district_display_name", "source", "method", "evidence", "confidence", "observed_at", "valid_until"}
        if not isinstance(entry, dict) or not required <= entry.keys():
            raise ValueError("Incomplete address entry")
        hotel_id = entry.get("hotel_id")
        if hotel_id in seen or hotel_id not in hotels:
            raise ValueError("Duplicate or unknown address hotel id")
        seen.add(hotel_id)
        hotel = hotels[hotel_id]
        for key, lower, upper in (("lat", -90, 90), ("lon", -180, 180)):
            value = entry.get(key)
            if (not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value)
                    or not lower <= value <= upper or value != hotel.get(key)):
                raise ValueError("Address coordinate binding mismatch")
        if entry.get("base_address") != hotel.get("address"):
            raise ValueError("Address base_address binding mismatch")
        for key in ("base_address", "address"):
            if entry.get(key) is not None and (not isinstance(entry[key], str) or len(entry[key]) > MAX_ADDRESS_LENGTH):
                raise ValueError("Invalid address string")
        if entry.get("precision") not in {"source", "building", "street", "area"}:
            raise ValueError("Invalid address precision")
        if entry.get("method") != METHOD or entry.get("source") not in source_ids:
            raise ValueError("Unknown address method or source")
        district, name = entry.get("district"), entry.get("district_display_name")
        if (district is None) != (name is None) or district is not None and (
            not isinstance(district, str) or not district.startswith("osm-relation-")
            or not isinstance(name, str) or not 0 < len(name) <= 200
        ):
            raise ValueError("Invalid address district")
        confidence = entry.get("confidence")
        if type(confidence) not in {int, float} or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Invalid address confidence")
        if _timestamp(entry["valid_until"]) <= _timestamp(entry["observed_at"]):
            raise ValueError("Invalid address validity interval")
        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 8 or len(canonical_bytes(evidence)) > MAX_EVIDENCE_BYTES:
            raise ValueError("Invalid or oversized address evidence")
        witnesses = set()
        for item in evidence:
            parsed = Evidence.model_validate(item)
            if parsed.kind != "open_data" or not parsed.source_ids or not set(parsed.source_ids) <= source_ids:
                raise ValueError("Unknown address evidence source")
            witnesses.update(parsed.source_ids)
        if entry["source"] not in witnesses:
            raise ValueError("Address source has no supporting evidence")
    if not isinstance(document.get("counts"), dict) or not isinstance(document.get("limitations"), list):
        raise ValueError("Address counts and limitations are required")
    if document["counts"].get("entries") != len(entries):
        raise ValueError("Address entry count mismatch")
    return document


def load_supplement(data_dir: Path):
    path = data_dir / "seed/hotel_addresses.json"
    if not path.exists():
        return None
    document = read_bounded(path, MAX_ADDRESS_BYTES)
    hotels = read_bounded(data_dir / "seed/hotels.json", MAX_BASE_HOTELS_BYTES)
    return validate_document(document, hotels)


def projected_hotel(row: dict, entry: dict, *, at: datetime | None = None) -> dict | None:
    """Only generic source addresses or a recognisable previous projection may move."""
    at = at or datetime.now(UTC)
    if (row.get("id") != entry["hotel_id"] or any(row.get(key) != entry[key] for key in ("lat", "lon"))
            or _timestamp(entry["valid_until"]) <= at):
        return None
    tags = row.get("tags", {})
    if not isinstance(tags, dict):
        return None
    previous = tags.get("address_evidence", {})
    owned = (isinstance(previous, dict) and previous.get("artifact") == ARTIFACT
             and previous.get("method") == METHOD and previous.get("hotel_id") == entry["hotel_id"]
             and previous.get("binding_sha256") == digest(binding(entry))
             and previous.get("projected_address") == row.get("address"))
    if row.get("address") != entry["base_address"] and not owned:
        return None  # Includes a manual edit after a previous projection.
    result = copy.deepcopy(row)
    if generic_address(entry["base_address"]) or owned:
        result["address"] = entry["address"]
    # Existing specific source addresses remain byte-for-byte unchanged.
    compact = {"artifact": ARTIFACT, "hotel_id": entry["hotel_id"], "method": METHOD,
               "source": entry["source"], "entry_sha256": digest(entry), "binding_sha256": digest(binding(entry)),
               "projected_address": result.get("address"), "district_id": entry["district"],
               "building_id": next((ev["inputs"]["building_id"] for ev in entry["evidence"]
                                    if ev.get("inputs", {}).get("building_id")), None)}
    result.setdefault("tags", {}).update(address_precision=entry["precision"],
                                          address_district=entry["district_display_name"], address_evidence=compact)
    return result if result != row else None


def apply_address_supplement(data_dir: Path, db_url: str, *, connection_factory=None):
    """Do all file validation and projection work outside the short write transaction."""
    document = load_supplement(data_dir)
    if document is None:
        return {"updated": 0, "conflicted": 0, "skipped": 0, "absent": True}
    entries = {entry["hotel_id"]: entry for entry in document["entries"]}
    sources = [Source.model_validate(row).model_dump(mode="json") for row in document["sources"]]
    factory = connection_factory or psycopg.connect
    with factory(db_url, autocommit=True) as conn:
        rows = list(conn.execute("SELECT id,payload FROM hotels WHERE id=ANY(%s) ORDER BY id", (sorted(entries),)))
        changes = []
        for hotel_id, row in rows:
            expected = copy.deepcopy(row)
            projected = projected_hotel(expected, entries[hotel_id])
            if projected is not None:
                changes.append((hotel_id, expected, projected))
        updated = conflicted = 0
        with conn.transaction():
            for source in sorted(sources, key=lambda item: item["id"]):
                conn.execute("INSERT INTO sources(id,payload) VALUES(%s,%s) ON CONFLICT(id) DO NOTHING",
                             (source["id"], Jsonb(source)))
            for hotel_id, expected, projected in sorted(changes):
                cursor = conn.execute("UPDATE hotels SET payload=%s,updated_at=now() WHERE id=%s AND payload=%s",
                                      (Jsonb(projected), hotel_id, Jsonb(expected)))
                updated += cursor.rowcount
                conflicted += cursor.rowcount == 0
    return {"updated": updated, "conflicted": conflicted, "skipped": len(entries) - len(changes), "absent": False}
