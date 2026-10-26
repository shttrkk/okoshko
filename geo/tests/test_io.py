"""Known-answer checks for logical artifact reads and unchanged input hashes."""
from __future__ import annotations

import gzip
import hashlib
import os

import pytest

from geo.io import exists, read_bytes, read_json
from geo.region import Region
from geo.runtime_cache import input_digest


def test_logical_gzip_returns_exact_original_bytes_and_explicit_gzip_is_raw(tmp_path):
    original = '{ "type": "FeatureCollection", "features": [], "name": "Сочи" }\n'.encode()
    logical = tmp_path / "buildings.geojson"
    physical = tmp_path / "buildings.geojson.gz"
    compressed = gzip.compress(original, mtime=0)
    physical.write_bytes(compressed)
    assert not logical.exists() and exists(logical) and exists(physical)
    assert read_bytes(logical) == original
    assert read_bytes(physical) == compressed
    assert read_json(logical)["name"] == "Сочи"
    assert input_digest(logical) == hashlib.sha256(original).hexdigest()
    assert input_digest(physical) == hashlib.sha256(compressed).hexdigest()


def test_plain_artifact_wins_over_gzip_fallback(tmp_path):
    logical = tmp_path / "roads.geojson"
    logical.write_bytes(b'{"features":[]}\n')
    (tmp_path / "roads.geojson.gz").write_bytes(b"invalid gzip")
    assert read_bytes(logical) == b'{"features":[]}\n'
    assert read_json(logical) == {"features": []}


def test_missing_or_corrupt_fallback_cannot_pass_a_provenance_read(tmp_path):
    logical = tmp_path / "pois.geojson"
    assert not exists(logical)
    with pytest.raises(FileNotFoundError):
        read_bytes(logical)
    (tmp_path / "pois.geojson.gz").write_bytes(b"invalid gzip")
    with pytest.raises(gzip.BadGzipFile):
        read_bytes(logical)


def test_region_uses_logical_names_and_original_hashes_for_gzip_only_layers(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    original = b'{ "type": "FeatureCollection", "features": [] }\n'
    layers = ("buildings", "coastline", "roads", "railways", "beaches", "pois")
    for name in layers:
        (seed / (name + ".geojson.gz")).write_bytes(gzip.compress(original, mtime=0))
    region = Region(tmp_path)
    assert region.inputs == {"/data/seed/" + name + ".geojson": hashlib.sha256(original).hexdigest() for name in layers}
    assert all(not features for features in region.features.values())


def test_cached_input_digest_invalidates_an_equal_size_replacement(tmp_path):
    logical = tmp_path / "buildings.geojson"
    physical = tmp_path / "buildings.geojson.gz"
    physical.write_bytes(gzip.compress(b"original A", mtime=0))
    old_stat = physical.stat()
    assert input_digest(logical) == hashlib.sha256(b"original A").hexdigest()
    physical.write_bytes(gzip.compress(b"original B", mtime=0))
    os.utime(physical, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    assert physical.stat().st_size == old_stat.st_size
    assert input_digest(logical) == hashlib.sha256(b"original B").hexdigest()
