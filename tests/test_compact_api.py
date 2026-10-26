"""Compact transport must preserve access to complete public evidence."""
from datetime import timedelta

import pytest

from backend.contracts import Claim, now
from backend.tests.test_api_integration import client, repo  # noqa: F401


@pytest.fixture
def calculation(repo):  # noqa: F811
    fact = repo.all("facts")[0]
    evidence = {**fact["evidence"][0], "inputs": {"large": "x" * 100_000}, "output": {"value": 7}}
    claim = Claim(id="compact-claim", hotel_id=fact["hotel_id"], characteristic_id=fact["characteristic_id"],
                  value=fact["value"], source="geo_engine", method="synthetic_test_v1", evidence=[evidence],
                  confidence=.7, observed_at=now()-timedelta(days=1), valid_until=now()+timedelta(days=1), synthetic=True)
    repo.add_claim(claim)
    fact.update(evidence=[evidence], supporting_claim_ids=[claim.id])
    repo.upsert("facts", fact)
    return fact, claim.model_dump(mode="json")


def test_compact_is_opt_in_and_evidence_can_be_loaded_completely(client, calculation):  # noqa: F811
    fact, claim = calculation
    path = "/api/hotels/" + fact["hotel_id"]
    full = client.get(path).json()
    compact = client.get(path + "?compact=true").json()
    assert full["claims"] and full["geo"]
    assert not full["evidence_deferred"] and not full["geometry_deferred"]
    assert compact["claims"] == [] and compact["geo"] == {}
    assert compact["evidence_deferred"] and compact["geometry_deferred"]
    assert [f["id"] for f in compact["facts"]] == [f["id"] for f in full["facts"]]
    preview = next(f for f in compact["facts"] if f["id"] == fact["id"])
    assert preview["supporting_claim_ids"] == [claim["id"]]
    assert preview["evidence"][0]["id"] == fact["evidence"][0]["id"]
    assert preview["evidence"][0]["source_ids"] == fact["evidence"][0]["source_ids"]
    assert preview["evidence"][0]["inputs"] == {} and preview["evidence"][0]["output"] == {}
    loaded = client.get(path + "/facts/" + fact["id"] + "/evidence").json()
    assert loaded["fact"]["evidence"][0]["inputs"] == fact["evidence"][0]["inputs"]
    assert loaded["claims"][0]["observed_at"] and loaded["claims"][0]["valid_until"]
    assert loaded["claims"][0]["source"] == "geo_engine"
    assert client.get("/api/hotels/test-2/facts/" + fact["id"] + "/evidence").status_code == 404
    assert client.get(path + "/facts/missing/evidence").status_code == 404
    assert client.get("/api/hotels/missing/facts/" + fact["id"] + "/evidence").status_code == 404


def test_compare_uses_one_bulk_read_and_edited_criteria_survive(client, repo, calculation):  # noqa: F811
    calls = []

    def bulk(ids, *, compact=False):
        calls.append((ids, compact))
        return {table: [row for row in repo.all(table) if row.get("hotel_id", row["id"]) in ids]
                for table in ["hotels", "facts", "claims", "conflicts", "questions", "change_events"]}

    repo.catalog_details = bulk
    parsed = {"text": "edited", "must": [{"characteristic_id": "beach_walk", "label": "Пляж", "field": "minutes", "op": "lte", "value": 3}]}
    response = client.post("/api/compare?compact=true", json={"ids": ["test-hotel", "test-2"], "parsed": parsed})
    assert response.status_code == 200, response.text
    assert calls == [(["test-hotel", "test-2"], True)]
    assert all(h["evidence_deferred"] and h["geometry_deferred"] for h in response.json()["hotels"])
    assert all(h["verdicts"][0]["status"] != "yes" for h in response.json()["hotels"])
    # A bulk reader may return more than the SQL compact projection in tests.
    # The public compact contract still excludes the full claims.
    assert all(h["claims"] == [] for h in response.json()["hotels"])
    assert client.get("/api/compare?ids=test-hotel,test-2&month=13").status_code == 422
