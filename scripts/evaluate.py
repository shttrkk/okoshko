"""make eval: read versioned seeds, evaluate independent labels, save report."""
import csv
import gzip
import json
from collections import defaultdict
from pathlib import Path

import yaml

from backend.quality import evaluate, read_labels

root = Path(__file__).resolve().parents[1]
data = root / "data"


def rows(table):
    path = data / "seed" / f"{table}.csv.gz"
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as file:
        return [json.loads(row["payload"]) for row in csv.DictReader(file)]


def main():
    specs = rows("characteristics") or [yaml.safe_load(path.read_text()) for path in (data / "characteristics").glob("*.yaml")]
    facts = rows("facts")
    provenance_claims = rows("claims")
    if not facts:
        from backend.agents.resolver import resolve_claims
        from backend.contracts import CharacteristicSpec, Claim
        claims = defaultdict(list)
        for path in [data/"seed/open_claims.jsonl.gz", data/"geo/geo_claims.jsonl.gz",
                     data/"seed/geo_trait_claims.jsonl.gz", data/"seed/connectivity_claims.jsonl.gz",
                     data/"seed/demo_hotelier_claims.jsonl",
                     data/"eval/website_claims.jsonl"]:
            if not path.is_file():
                continue
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt") as stream:
                for line in stream:
                    claim = Claim.model_validate_json(line)
                    claims[claim.hotel_id].append(claim)
                    provenance_claims.append(claim.model_dump(mode="json"))
        dialogues = data/"seed/demo_hotelier_dialogues.json"
        if dialogues.is_file():
            for row in json.loads(dialogues.read_text())["claims"]:
                claim = Claim.model_validate(row)
                claims[claim.hotel_id].append(claim)
                provenance_claims.append(claim.model_dump(mode="json"))
        typed_specs = [CharacteristicSpec.model_validate(spec) for spec in specs]
        for hotel in rows("hotels"):
            resolved, _ = resolve_claims(hotel["id"], claims[hotel["id"]], typed_specs, rows("sources"), known_floors=hotel.get("floors"))
            facts.extend(fact.model_dump(mode="json") for fact in resolved)
    report = evaluate(rows("hotels"), facts, specs, read_labels(data/"eval"/"labels.csv"), rows("questions"), provenance_claims)
    report["inputs"] = "Versioned seed ledgers; runtime-only POI and manual actions are evaluated separately by /api/quality"
    destination = data / "eval" / "results.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({"status": report["status"], "hotel_count": report["hotel_count"], "label_count": report["label_count"], "coverage": report["coverage"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

