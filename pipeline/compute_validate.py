"""Artifact/contract validation. No claimed photo or field accuracy."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

from backend.contracts.models import Claim


def validate(data_dir, geo_dir):
    seed = data_dir/"seed" if (data_dir/"seed").exists() else data_dir
    hotels = json.loads((seed/"hotels.json").read_text())
    by_id = {hotel["id"]: hotel for hotel in hotels}
    samples, characteristics, counts = [], {}, {}
    inputs = {}
    for path in sorted(seed.glob("*.geojson")) + [seed/"hotels.json", seed/"walk_graph.json.gz"]:
        if path.exists():
            inputs["/data/seed/"+path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    for hotel in hotels:
        name = hotel["id"]
        path = geo_dir/(name+".json")
        assert path.exists(), str(path)
        data = json.loads(path.read_text())
        assert data["hotel_id"] == name
        assert not data["synthetic"], name
        assert len(data["sun_paths"]) in {0, 12}, name
        assert data["calculation_version"]
        for facade in data["facades"]:
            floors = len(facade["sea_width_by_floor"])
            assert len(facade["sea_probability_by_floor"]) == floors
            assert all(0 <= p <= 1 for p in facade["sea_probability_by_floor"])
            assert 0 <= facade["height_coverage"] <= 1
            assert 0 <= facade["nearby_height_coverage"] <= 1
            assert len(facade["sunset_by_month"]) == 12
        with np.load(geo_dir/(name+".npz"), allow_pickle=False) as arrays:
            assert all(array.dtype.kind in "biuf" for array in arrays.values())
        for claim in data["claims"]:
            Claim.model_validate(claim)
            assert claim["hotel_id"] in by_id
            characteristic = claim["characteristic_id"]
            counts[characteristic] = counts.get(characteristic, 0)+1
            characteristics.setdefault(characteristic, set()).add(name)
            for evidence in claim["evidence"]:
                assert evidence["inputs"] and evidence["output"]
                assert (geo_dir/Path(evidence["artifact"]).name).exists()
                for artifact, digest in evidence["inputs"]["input_sha256"].items():
                    if artifact in inputs:
                        assert inputs[artifact] == digest, (name, artifact)
            if characteristic == "sea_view" and claim["value"]["available"]:
                assert claim["value"]["sea_width_deg"] >= 10
                assert claim["scope"]["facade"] == claim["value"]["facade"]
        if len(samples) < 5:
            samples.append({"hotel_id": name, "name": hotel["name"], "center": data["center"],
                            "claims_validated": len(data["claims"]), "facades": len(data["facades"]),
                            "sea_facades": [{"facade": facade["code"], "min_floor": facade["min_floor_sea"],
                                             "min_floor_p80": facade["min_floor_sea_p80"]} for facade in data["facades"] if facade["min_floor_sea"]],
                            "beach_route": bool(data["route"]["features"]), "unknown": data["unknown_characteristics"]})
    for path in geo_dir.iterdir():
        if path.is_file():
            assert path.stat().st_size < 50_000_000, str(path)
    with gzip.open(geo_dir/"geo_claims.jsonl.gz", "rt") as stream:
        exported = [Claim.model_validate_json(line) for line in stream]
    assert len(exported) == sum(counts.values())
    report = {"hotels": len(hotels), "claims": sum(counts.values()), "claims_by_characteristic": counts,
              "coverage_hotels": {key: len(value) for key, value in characteristics.items()}, "samples": samples,
              "validation_type": "contract, provenance, numeric artifacts and internal invariants",
              "ground_truth_labels": 0, "accuracy": None,
              "limitation": "No photo/manual/field labels were supplied. These checks establish reproducibility and schema correctness, not real-world accuracy.",
              "input_sha256": inputs,
              "implementation_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(Path("geo").glob("*.py"))}}
    (geo_dir/"validation.json").write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)+"\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--geo-dir", type=Path, default=Path("data/geo"))
    args = parser.parse_args()
    report = validate(args.data_dir, args.geo_dir)
    print(json.dumps({key: value for key, value in report.items() if key not in {"input_sha256", "implementation_sha256", "samples"}}, ensure_ascii=False))
