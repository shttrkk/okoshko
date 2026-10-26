"""Re-issue shipped claims whose validity differs from the current TTL policy.

Claims are immutable. A corrected validity window is a new claim that names the
previous record in ``supersedes_ids``; the old record is never edited in place.
Runs offline over versioned artifacts only:

    python -m pipeline.retime_claims [--data-dir data] [--check]

Policy: open-data claims live for the characteristic ``ttl_days``
(data/characteristics/*.yaml); geometric claims for ``geo.service.geo_ttl_days``.
A fresh pipeline run applies the same policy directly.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from backend.contracts.models import Claim
from geo.service import _digest, _json, geo_ttl_days
from pipeline.common import dump
from pipeline.export_seed import ttl_days

GEO_DIGEST_KEYS = ("hotel_id", "characteristic_id", "spec_version", "scope", "value", "source", "method",
                   "confidence", "observed_at", "valid_until", "synthetic")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_like(template: str, value: datetime) -> str:
    text = value.isoformat()
    return text.replace("+00:00", "Z") if template.endswith("Z") else text


def _days(claim: dict) -> float:
    return (_parse(claim["valid_until"]) - _parse(claim["observed_at"])).total_seconds() / 86400


def _supersede(claim: dict, new_id: str, valid_until: str, evidence_id: str) -> dict:
    result = json.loads(json.dumps(claim))
    result["id"] = new_id
    result["valid_until"] = valid_until
    result["supersedes_ids"] = sorted({*claim.get("supersedes_ids", []), claim["id"]})
    for evidence in result["evidence"]:
        evidence["id"] = evidence_id
    Claim.model_validate(result)
    return result


def retime_open(claim: dict) -> dict | None:
    days = ttl_days(claim["characteristic_id"])
    if abs(_days(claim) - days) < 1e-6:
        return None
    valid_until = _format_like(claim["valid_until"], _parse(claim["observed_at"]) + timedelta(days=days))
    new_id = "open-" + str(uuid5(NAMESPACE_URL, json.dumps([claim["id"], "ttl_days", days])))
    return _supersede(claim, new_id, valid_until, new_id + "-evidence")


def retime_geo(claim: dict) -> dict | None:
    days = geo_ttl_days(claim["characteristic_id"])
    if abs(_days(claim) - days) < 1e-6:
        return None
    valid_until = _format_like(claim["valid_until"], _parse(claim["observed_at"]) + timedelta(days=days))
    record = {key: claim[key] for key in GEO_DIGEST_KEYS}
    record["valid_until"] = valid_until
    new_id = "geo-" + _digest(record)
    return _supersede(claim, new_id, valid_until, "ev-" + new_id)


def _read_jsonl_gz(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _write_jsonl_gz(path: Path, rows: list[dict], dumps) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as stream:
        for row in rows:
            stream.write((dumps(row) + "\n").encode())
    temporary.replace(path)


def _open_dumps(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _geo_dumps(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)


def retime(data_dir: Path, *, apply: bool = True) -> dict:
    report = {"open": {}, "geo": {}, "artifacts": 0}
    open_path = data_dir / "seed" / "open_claims.jsonl.gz"
    rows = _read_jsonl_gz(open_path)
    changed = 0
    for index, claim in enumerate(rows):
        replacement = retime_open(claim)
        if replacement:
            rows[index] = replacement
            changed += 1
            report["open"][claim["characteristic_id"]] = report["open"].get(claim["characteristic_id"], 0) + 1
    if changed and apply:
        _write_jsonl_gz(open_path, rows, _open_dumps)
    manifest_path = data_dir / "seed" / "manifest.json"
    if apply and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        entry = manifest.get("outputs", {}).get("data/seed/open_claims.jsonl.gz")
        digest = hashlib.sha256(open_path.read_bytes()).hexdigest()
        if entry is not None and entry.get("sha256") != digest:
            entry["sha256"], entry["bytes"] = digest, open_path.stat().st_size
            dump(manifest_path, manifest)

    geo_path = data_dir / "geo" / "geo_claims.jsonl.gz"
    rows = _read_jsonl_gz(geo_path)
    replaced: dict[str, dict] = {}
    for index, claim in enumerate(rows):
        expected = "geo-" + _digest({key: claim[key] for key in GEO_DIGEST_KEYS})
        if claim["id"] != expected:
            raise ValueError(f"{claim['id']}: ledger identity does not match the geo digest; refusing to re-issue")
        replacement = retime_geo(claim)
        if replacement:
            replaced[claim["id"]] = replacement
            rows[index] = replacement
            report["geo"][claim["characteristic_id"]] = report["geo"].get(claim["characteristic_id"], 0) + 1
    if replaced and apply:
        _write_jsonl_gz(geo_path, rows, _geo_dumps)
        hotels = {row["hotel_id"] for row in replaced.values()}
        for hotel_id in sorted(hotels):
            path = data_dir / "geo" / (hotel_id + ".json")
            if not path.is_file():
                continue
            document = json.loads(path.read_text())
            document["claims"] = [replaced.get(claim["id"], claim) for claim in document.get("claims", [])]
            document["claim_ids"] = [replaced[cid]["id"] if cid in replaced else cid for cid in document.get("claim_ids", [])]
            path.write_text(_json(document) + "\n")
            report["artifacts"] += 1
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--check", action="store_true", help="Report claims outside the TTL policy without writing")
    args = parser.parse_args(argv)
    report = retime(args.data_dir, apply=not args.check)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if args.check and (report["open"] or report["geo"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
