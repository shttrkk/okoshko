"""Validate and inventory isolated TZ artifacts, without loading runtime facts."""
from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from backend.contracts import CharacteristicSpec, Claim, Source
from backend.contracts.validation import validate_value
from pipeline.common import CONFIG, ROOT, SEED, dump, output_metadata, sha, utcnow


def run():
    hotels = {h["id"] for h in json.loads((SEED / "hotels.json").read_text())}
    claims, flags, stages, sources, checked_hashes = [], defaultdict(lambda: defaultdict(list)), {}, {}, {}
    for path in sorted(SEED.glob("tz_*_manifest.json")):
        stages[path.name] = json.loads(path.read_text())
    for path in sorted(SEED.glob("tz_*_sources.json")):
        for record in json.loads(path.read_text()):
            source = Source.model_validate(record)
            previous = sources.get(source.id)
            if previous and (source.license != previous["license"] or source.url != previous["url"]):
                raise ValueError(f"inconsistent source identity: {source.id}")
            sources[source.id] = source.model_dump(mode="json")
    for path in sorted(SEED.glob("tz_*_observations.json.gz")):
        with gzip.open(path, "rt") as stream:
            for row in json.load(stream)["observations"]:
                if row["hotel_id"] not in hotels:
                    raise ValueError("observation hotel outside portfolio")
                value = row.get("value") or {}
                flags[row["kind"]][row["hotel_id"]].append(value.get("available"))
    specs = {}
    for directory in [ROOT / "data/characteristics", ROOT / "data/characteristics/tz_draft"]:
        for path in directory.glob("*.yaml"):
            spec = CharacteristicSpec.model_validate(yaml.safe_load(path.read_text()))
            specs[spec.id] = spec
    for path in sorted(SEED.glob("tz_*_claims.jsonl.gz")):
        with gzip.open(path, "rt") as stream:
            for line in stream:
                if not line.strip():
                    continue
                claim = Claim.model_validate_json(line)
                if claim.hotel_id not in hotels or claim.synthetic:
                    raise ValueError("unreal/out-of-portfolio TZ claim")
                if claim.characteristic_id in specs:
                    spec = specs[claim.characteristic_id]
                    validate_value(claim.value, spec.value_schema)
                    if (claim.valid_until-claim.observed_at).total_seconds() > spec.ttl_days*86400:
                        raise ValueError("claim exceeds specification TTL")
                if claim.characteristic_id == "construction_nearby":
                    value = claim.value
                    eligible = [row for row in value.get("candidates", []) if row.get("eligible_current")]
                    if value.get("current_eligible_count") != len(eligible) or value.get("available") is False:
                        raise ValueError("construction archive/unknown semantics violated")
                    if value.get("available") is True and not eligible:
                        raise ValueError("construction yes lacks current evidence")
                    for row in eligible:
                        if not row["temporal"]["eligible_temporally"] or not row["temporal"]["verification_keys"] or not any(side["eligible_geometry"] for side in row["facades"]):
                            raise ValueError("construction yes lacks dated verification and facade proximity")
                for evidence in claim.evidence:
                    for source_id in evidence.source_ids+[claim.source]:
                        if source_id not in sources:
                            raise ValueError(f"missing Source: {source_id}")
                    if evidence.artifact:
                        artifact = Path(evidence.artifact.removeprefix("/"))
                        if not evidence.artifact.startswith("/data/") or ".." in artifact.parts or not (ROOT / artifact).is_file():
                            raise ValueError("evidence artifact missing/unsafe")
                    for reference, expected in evidence.inputs.get("input_sha256", {}).items():
                        if reference not in checked_hashes and reference.startswith("/data/") and ".." not in Path(reference).parts:
                            checked_hashes[reference] = sha(ROOT / reference.removeprefix("/"))
                        if checked_hashes.get(reference) != expected:
                            raise ValueError(f"input checksum mismatch: {reference}")
                claims.append(claim)
    ids = [c.id for c in claims]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate Claim ID")
    fisht_path = SEED / "tz_fisht_manifest.json"
    if fisht_path.exists() and not json.loads(fisht_path.read_text()).get("confirmed_height"):
        if any(c.characteristic_id == "landmark_view" and c.value.get("landmark_id") == "fisht" for c in claims):
            raise ValueError("Fisht LOS must remain disabled without confirmed height")
    coverage = {}
    for kind, values in flags.items():
        if set(values) != hotels:
            raise ValueError(f"incomplete portfolio processing: {kind}")
        counts = Counter("yes" if True in state else "unknown" if None in state else "no" for state in values.values())
        coverage[kind] = {key: counts[key] for key in ("yes", "no", "unknown")}
    paths = sorted(p for p in SEED.glob("tz_*") if p.is_file() and p.name != "tz_manifest.json")
    for path in paths:
        if path.stat().st_size >= 50_000_000:
            raise ValueError(f"oversized artifact: {path.name}")
        if path.suffix == ".gz" and path.read_bytes()[4:8] != b"\0\0\0\0":
            raise ValueError(f"non-reproducible gzip mtime: {path.name}")
    manifest = {"stage": "tz_stage1@1", "generated_at": utcnow(), "runtime_loaded": False, "portfolio_hotels": len(hotels),
                "osm_snapshot": CONFIG["osm"]["snapshot"], "coverage": coverage, "claims": len(claims),
                "sources": list(sources.values()), "stages": sorted(stages),
                "outputs": {p.name: output_metadata(p) for p in paths}, "total_artifact_bytes": sum(p.stat().st_size for p in paths),
                "known_answer_fixture_file": "tests/test_tz_calculators.py", "registry_safety_fixture_file": "tests/test_tz_registry.py",
                "coverage_semantics": "Per hotel: any true scoped value -> yes; otherwise missing/partial -> unknown; otherwise all known false -> no. This is model/data coverage, not field-verified accuracy or guest-search integration.",
                "limitations": ["New ledgers/specs are prepared separately; no runtime seed loader, guest parser or ranker wiring", "Climate: 2 representative zonal daily samples -> 3000 derived hotel-month records; repeated hotels do not increase confidence", "GTFS, live service times/night flights, events feed and OpenCellID are unavailable/not used", "No OTA/Ostrovok/Yandex/Google/2GIS/Mapillary data acquisition in this stage"]}
    # A compact digest makes baseline inventory reviewable without replicating
    # the original seeds. Their actual bytes remain untouched.
    baseline_path = Path("/tmp/okno-tz-baseline-sha.json")
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text())
        changed = [p for p, expected in baseline.items() if not (ROOT / p).is_file() or sha(ROOT / p) != expected]
        if changed:
            raise ValueError(f"original files changed: {changed}")
        manifest["baseline_integrity"] = {"files": len(baseline), "changed": 0, "inventory_sha256": hashlib.sha256(json.dumps(baseline, sort_keys=True).encode()).hexdigest()}
    dump(SEED / "tz_manifest.json", manifest)
    print(json.dumps({k: manifest[k] for k in ("claims", "total_artifact_bytes", "coverage")}, ensure_ascii=False, sort_keys=True), flush=True)
    return manifest


if __name__ == "__main__":
    run()
