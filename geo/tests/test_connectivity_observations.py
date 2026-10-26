"""Property identity and unknown-speed invariants on synthetic OSM excerpts."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from shapely.geometry import box, mapping

from backend.agents.query_match import assess_hotel
from backend.agents.resolver import resolve_claims
from backend.contracts import CharacteristicSpec, Claim, Criterion, ParsedQuery
from backend.contracts.validation import validate_value
from pipeline.collect_connectivity import _collisions, build_extract, claims_from_extract, collect


def spec():
    return CharacteristicSpec.model_validate(yaml.safe_load(Path("data/characteristics/connectivity.yaml").read_text()))


def fixture_extract(*, canonical_access="wlan", alias_access=None, alias_changes=None):
    observed = (datetime.now(UTC)-timedelta(days=2)).replace(microsecond=0).isoformat()
    source = {"id": "osm_geofabrik", "name": "Synthetic OSM property fixture", "license": "ODbL-1.0",
              "attribution": "Synthetic test coordinates, not hotel ground truth", "observed_at": observed}
    canonical = {"id": "osm-way-1", "name": "Example", "lon": 39.72, "lat": 43.58,
                 "footprint": mapping(box(39.7199, 43.5799, 39.7201, 43.5801)),
                 "tags": {"name": "Example", "tourism": "hotel", "addr:street": "Test", "addr:housenumber": "1"}}
    if canonical_access is not None:
        canonical["tags"]["internet_access"] = canonical_access
    hotel = {"id": canonical["id"], "name": canonical["name"], "tags": {"source_records": []}}
    rows = [canonical]
    if alias_access is not None:
        alias = {"id": "osm-node-2", "name": "Example Hotel", "lon": 39.72, "lat": 43.58, "footprint": None,
                 "tags": {"name": "Example Hotel", "tourism": "hotel", "addr:street": "Test",
                          "addr:housenumber": "1", "internet_access": alias_access, "internet_access:fee": "no"}}
        for key, value in (alias_changes or {}).items():
            if key == "tags":
                alias["tags"].update(value)
            else:
                alias[key] = value
        rows.append(alias)
        hotel["tags"]["source_records"].append({"id": alias["id"], "source_id": source["id"]})
    download = {"url": "https://example.test/synthetic.osm.pbf", "sha256": "a"*64,
                "retrieved_at": (datetime.now(UTC)-timedelta(days=1)).replace(microsecond=0).isoformat()}
    return build_extract([hotel], rows, source, download, input_hashes={"synthetic_fixture": "b"*64}, synthetic=True)


def encoded(extract):
    return (json.dumps(extract, ensure_ascii=False, sort_keys=True, indent=2)+"\n").encode()


@pytest.mark.parametrize(("access", "expected_wifi"), [("wlan", True), ("yes", None), ("wired", None)])
def test_reported_access_never_becomes_measured_speed(access, expected_wifi):
    extract = fixture_extract(canonical_access=access)
    [row] = claims_from_extract(encoded(extract), spec())
    claim = Claim.model_validate(row)
    assert claim.value["wifi_reported"] is expected_wifi
    assert claim.value["internet_access"] == access
    assert claim.value["measurement"] is False
    assert all(claim.value[key] is None for key in ("download_mbps", "upload_mbps", "latency_ms"))
    assert "available" not in claim.value and claim.confidence == .55
    assert claim.observed_at.isoformat() == extract["source"]["observed_at"]
    assert (claim.valid_until-claim.observed_at).days == spec().ttl_days
    assert claim.synthetic and claim.evidence[0].kind == "simulation"
    assert claim.evidence[0].inputs["pbf_sha256"] == "a"*64
    validate_value(claim.value, spec().value_schema)


def test_reviewed_alias_has_reprocessable_geometry_identity_proof():
    extract = fixture_extract(canonical_access=None, alias_access="wlan")
    [row] = claims_from_extract(encoded(extract), spec())
    assert row["hotel_id"] == "osm-way-1"
    assert row["evidence"][0]["inputs"]["osm_id"] == "osm-node-2"
    proof = row["evidence"][0]["inputs"]["identity"]["proof"]
    assert proof["same_street_house_number"] and proof["boundary_margin_m"] > 1
    assert row["value"]["fee"] == "no"


@pytest.mark.parametrize("changes", [
    {"tags": {"tourism": "cafe"}},
    {"name": "Different property"},
    {"tags": {"addr:housenumber": "2"}},
    {"lon": 39.7201},  # boundary, not robust property containment
    {"lon": 39.721},   # outside, even when name/address happen to agree
])
def test_neighbor_or_ambiguous_alias_does_not_supply_hotel_wifi(changes):
    extract = fixture_extract(canonical_access=None, alias_access="wlan", alias_changes=changes)
    assert extract["records"] == []
    assert extract["rejected_aliases"]


def test_default_work_search_stays_unknown_after_materializing_wlan():
    assert spec().search.field == "download_mbps" and spec().search.default_op == "gte"
    assert spec().search.default_value == 30
    rows = claims_from_extract(encoded(fixture_extract()), spec())
    facts, conflicts = resolve_claims("osm-way-1", [Claim.model_validate(row) for row in rows], [spec()], [])
    assert not conflicts and len(facts) == 1
    fact = facts[0]
    assert fact.status == "computed" and fact.value["wifi_reported"] is True
    assert fact.value["download_mbps"] is None
    request = ParsedQuery(text="Интернет для работы от 30 Мбит/с", must=[
        Criterion(characteristic_id="connectivity", label="Скорость ≥30 Мбит/с", field="download_mbps", op="gte", value=30)])
    assessment = assess_hotel(facts, request, [spec()])
    assert assessment.matches[0].status == "unknown"
    assert not assessment.matches[0].supported


def test_conflicting_property_reports_require_review_instead_of_arbitrary_publication():
    extract = fixture_extract(canonical_access="wlan", alias_access="no")
    assert len(extract["records"]) == 2
    assert _collisions(extract["records"]) == [{"hotel_id": "osm-way-1", "field": "internet_access",
                                               "values": ["no", "wlan"], "osm_ids": ["osm-node-2", "osm-way-1"]}]
    with pytest.raises(ValueError, match="conflicting OSM property reports"):
        claims_from_extract(encoded(extract), spec())


def test_extract_reprocesses_without_raw_files_and_gzip_is_reproducible(tmp_path):
    (tmp_path/"seed").mkdir()
    (tmp_path/"characteristics").mkdir()
    (tmp_path/"characteristics"/"connectivity.yaml").write_text(Path("data/characteristics/connectivity.yaml").read_text())
    (tmp_path/"seed"/"connectivity_observations.json").write_bytes(encoded(fixture_extract()))
    first = collect(tmp_path)
    ledger = tmp_path/"seed"/"connectivity_claims.jsonl.gz"
    content = ledger.read_bytes()
    second = collect(tmp_path)
    assert ledger.read_bytes() == content and first["ledger"] == second["ledger"]
    assert first["speed_observations"] == 0


def test_modified_source_excerpt_fails_integrity_check():
    extract = fixture_extract()
    extract["records"][0]["raw_tags"]["internet_access"] = "yes"
    with pytest.raises(ValueError, match="hash mismatch"):
        claims_from_extract(encoded(extract), spec())
