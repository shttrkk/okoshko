"""Validate and inventory isolated TZ artifacts, without loading runtime facts."""
from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from backend.catalogue import searchable
from backend.contracts import CharacteristicSpec, Claim, Source
from backend.contracts.validation import validate_value
from pipeline.common import CONFIG, ROOT, SEED, dump, output_metadata, sha, utcnow


def run():
    hotel_rows = json.loads((SEED / 'hotels.json').read_text())
    hotels = {h['id'] for h in hotel_rows}
    guest_hotels = {h['id'] for h in hotel_rows if searchable(h, ROOT / 'data')}
    claims, flags, stages, sources, checked_hashes = [], defaultdict(lambda: defaultdict(list)), {}, {}, {}
    field_flags, target_flags = defaultdict(lambda: defaultdict(list)), defaultdict(lambda: defaultdict(list))
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
                if row['kind'] == 'landmark_view' and value.get('landmark_id'):
                    target_flags[value['landmark_id']][row['hotel_id']].append(value.get('available'))
                for field in {'building_history': ['history_information', 'historic_designation', 'explicit_building_date', 'architecture'],
                              'family_nearby': ['walk_playground.available', 'walk_hospital.available', 'walk_children_hospital.available', 'walk_promenade.available'],
                              'car_access': ['mapped_road_access', 'walk_parking.available', 'walk_ev.available'],
                              'climate': ['sunny_days', 'rainy_days', 'wet_month_proxy'],
                              'location_access': ['in_central_sochi', 'center_route.available', 'station_route.available', 'bus_stop_route.available']}.get(row['kind'], []):
                    field_value = value
                    for part in field.split('.'):
                        field_value = field_value.get(part) if isinstance(field_value, dict) else None
                    field_flags[row['kind']+'.'+field][row['hotel_id']].append(field_value)
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
                if claim.characteristic_id == "landmark_view" and claim.scope.landmark_id != claim.value.get("landmark_id"):
                    raise ValueError("landmark target scope mismatch")
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
        if any(c.characteristic_id == "landmark_view" and c.value.get("landmark_id") == "fisht" and
               (c.value.get("available") is not None or c.value.get("record_type") != "target_metadata" or c.value.get("height_available") is not False) for c in claims):
            raise ValueError("Fisht LOS must remain disabled without confirmed height")
    coverage = {}
    def summarize(values, population):
        counts = Counter('yes' if any(v is True for v in values.get(hotel, [])) else
                         'known' if any(v is not None and not isinstance(v, bool) for v in values.get(hotel, [])) else
                         'unknown' if not values.get(hotel) or any(v is None for v in values[hotel]) else 'no' for hotel in population)
        return {key: counts[key] for key in ('yes', 'no', 'known', 'unknown')}
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
    manifest = {"stage": "tz_prepared@2", "generated_at": utcnow(), "pipeline_writes_database": False, "runtime_loader": "backend.tz_seed.load_tz_seed", "portfolio_hotels": len(hotels),
                "osm_snapshot": CONFIG["osm"]["snapshot"], "coverage": coverage, "claims": len(claims),
                "guest_hotels": len(guest_hotels), "guest_coverage": {kind: summarize(values, guest_hotels) for kind, values in flags.items()},
                "field_coverage": {field: {'source_250': summarize(values, hotels), 'guest_247': summarize(values, guest_hotels)} for field, values in field_flags.items()},
                "landmark_target_coverage": {target: {'source_250': summarize(values, hotels), 'guest_247': summarize(values, guest_hotels)} for target, values in target_flags.items()},
                "sources": list(sources.values()), "stages": sorted(stages),
                "outputs": {p.name: output_metadata(p) for p in paths}, "total_artifact_bytes": sum(p.stat().st_size for p in paths),
                "known_answer_fixture_file": "tests/test_tz_calculators.py", "registry_safety_fixture_file": "tests/test_tz_registry.py",
                "coverage_semantics": "Per hotel: any true scoped value -> yes; otherwise missing/partial -> unknown; otherwise all known false -> no. This is model/data coverage, not field-verified accuracy or guest-search integration.",
                "limitations": ["Prepared observations are loaded by backend.tz_seed; coverage describes data availability, not independently verified accuracy", "Climate: 2 representative zonal daily samples -> 3000 derived hotel-month records; repeated hotels do not increase confidence", "GTFS, live service times/night flights, events feed and OpenCellID are unavailable/not used", "No OTA/Ostrovok/Yandex/Google/2GIS/Mapillary data acquisition in this stage"]}
    # A compact digest makes baseline inventory reviewable without replicating
    # the original seeds. Their actual bytes remain untouched.
    baseline_path = SEED / "tz_baseline_integrity.json"
    if baseline_path.exists():
        baseline_record = json.loads(baseline_path.read_text())
        baseline = baseline_record["files"]
        changed = [p for p, expected in baseline.items() if not (ROOT / p).is_file() or sha(ROOT / p) != expected]
        if changed:
            raise ValueError(f"original files changed: {changed}")
        manifest["baseline_integrity"] = {"baseline_commit": baseline_record["baseline_commit"], "files": len(baseline), "changed": 0, "inventory_sha256": hashlib.sha256(json.dumps(baseline, sort_keys=True).encode()).hexdigest()}
    dump(SEED / "tz_manifest.json", manifest)
    print(json.dumps({k: manifest[k] for k in ("claims", "total_artifact_bytes", "coverage")}, ensure_ascii=False, sort_keys=True), flush=True)
    return manifest


if __name__ == "__main__":
    run()
