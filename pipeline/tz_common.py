"""Separate stage-one Claim ledger and transparent unknown coverage."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

import yaml

from backend.contracts import Claim, Source
from pipeline.common import SEED, dump, output_metadata, utcnow


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def stable_id(value):
    return hashlib.sha256(json_text(value).encode()).hexdigest()[:32]


def baseline_sources():
    with gzip.open(SEED / "sources.csv.gz", "rt") as stream:
        return {r["id"]: json.loads(r["payload"]) for r in csv.DictReader(stream)}


def parameters(kind):
    spec = yaml.safe_load(spec_path(kind).read_text())
    return {k: v for k, v in spec["methods"][0]["params"].items() if k != "kind"}


def spec_path(kind):
    root = SEED.parent / "characteristics"
    active = root / (kind+".yaml")
    return active if active.exists() else root / "tz_draft" / active.name


def spec_input(kind):
    path = spec_path(kind)
    return "/data/"+str(path.relative_to(SEED.parent)), hashlib.sha256(path.read_bytes()).hexdigest()


def write_stage(name, observations, sources, *, inputs, limitations=()):
    """Unknown values stay observations; there is no fabricated unknown Claim."""
    generated = datetime.now(UTC)
    evidence_path = SEED / f"tz_{name}_observations.json.gz"
    content = {"stage": name, "generated_at": generated.isoformat(), "observations": observations,
               "limitations": list(limitations)}
    _gzip(evidence_path, json_text(content).encode())
    claims, coverage, hotel_flags = [], defaultdict(Counter), defaultdict(lambda: defaultdict(list))
    for row in observations:
        value = row.get("value")
        available = value.get("available") if isinstance(value, dict) else None
        coverage[row["kind"]]["yes" if available is True else "no" if available is False else "unknown"] += 1
        hotel_flags[row["kind"]][row["hotel_id"]].append(available)
        if value is None or row.get("emit_claim", True) is False:
            continue
        # A partial map observation can carry counts/tags with available=null;
        # it must not be counted as a satisfied yes/no condition.
        observed = datetime.fromisoformat(row.get("observed_at", generated.isoformat()).replace("Z", "+00:00"))
        scope = row.get("scope", {"type": "hotel"})
        data = {"hotel_id": row["hotel_id"], "characteristic_id": row["kind"], "scope": scope,
                "value": value, "source": row.get("source", "tz_osm_geofabrik"), "method": row.get("method", f"tz_{name}@1"),
                "confidence": row.get("confidence", .55), "observed_at": observed.isoformat(),
                "valid_until": (observed+timedelta(days=row.get("ttl_days", 90))).isoformat(), "synthetic": False,
                "evidence": [{"id": stable_id([row["hotel_id"], row["kind"], scope, inputs]), "kind": row.get("evidence_kind", "calculation"),
                              "label": row.get("label", "Расчёт по сохранённым открытым данным"),
                              "source_ids": row.get("source_ids", [row.get("source", "tz_osm_geofabrik")]),
                              "artifact": "/data/seed/"+evidence_path.name, "inputs": {"input_sha256": {**inputs, **row.get("input_sha256", {})}, "observation_id": row["id"]},
                              "output": {"observation_id": row["id"]}, "limitations": list(limitations)+row.get("limitations", [])}]}
        for reference in row.get("related_artifacts", []):
            data["evidence"].append({"id": stable_id([row["id"], reference]), "kind": "open_data", "label": "Общая зоновая исходная выборка",
                                     "source_ids": row.get("source_ids", [data["source"]]), "artifact": reference,
                                     "inputs": {"input_sha256": {**inputs, **row.get("input_sha256", {})}},
                                     "output": {}, "limitations": ["Представительная точка зоны; это не датчик у отеля"]})
        data["id"] = stable_id(data)
        claims.append(Claim.model_validate(data).model_dump(mode="json"))
    claim_path = SEED / f"tz_{name}_claims.jsonl.gz"
    _gzip(claim_path, ("\n".join(json_text(c) for c in claims)+("\n" if claims else "")).encode())
    sources = [Source.model_validate(s).model_dump(mode="json") for s in sources]
    source_path = SEED / f"tz_{name}_sources.json"
    dump(source_path, sources)
    hotel_coverage = {}
    for kind, hotels in hotel_flags.items():
        counts = Counter("yes" if True in flags else "unknown" if None in flags else "no" for flags in hotels.values())
        hotel_coverage[kind] = {key: counts[key] for key in ("yes", "no", "unknown")}
    manifest = {"stage": name, "generated_at": utcnow(), "claims": len(claims), "observations": len(observations),
                "coverage": {kind: {key: counts[key] for key in ("yes", "no", "unknown")} for kind, counts in coverage.items()},
                "hotel_coverage": hotel_coverage, "coverage_semantics": "yes/no describes the prepared value's declared threshold; unknown includes missing/unmapped inputs; observations may repeat hotels by month/facade/floor",
                "inputs": inputs, "limitations": list(limitations), "runtime_loaded": False,
                "outputs": {p.name: output_metadata(p) for p in (evidence_path, claim_path, source_path)}}
    dump(SEED / f"tz_{name}_manifest.json", manifest)
    print(json_text({"stage": name, "claims": len(claims), "coverage": manifest["coverage"]}), flush=True)
    return manifest


def observation(hotel_id, kind, value, **metadata):
    return {"id": stable_id([hotel_id, kind, metadata.get("scope"), value]), "hotel_id": hotel_id, "kind": kind,
            "value": value, **metadata}


def _gzip(path, content):
    with path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as stream:
            stream.write(content)
