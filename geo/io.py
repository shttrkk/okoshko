"""Read logical artifacts without changing their provenance byte representation.

For a logical path such as ``buildings.geojson``, the plain file wins. If it is
absent, ``buildings.geojson.gz`` is decompressed and its original bytes returned.
An explicitly supplied ``.gz`` path is always read as raw compressed bytes; this
keeps existing hashes of walk_graph.json.gz and other binary artifacts intact.
``read_json`` therefore expects a logical, uncompressed JSON/GeoJSON path.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path


def exists(path: str | Path) -> bool:
    path = Path(path)
    return path.is_file() or (path.suffix != ".gz" and Path(str(path) + ".gz").is_file())


def read_bytes(path: str | Path) -> bytes:
    path = Path(path)
    if path.is_file() or path.suffix == ".gz":
        return path.read_bytes()
    with gzip.open(str(path) + ".gz", "rb") as stream:
        return stream.read()


def read_json(path: str | Path):
    return json.loads(read_bytes(path))
