"""Reproduce every geo claim/artifact from downloaded seed layers and numeric DSM.

python -m pipeline.compute_views --data-dir data --output-dir data/geo
"""
from __future__ import annotations

import argparse
import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

from backend.contracts.models import Claim
from geo.region import Region
from geo.service import compute_with_region


def compute_all(data_dir: Path, output_dir: Path, *, limit=None, hotel_ids=None):
    seed = data_dir/"seed" if (data_dir/"seed").exists() else data_dir
    hotels = json.loads((seed/"hotels.json").read_text())
    if hotel_ids:
        hotels = [hotel for hotel in hotels if hotel["id"] in hotel_ids]
    if limit:
        hotels = hotels[:limit]
    region = Region(data_dir, terrain_path=output_dir/"terrain_30m.npz")
    timestamp = datetime.now(UTC).replace(microsecond=0)
    summary = {"hotels": 0, "claims": 0, "unknown": 0, "computed_at": timestamp.isoformat(), "samples": []}
    with (output_dir/"geo_claims.jsonl.gz").open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
            for hotel in hotels:
                result = compute_with_region(hotel, region, output_dir, computed_at=timestamp)
                for claim in result["claims"]:
                    Claim.model_validate(claim)
                    stream.write((json.dumps(claim, ensure_ascii=False, sort_keys=True, allow_nan=False)+"\n").encode())
                summary["hotels"] += 1
                summary["claims"] += len(result["claims"])
                summary["unknown"] += bool(result["geo"]["unknown_characteristics"])
                if len(summary["samples"]) < 5:
                    summary["samples"].append({"hotel_id": hotel["id"], "name": hotel["name"], "facades": len(result["geo"]["facades"]),
                                               "claims": len(result["claims"]), "unknown": result["geo"]["unknown_characteristics"]})
                print(json.dumps({"hotel": hotel["id"], "done": summary["hotels"], "claims": len(result["claims"]) }), flush=True)
    summary["validation"] = "Contract and artifact invariants; no photo/field ground-truth labels, no accuracy percentage claimed"
    (output_dir/"computation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2)+"\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/geo"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--hotel-id", action="append")
    args = parser.parse_args()
    compute_all(args.data_dir, args.output_dir, limit=args.limit, hotel_ids=args.hotel_id)
