"""Export final Hotel portfolio's existing geo claims; no recomputation/seed writes."""
from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path

from backend.contracts.models import Claim


def export_existing(data_dir, geo_dir, *, prune=False):
    seed = data_dir/"seed" if (data_dir/"seed").exists() else data_dir
    hotels = json.loads((seed/"hotels.json").read_text())
    ids = {hotel["id"] for hotel in hotels}
    removed = []
    if prune:
        for path in geo_dir.iterdir():
            match = re.fullmatch(r"(osm-node-\d+|osm-way-\d+|overture-[0-9a-f-]+)(?:\.los)?\.(json|npz)", path.name)
            if match and match.group(1) not in ids:
                removed.append(path.name)
                path.unlink()
    summary = {"hotels": 0, "claims": 0, "unknown": 0, "samples": [], "removed_orphans": removed,
               "validation": "Contract/artifact invariants; no photo/field ground-truth labels or real-world accuracy percentage"}
    seen = set()
    with (geo_dir/"geo_claims.jsonl.gz").open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
            for hotel in hotels:
                data = json.loads((geo_dir/(hotel["id"]+".json")).read_text())
                for record in data["claims"]:
                    claim = Claim.model_validate(record)
                    assert claim.hotel_id == hotel["id"] and not claim.synthetic
                    assert claim.id not in seen
                    seen.add(claim.id)
                    stream.write((json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False)+"\n").encode())
                summary["hotels"] += 1
                summary["claims"] += len(data["claims"])
                summary["unknown"] += bool(data["unknown_characteristics"])
                if len(summary["samples"]) < 5:
                    summary["samples"].append({"hotel_id": hotel["id"], "name": hotel["name"], "facades": len(data["facades"]),
                                               "claims": len(data["claims"]), "unknown": data["unknown_characteristics"]})
    (geo_dir/"computation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2)+"\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--geo-dir", type=Path, default=Path("data/geo"))
    parser.add_argument("--prune", action="store_true")
    args = parser.parse_args()
    print(json.dumps(export_existing(args.data_dir, args.geo_dir, prune=args.prune), ensure_ascii=False))
