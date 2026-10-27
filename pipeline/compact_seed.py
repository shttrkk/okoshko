"""Losslessly gzip existing seed GeoJSON; never reserialize or recompute claims.

Run ``python -m pipeline.compact_seed`` on a validated seed. The manifest keeps
physical gzip hashes and sizes alongside the original logical GeoJSON hashes.
The command is idempotent and removes only the unused layer CSV duplicates.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from geo.io import read_bytes
from pipeline.common import ROOT, SEED, dump, output_metadata, read, sha


def run():
    manifest = read(SEED / "manifest.json")
    outputs = manifest["outputs"]
    # A fresh core export may rebuild outputs without this supplemental input.
    # Validate and retain the published snapshot; never fetch it during compaction.
    district_snapshot = SEED / "admin_districts.geojson.gz"
    district_key = str(district_snapshot.relative_to(ROOT))
    if district_snapshot.exists() and district_key not in outputs:
        from pipeline.enrich_addresses import read_snapshot
        read_snapshot(district_snapshot)
        outputs[district_key] = output_metadata(district_snapshot)
    logical_paths = sorted(set(SEED.glob("*.geojson")) | {p.with_suffix("") for p in SEED.glob("*.geojson.gz")})
    report = []
    for logical in logical_paths:
        physical = Path(str(logical) + ".gz")
        logical_name = str(logical.relative_to(ROOT))
        physical_name = str(physical.relative_to(ROOT))
        before = logical if logical.is_file() else physical
        previous = outputs[str(before.relative_to(ROOT))]
        assert sha(before) == previous["sha256"] and before.stat().st_size == previous["bytes"], before
        original = read_bytes(logical)
        original_hash = hashlib.sha256(original).hexdigest()
        expected = previous.get("uncompressed_sha256", previous["sha256"])
        assert original_hash == expected, logical
        before_size = before.stat().st_size
        temp = physical.with_suffix(physical.suffix + ".tmp")
        with temp.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as stream:
                stream.write(original)
        assert gzip.decompress(temp.read_bytes()) == original, logical
        assert temp.stat().st_size < 50_000_000, temp
        temp.replace(physical)
        logical.unlink(missing_ok=True)
        outputs.pop(logical_name, None)
        outputs[physical_name] = output_metadata(physical)
        report.append({"path": physical_name, "before_bytes": before_size, "after_bytes": physical.stat().st_size,
                       "uncompressed_bytes": len(original), "uncompressed_sha256": original_hash})
    for name in ("buildings", "osm_features"):
        path = SEED / (name + ".csv.gz")
        key = str(path.relative_to(ROOT))
        if path.is_file():
            previous = outputs[key]
            assert sha(path) == previous["sha256"] and path.stat().st_size == previous["bytes"], path
            report.append({"removed_duplicate": key, "before_bytes": path.stat().st_size, "after_bytes": 0})
            path.unlink()
        outputs.pop(key, None)
    dump(SEED / "manifest.json", manifest)
    from pipeline.enrich_addresses import run as enrich_addresses
    enrich_addresses(SEED.parent)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    run()
