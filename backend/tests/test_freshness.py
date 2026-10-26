"""Synthetic supersession, durable evidence and local passport change invariants."""
from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import numpy as np

from backend.agents.freshness import durable_geo, refresh_expired, simulate_change
from backend.agents.resolver import resolve_hotel
from backend.contracts import Claim, Evidence, ExpectationPassport, PassportItem, Scope, now
from backend.tests.test_api_integration import ApiRepo


def observation(id, first, last, available, *, expired=False):
    return Claim(id=id, hotel_id="test-hotel", characteristic_id="sea_view", source="geo_engine", method="test_viewshed@1",
                 scope=Scope(type="floor_range", facade="W", floor_min=first, floor_max=last),
                 value={"available": available, "min_floor": first if available else None, "facade": "W"},
                 evidence=[Evidence(kind="simulation", label="Synthetic LOS test", source_ids=["geo_engine"],
                                    artifact="/data/geo/test-hotel.json")], confidence=.7,
                 observed_at=now()-timedelta(days=2), valid_until=now()+timedelta(days=-1 if expired else 90), synthetic=True)


def write_result(data_dir, claims):
    directory = data_dir / "geo"
    directory.mkdir(parents=True, exist_ok=True)
    filename = "test-hotel-change-synthetic"
    artifact = "/data/geo/"+filename+".json"
    for claim in claims:
        for evidence in claim.evidence:
            evidence.artifact = artifact
    geo = {"hotel_id": "test-hotel", "artifact": artifact, "center": [39.72, 43.58], "facades": [], "synthetic": True}
    document = {**geo, "claims": [claim.model_dump(mode="json") for claim in claims]}
    (directory / (filename+".json")).write_text(json.dumps(document))
    np.savez_compressed(directory / (filename+".npz"), available=np.array([False]))
    return {"geo": geo, "claims": document["claims"]}


def test_fixture_supersedes_all_old_ranges_and_is_idempotent(tmp_path):
    repo = ApiRepo()
    for claim in [observation("old-low", 1, 3, False), observation("old-high", 4, 8, True)]:
        repo.add_claim(claim)
    before = resolve_hotel("test-hotel", repo)
    old_high = next(fact for fact in before if fact.characteristic_id == "sea_view" and fact.value["available"])
    passport = ExpectationPassport(hotel_id="test-hotel", status="confirmed", room_number="501",
                 items=[PassportItem(characteristic_id="sea_view", text="Море из номера", scope=old_high.scope, fact_ids=[old_high.id], status="confirmed")])
    repo.insert("expectation_passports", passport.model_dump(mode="json"))
    result = write_result(tmp_path / "data", [observation("new-blocked", 1, 8, False)])
    runtime = tmp_path / "runtime"
    with patch("geo.service.compute_hotel", return_value=result) as compute:
        change = simulate_change(repo, "test-hotel", data_dir=tmp_path / "data", runtime=runtime)
        duplicate = simulate_change(repo, "test-hotel", data_dir=tmp_path / "data", runtime=runtime)
    assert change.id == duplicate.id and compute.call_count == 1
    assert compute.call_args.kwargs["new_building"]["properties"]["synthetic"]
    assert change.synthetic and change.before and change.after
    new_claim = repo.get("claims", "new-blocked")
    assert set(new_claim["supersedes_ids"]) == {"old-low", "old-high"}
    assert new_claim["run_id"] == change.payload["run_id"]
    sea_facts = repo.find("facts", characteristic_id="sea_view")
    assert len(sea_facts) == 1 and sea_facts[0]["scope"]["floor_max"] == 8
    assert sea_facts[0]["value"]["available"] is False
    updated_passport = repo.get("expectation_passports", passport.id)
    assert updated_passport["status"] == "at_risk" and updated_passport["items"][0]["status"] == "at_risk"
    assert len(updated_passport["notifications"]) == 1
    assert updated_passport["notifications"][0]["channel"] == "local"
    artifact = new_claim["evidence"][0]["artifact"]
    assert artifact.startswith("/data/runtime/geo/")
    persisted = json.loads((runtime / "geo" / artifact.split("/")[-1]).read_text())
    assert persisted["claims"][0]["run_id"] == new_claim["run_id"]
    assert persisted["claims"][0]["evidence"][0]["artifact"] == artifact
    with np.load(runtime / "geo" / artifact.split("/")[-1].replace(".json", ".npz"), allow_pickle=False) as arrays:
        assert not arrays["available"].any()
    # Reconstruct only the durable repository; no module-level event cache is needed.
    restored = ApiRepo()
    restored.tables = repo.tables
    assert simulate_change(restored, "test-hotel", data_dir=tmp_path / "data", runtime=runtime).id == change.id


def test_expired_geo_rechecks_tool_and_resolves_new_claim(tmp_path):
    repo = ApiRepo()
    repo.add_claim(observation("expired", 1, 8, True, expired=True))
    resolve_hotel("test-hotel", repo)
    assert repo.find("facts", characteristic_id="sea_view")[0]["status"] == "stale"
    result = write_result(tmp_path / "data", [observation("rechecked", 1, 8, False)])
    with patch("geo.service.compute_hotel", return_value=result) as compute:
        refreshed = refresh_expired(repo, data_dir=tmp_path / "data", runtime=tmp_path / "runtime")
    assert compute.call_count == 1 and refreshed["processed"] == 1
    assert repo.get("agent_runs", refreshed["run_id"])["status"] == "completed"
    assert repo.get("claims", "rechecked")["supersedes_ids"] == ["expired"]
    fact = repo.find("facts", characteristic_id="sea_view")[0]
    assert fact["status"] == "computed" and fact["value"]["available"] is False


def test_expired_archive_is_not_redated_as_new_observation(tmp_path):
    repo = ApiRepo()
    old = Claim(id="expired-climate", hotel_id="test-hotel", characteristic_id="climate", source="open_meteo", method="archive@1",
                scope={"month": 9}, value={"month": 9, "water_c": 23}, evidence=[Evidence(kind="open_data", label="Synthetic archive", source_ids=["open_meteo"], url="https://example.org/archive")],
                confidence=.7, observed_at=now()-timedelta(days=200), valid_until=now()-timedelta(days=100))
    repo.add_claim(old)
    result = refresh_expired(repo, data_dir=tmp_path / "data", runtime=tmp_path / "runtime")
    assert result["processed"] == 1
    assert len(repo.all("claims")) == 1
    assert repo.find("facts", characteristic_id="climate")[0]["status"] == "stale"


def test_durable_geo_rewrites_nested_route_and_evidence(tmp_path):
    result = write_result(tmp_path / "data", [observation("derived-path", 1, 8, False)])
    artifact = result["geo"]["artifact"]
    result["claims"][0]["value"]["route_ref"] = artifact+"#route"
    persisted = durable_geo(result, tmp_path / "data", tmp_path / "runtime")
    assert persisted["claims"][0]["value"]["route_ref"].startswith("/data/runtime/geo/")


def test_missing_visibility_inputs_do_not_publish_a_building_change(tmp_path):
    import pytest
    repo = ApiRepo()
    result = write_result(tmp_path / "data", [])
    with patch("geo.service.compute_hotel", return_value=result), pytest.raises(ValueError, match="не хватает геовходов"):
        simulate_change(repo, "test-hotel", data_dir=tmp_path / "data", runtime=tmp_path / "runtime")
    assert not repo.all("change_events") and not repo.all("claims")
    assert repo.all("agent_runs")[0]["status"] == "failed"


def test_expiry_recheck_keeps_the_active_synthetic_building(tmp_path):
    repo = ApiRepo()
    repo.add_claim(observation("expired-with-building", 1, 8, True, expired=True))
    fixture = {"type": "Feature", "properties": {"synthetic": True}, "geometry": {"type": "Polygon", "coordinates": []}}
    repo.insert("change_events", {"id": "active-building", "affected_hotel_ids": ["test-hotel"], "payload": {"fixture": fixture}})
    result = write_result(tmp_path / "data", [observation("refreshed-with-building", 1, 8, False)])
    runtime = tmp_path / "runtime"
    with patch("geo.service.compute_hotel", return_value=result) as compute:
        refresh = refresh_expired(repo, data_dir=tmp_path / "data", runtime=runtime)
    assert compute.call_args.kwargs["new_building"] == fixture
    claim = repo.get("claims", "refreshed-with-building")
    document = json.loads((runtime / "geo" / claim["evidence"][0]["artifact"].split("/")[-1]).read_text())
    assert document["claims"][0]["run_id"] == refresh["run_id"]
    assert document["claims"][0]["supersedes_ids"] == ["expired-with-building"]


def test_expiry_cannot_be_renewed_by_identical_local_geo_snapshot(tmp_path):
    import hashlib
    repo = ApiRepo()
    seed = tmp_path/"seed"
    seed.mkdir()
    source = seed/"buildings.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}')
    claim = observation("old-expired", 1, 5, True, expired=True)
    claim.evidence[0].inputs = {"input_sha256": {"/data/seed/buildings.geojson": hashlib.sha256(source.read_bytes()).hexdigest()}}
    repo.add_claim(claim)
    with patch("geo.service.compute_hotel", side_effect=AssertionError("Unchanged data must not renew observation")):
        result = refresh_expired(repo, data_dir=tmp_path, runtime=tmp_path/"runtime")
    assert result["processed"] == 1
    assert len(repo.all("claims")) == 1
    [fact] = [row for row in repo.find("facts", hotel_id="test-hotel") if row["characteristic_id"] == "sea_view"]
    assert fact["status"] == "stale" and fact["valid_until"] == claim.valid_until.isoformat()
