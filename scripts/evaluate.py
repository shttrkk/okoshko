"""make eval: read versioned seeds, evaluate independent labels, save report."""
import csv
import gzip
import json
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
    report = evaluate(rows("hotels"), rows("facts"), specs, read_labels(data/"eval"/"labels.csv"), rows("questions"))
    destination = data / "eval" / "results.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({"status": report["status"], "hotel_count": report["hotel_count"], "label_count": report["label_count"], "coverage": report["coverage"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

