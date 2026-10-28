"""Validate prepared regional observations before an insert-only database import."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
import yaml
from psycopg.types.json import Jsonb

from backend.contracts import CharacteristicSpec, Claim, Source
from backend.contracts.validation import validate_value

_NAME = re.compile(r"tz_[a-z0-9_]+\.(?:json|jsonl\.gz|json\.gz|geojson\.gz)\Z")
_STAGES = {"environment", "routes", "climate", "history", "landmarks", "supplements", "location", "metadata"}
_MAX_FILE = 50_000_000


@dataclass(frozen=True)
class PreparedSeed:
    sources: tuple[Source, ...] = ()
    claims: tuple[Claim, ...] = ()


def _local_file(data_dir: Path, reference: str) -> Path:
    """Only immutable shipped data, never runtime, raw downloads or external paths."""
    relative = Path(reference.removeprefix("/data/"))
    if (not reference.startswith("/data/") or relative.is_absolute() or ".." in relative.parts
            or not relative.parts or relative.parts[0] not in {"seed", "geo", "characteristics", "landmarks"}):
        raise ValueError(f"Unsafe prepared-data reference: {reference}")
    current = data_dir
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Symlink in prepared-data reference: {reference}")
    if not current.is_file() or not current.resolve().is_relative_to(data_dir.resolve()):
        raise ValueError(f"Missing prepared-data reference: {reference}")
    return current


def _content(path: Path) -> bytes:
    """Shipped bytes. Plain JSON is committed with LF; a CRLF checkout (Git for Windows
    autocrlf) is compared as the committed text, compressed files are never touched."""
    data = path.read_bytes()
    if path.suffix == ".json" and b"\r\n" in data:
        data = data.replace(b"\r\n", b"\n")
    return data


def _sha(path: Path) -> str:
    return hashlib.sha256(_content(path)).hexdigest()


def prepare_seed(data_dir: Path, known_hotels: set[str]) -> PreparedSeed:
    """No writes: a corrupt bundle must fail before any source or claim is imported."""
    manifest_path = data_dir / "seed/tz_manifest.json"
    if not manifest_path.exists():
        return PreparedSeed()
    if manifest_path.is_symlink() or manifest_path.stat().st_size > 2_000_000:
        raise ValueError("Unsafe prepared-data manifest")
    manifest = json.loads(manifest_path.read_text())
    outputs = manifest.get("outputs", {})
    if not isinstance(outputs, dict) or len(outputs) > 200:
        raise ValueError("Invalid prepared-data inventory")
    hashes: dict[str, str] = {}

    def check(reference: str, expected: str) -> None:
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Invalid prepared-data checksum")
        if reference not in hashes:
            hashes[reference] = _sha(_local_file(data_dir, reference))
        if hashes[reference] != expected:
            raise ValueError(f"Prepared-data checksum mismatch: {reference}")

    for name, metadata in outputs.items():
        if not _NAME.fullmatch(name):
            raise ValueError("Unsafe prepared-data filename")
        path = _local_file(data_dir, f"/data/seed/{name}")
        if (not 0 <= metadata.get("bytes", -1) <= _MAX_FILE or path.stat().st_size > 2 * _MAX_FILE
                or len(_content(path)) != metadata["bytes"]):
            raise ValueError(f"Prepared-data size mismatch: {name}")
        check(f"/data/seed/{name}", metadata.get("sha256"))

    sources: dict[str, Source] = {}
    for record in manifest.get("sources", []):
        source = Source.model_validate(record)
        if not source.url or not source.license or not source.attribution:
            raise ValueError("Prepared source requires attribution and license")
        previous = sources.get(source.id)
        if previous and (previous.url != source.url or previous.license != source.license):
            raise ValueError("Conflicting prepared source identity")
        sources[source.id] = source
    # Every per-stage source file is also checked against the public inventory.
    for name in outputs:
        if name.endswith("_sources.json"):
            for row in json.loads((data_dir / "seed" / name).read_text()):
                source = Source.model_validate(row)
                identity = sources.get(source.id)
                if identity is None or (identity.url, identity.license) != (source.url, source.license):
                    raise ValueError("Prepared stage source differs from inventory")

    specs = {spec.id: spec for path in (data_dir / "characteristics").glob("*.yaml")
             if (spec := CharacteristicSpec.model_validate(yaml.safe_load(path.read_text())))}
    claims: dict[str, Claim] = {}
    for name in sorted(outputs):
        if not name.endswith("_claims.jsonl.gz"):
            continue
        stage = name.removeprefix("tz_").removesuffix("_claims.jsonl.gz")
        if stage not in _STAGES:
            raise ValueError(f"Unknown prepared claim stage: {stage}")
        expanded = 0
        with gzip.open(data_dir / "seed" / name, "rt", encoding="utf-8") as stream:
            for line in stream:
                expanded += len(line)
                if len(line) > 2_000_000 or expanded > 150_000_000 or len(claims) >= 100_000:
                    raise ValueError("Prepared claim ledger exceeds size limit")
                if not line.strip():
                    continue
                claim = Claim.model_validate_json(line)
                spec = specs.get(claim.characteristic_id)
                if claim.id in claims or claim.hotel_id not in known_hotels or claim.synthetic or spec is None:
                    raise ValueError("Duplicate, synthetic or unknown prepared claim")
                if claim.source not in sources or not claim.method.startswith("tz_"):
                    raise ValueError("Prepared claim lacks a registered source or method")
                validate_value(claim.value, spec.value_schema)
                if (claim.valid_until - claim.observed_at).total_seconds() > spec.ttl_days * 86400:
                    raise ValueError("Prepared claim exceeds characteristic TTL")
                for evidence in claim.evidence:
                    if not set(evidence.source_ids) <= sources.keys() or evidence.kind in {"simulation", "mock"}:
                        raise ValueError("Prepared evidence has unknown or synthetic sources")
                    if evidence.artifact:
                        _local_file(data_dir, evidence.artifact)
                    for reference, expected in evidence.inputs.get("input_sha256", {}).items():
                        check(reference, expected)
                claims[claim.id] = claim
    if len(claims) != manifest.get("claims"):
        raise ValueError("Prepared claim count differs from inventory")
    return PreparedSeed(tuple(sources.values()), tuple(claims.values()))


def insert_prepared(conn, bundle: PreparedSeed) -> dict[str, int]:
    """Preserve learned source priors and all existing partner/guest actions."""
    with conn.cursor() as cursor:
        cursor.executemany("INSERT INTO sources(id,payload) VALUES(%s,%s) ON CONFLICT(id) DO NOTHING",
                           [(source.id, Jsonb(source.model_dump(mode="json"))) for source in bundle.sources])
    conn.execute("CREATE TEMP TABLE tz_seed_import(id text PRIMARY KEY,payload jsonb NOT NULL) ON COMMIT DROP")
    with conn.cursor().copy("COPY tz_seed_import(id,payload) FROM STDIN") as copy:
        for claim in bundle.claims:
            copy.write_row((claim.id, claim.model_dump_json()))
    inserted = conn.execute("INSERT INTO claims(id,payload) SELECT id,payload FROM tz_seed_import "
                            "ON CONFLICT(id) DO NOTHING").rowcount
    conn.execute("DROP TABLE tz_seed_import")
    return {"validated_claims": len(bundle.claims), "inserted_claims": inserted, "sources": len(bundle.sources)}


def load_tz_seed(data_dir: Path, db_url: str) -> dict[str, int]:
    if not (data_dir / "seed/tz_manifest.json").exists():
        return {"validated_claims": 0, "inserted_claims": 0, "sources": 0}
    with psycopg.connect(db_url, autocommit=True) as conn:
        hotels = {row[0] for row in conn.execute("SELECT id FROM hotels")}
    bundle = prepare_seed(data_dir, hotels)
    with psycopg.connect(db_url) as conn:
        return insert_prepared(conn, bundle)
