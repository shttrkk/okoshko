"""Shared reproducible IO; raw downloads never belong in git."""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw"
SEED = ROOT / "data/seed"
CONFIG_PATH = Path(os.environ.get("REGION", str(ROOT / "pipeline/regions/sochi.yaml")))
if not CONFIG_PATH.is_absolute():
    CONFIG_PATH = ROOT / CONFIG_PATH
CONFIG = yaml.safe_load(CONFIG_PATH.read_text())
ZONE_A = CONFIG["zones"]["A"]["bbox"]
ZONE_B = CONFIG["zones"]["B"]["bbox"]
COVERAGE_BBOXES = [z["coverage_bbox"] for z in CONFIG["zones"].values()]
BBOX = COVERAGE_BBOXES[0]
RETRIEVED = "2026-10-26T00:00:00Z"  # replaced by actual successful retrieval metadata
UA = "OknoHackathon/1.0 (open-data research; local noncommercial prototype)"


def utcnow():
    return datetime.now(UTC).isoformat()


def dump(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    temp.replace(path)


def read(path: Path):
    return json.loads(path.read_text())


def sha(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, name: str, *, params=None, refresh=False):
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / name
    meta = path.with_suffix(path.suffix + ".metadata.json")
    expected_url = requests.Request("GET", url, params=params).prepare().url
    if path.exists() and meta.exists() and not refresh and read(meta).get("url") == expected_url:
        return path, read(meta)
    with requests.get(url, params=params, headers={"User-Agent": UA}, timeout=(30, 180), stream=True) as r:
        r.raise_for_status()
        temp = path.with_suffix(path.suffix + ".part")
        with temp.open("wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                f.write(chunk)
        temp.replace(path)
        record = {"url": r.url, "retrieved_at": utcnow(), "last_modified": r.headers.get("Last-Modified"),
                  "etag": r.headers.get("ETag"), "sha256": sha(path), "bytes": path.stat().st_size}
    dump(meta, record)
    return path, record


def gzip_json(path, value):
    with path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as f:
            f.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def table(name, records):
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(["id", "payload"])
    for row in records:
        writer.writerow([row["id"], json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))])
    path = SEED / (name + ".csv.gz")
    with path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as f:
            f.write(text.getvalue().encode())


def collection(features):
    return {"type": "FeatureCollection", "features": sorted(features, key=lambda f: f["id"])}


def source(id, name, url, license, attribution, observed_at, retrieved_at, notes="", kind="open_data"):
    return dict(id=id, name=name, kind=kind, url=url, license=license, attribution=attribution,
                observed_at=observed_at, retrieved_at=retrieved_at, used=True, notes=notes)
