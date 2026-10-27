"""Prepared observations fail closed before writes and never replace source priors."""
import gzip
import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.tz_seed import insert_prepared, prepare_seed

ROOT = Path(__file__).resolve().parents[1]


def write_bundle(root, *, change=None):
    seed = root / "seed"
    seed.mkdir(exist_ok=True)
    specs = root / "characteristics"
    specs.mkdir(exist_ok=True)
    (specs / "night_open.yaml").write_bytes((ROOT / "data/characteristics/night_open.yaml").read_bytes())
    at = datetime(2026, 10, 25, tzinfo=UTC)
    source = {"id": "test_source", "name": "Known-answer source", "kind": "open_data",
              "url": "https://example.org/data", "license": "CC0-1.0", "attribution": "Test author",
              "observed_at": at.isoformat(), "retrieved_at": at.isoformat()}
    (seed / "tz_input.json").write_text('{"test_fixture":true}')
    claim = {"id": "test_claim", "hotel_id": "h", "characteristic_id": "night_open",
             "value": {"available": True}, "source": "test_source", "method": "tz_routes@1",
             "confidence": .5, "observed_at": at.isoformat(), "valid_until": (at + timedelta(days=30)).isoformat(),
             "evidence": [{"kind": "calculation", "label": "Known-answer fixture", "source_ids": ["test_source"],
                           "artifact": "/data/seed/tz_input.json", "inputs": {"input_sha256": {
                               "/data/seed/tz_input.json": hashlib.sha256((seed / "tz_input.json").read_bytes()).hexdigest()}}}]}
    if change:
        change(claim)
    with gzip.open(seed / "tz_routes_claims.jsonl.gz", "wt") as stream:
        stream.write(json.dumps(claim) + "\n")
    (seed / "tz_routes_sources.json").write_text(json.dumps([source]))
    outputs = {path.name: {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
               for path in seed.iterdir() if path.name != "tz_manifest.json"}
    manifest = {"claims": 1, "sources": [source], "outputs": outputs}
    (seed / "tz_manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_absent_bundle_preserves_minimal_seed(tmp_path):
    assert prepare_seed(tmp_path, {"h"}).claims == ()


def test_valid_bundle_keeps_value_provenance_and_dates(tmp_path):
    write_bundle(tmp_path)
    bundle = prepare_seed(tmp_path, {"h"})
    assert len(bundle.claims) == len(bundle.sources) == 1
    assert bundle.claims[0].value == {"available": True}
    assert bundle.claims[0].evidence[0].inputs["input_sha256"]


@pytest.mark.parametrize("change", [
    lambda claim: claim.update(hotel_id="other"),
    lambda claim: claim.update(source="unknown"),
    lambda claim: claim.update(characteristic_id="missing"),
    lambda claim: claim.update(synthetic=True),
    lambda claim: claim.update(method="unregistered"),
    lambda claim: claim.update(valid_until="2030-01-01T00:00:00Z"),
    lambda claim: claim["evidence"][0].update(source_ids=["unknown"]),
    lambda claim: claim["evidence"][0].update(artifact="/data/raw/private.json"),
    lambda claim: claim["evidence"][0]["inputs"].update(input_sha256={"/data/../secret": "a" * 64}),
])
def test_invalid_semantics_rejected_before_database_is_needed(tmp_path, change):
    write_bundle(tmp_path, change=change)
    with pytest.raises(ValueError):
        prepare_seed(tmp_path, {"h"})


def test_changed_input_and_manifest_traversal_are_rejected(tmp_path):
    manifest = write_bundle(tmp_path)
    (tmp_path / "seed/tz_input.json").write_text("changed")
    with pytest.raises(ValueError, match="size|checksum"):
        prepare_seed(tmp_path, {"h"})
    manifest["outputs"] = {"../../secret": {"bytes": 1, "sha256": "a" * 64}}
    (tmp_path / "seed/tz_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="filename"):
        prepare_seed(tmp_path, {"h"})


def test_symlink_and_inconsistent_source_identity_are_rejected(tmp_path):
    manifest = write_bundle(tmp_path)
    raw = tmp_path / "seed/tz_input.json"
    raw.rename(tmp_path / "outside.json")
    raw.symlink_to(tmp_path / "outside.json")
    with pytest.raises(ValueError, match="Symlink"):
        prepare_seed(tmp_path, {"h"})
    raw.unlink()
    raw.write_text('{"test_fixture":true}')
    manifest["sources"].append({**manifest["sources"][0], "license": "Other"})
    (tmp_path / "seed/tz_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="source identity"):
        prepare_seed(tmp_path, {"h"})


class InsertOnlyConnection:
    """Model conflict behavior while inspecting every write statement."""
    def __init__(self):
        self.sources = {"test_source": {"alpha": 19, "beta": 2}}
        self.claims = {}
        self.buffer = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def cursor(self):
        return self

    def executemany(self, statement, rows):
        assert "ON CONFLICT(id) DO NOTHING" in statement
        for identifier, payload in rows:
            self.sources.setdefault(identifier, payload.obj)

    @contextmanager
    def copy(self, statement):
        assert statement.startswith("COPY tz_seed_import")
        self.buffer = []
        yield self

    def write_row(self, row):
        self.buffer.append(row)

    def execute(self, statement):
        assert "DELETE" not in statement and "UPDATE" not in statement
        if statement.startswith("INSERT INTO claims"):
            count = len(self.claims)
            for identifier, payload in self.buffer:
                self.claims.setdefault(identifier, json.loads(payload))
            self.rowcount = len(self.claims) - count
        return self


def test_restart_preserves_claims_and_learned_source_priors(tmp_path):
    write_bundle(tmp_path)
    bundle = prepare_seed(tmp_path, {"h"})
    conn = InsertOnlyConnection()
    assert insert_prepared(conn, bundle)["inserted_claims"] == 1
    conn.claims["test_claim"]["confidence"] = .91
    assert insert_prepared(conn, bundle)["inserted_claims"] == 0
    assert conn.claims["test_claim"]["confidence"] == .91
    assert conn.sources["test_source"] == {"alpha": 19, "beta": 2}
