"""Compact transport must preserve access to complete public evidence."""
from datetime import timedelta

import pytest

from backend.contracts import Claim, HotelDetail, now
from backend.tests.test_api_integration import client, repo  # noqa: F401


@pytest.fixture
def calculation(repo):  # noqa: F811
    fact = repo.all("facts")[0]
    evidence = {**fact["evidence"][0], "inputs": {"large": "x" * 100_000}, "output": {"value": 7},
                "artifact": "/data/geo/test-hotel.json", "limitations": ["Large repeated model limitation " * 1000]}
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
    assert preview["evidence"][0].get("inputs", {}) == {} and preview["evidence"][0].get("output", {}) == {}
    assert preview["evidence"][0]["artifact"] == fact["evidence"][0]["artifact"]
    assert preview["evidence"][0]["limitations"] == []
    assert preview["evidence"][0]["label"] == fact["evidence"][0]["label"]
    assert HotelDetail.model_validate(compact).evidence_deferred
    loaded = client.get(path + "/facts/" + fact["id"] + "/evidence").json()
    assert loaded["fact"]["evidence"][0]["inputs"] == fact["evidence"][0]["inputs"]
    assert loaded["fact"]["evidence"][0]["limitations"] == fact["evidence"][0]["limitations"]
    assert loaded["fact"]["evidence"][0]["artifact"] == fact["evidence"][0]["artifact"]
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


def test_similarity_keeps_requested_month_for_additional_must(client, repo):  # noqa: F811
    from backend.tests.test_search import fact
    for hotel_id, may in [("test-hotel", True), ("test-2", True), ("test-3", False)]:
        for month, available in [(5, may), (9, True)]:
            row = fact(hotel_id, "sunset_view", {"available": available}, side="W", low=1, high=5, month=month)
            repo.upsert("facts", row.model_dump(mode="json"))
    calls = []

    def projection(ids=None, month=None):
        calls.append((ids, month))
        return [row for row in repo.all("facts") if month is None or row["scope"].get("month") in {None, month}]

    repo.search_facts = projection
    response = client.post("/api/search", json={"similar_to": "test-2", "parsed": {"text": "похожий, но закат в мае", "month": 5,
                            "must": [{"characteristic_id": "sunset_view", "label": "Закат", "field": "available", "value": True}]}})
    assert response.status_code == 200, response.text
    assert calls == [(None, None)]
    assert [hit["hotel"]["id"] for hit in response.json()["results"]] == ["test-hotel"]
    edited = response.json()["parsed"]
    edited["similar_to_name"] = "untrusted display name"
    edited["clarifications"] = ["Уточните месяц поездки"]
    repeated = client.post("/api/search", json={"parsed": edited})
    assert repeated.status_code == 200, repeated.text
    result = repeated.json()
    assert result["parsed"]["similar_to"] == "test-2" and result["parsed"]["similar_to_name"] == "Synthetic 2"
    assert result["parsed"]["clarifications"] == ["Уточните месяц поездки"]
    assert [hit["hotel"]["id"] for hit in result["results"]] == ["test-hotel"]


def test_compact_keeps_exact_sunset_geometry_in_verdicts(client, repo):  # noqa: F811
    from backend.tests.test_search import fact
    row = fact('test-hotel', 'sunset_view', {'available': True, 'azimuth_deg': 300}, side='N', low=1, high=5, month=9)
    row.evidence[0].inputs = {'facade_azimuth_deg': 345, 'window_half_angle_deg': 75, 'raw_array': [42]*1000}
    repo.upsert('facts', row.model_dump(mode='json'))
    query = {'text': '', 'month': 9, 'must': [{'characteristic_id': 'sunset_view', 'label': 'Закат', 'field': 'available', 'op': 'eq', 'value': True}]}
    path = '/api/hotels/test-hotel/evaluate'
    full = client.post(path, json=query).json()
    compact = client.post(path+'?compact=true', json=query).json()
    assert compact['verdicts'] == full['verdicts']
    assert compact['disappointments'] == full['disappointments']
    projected = next(f for f in compact['facts'] if f['id'] == row.id)
    assert projected['evidence'][0]['inputs'] == {'facade_azimuth_deg': 345, 'window_half_angle_deg': 75}
