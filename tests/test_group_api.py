"""Synthetic known-answer API cases; no real inventory is asserted."""
import pytest

from backend.tests.test_api_integration import client, repo  # noqa: F401
from tests.test_group_matching import fixtures


@pytest.fixture
def group_fixture(repo):  # noqa: F811
    hotel, facts, _, parsed = fixtures()
    repo.upsert("hotels", hotel.model_dump(mode="json"))
    for fact in facts:
        repo.upsert("facts", fact.model_dump(mode="json"))
    return hotel, parsed


def test_group_search_and_compact_detail_keep_two_independent_positions(client, group_fixture):  # noqa: F811
    hotel, parsed = group_fixture
    response = client.post("/api/search", json={"parsed": parsed.model_dump(mode="json")})
    assert response.status_code == 200, response.text
    hit = next(h for h in response.json()["results"] if h["hotel"]["id"] == hotel.id)
    assert hit["group"] == "all" and len(hit["room_plan"]) == 2
    assert [r["scope"]["facade"] for r in hit["room_plan"]] == ["W", "E"]
    assert all(r["profile"]["clauses"] for r in hit["room_plan"])
    detail = client.post(f"/api/hotels/{hotel.id}/evaluate?compact=true", json=parsed.model_dump(mode="json"))
    assert detail.status_code == 200, detail.text
    assert detail.json()["room_plan"] == hit["room_plan"]
    assert detail.json()["evidence_deferred"]


def test_profiles_are_compact_and_refs_resolve_on_demand(client, group_fixture):  # noqa: F811
    hotel, _ = group_fixture
    path = f"/api/hotels/{hotel.id}/rooms"
    result = client.get(path + "?month=9&facade=W&floor_min=4&floor_max=6")
    assert result.status_code == 200, result.text
    profile = result.json()[0]
    sea = next(c for c in profile["clauses"] if c["characteristic_id"] == "sea_view")
    assert sea["status"] == "yes" and sea["synthetic"]
    assert len(result.content) < 20000
    assert "evidence" not in sea and sea["fact_ids"]
    evidence = client.get(f"/api/hotels/{hotel.id}/facts/{sea['fact_ids'][0]}/evidence")
    assert evidence.status_code == 200 and evidence.json()["fact"]["evidence"]
    for suffix in ("?month=13", "?floor_min=7&floor_max=3", "?facade=FAKE"):
        assert client.get(path + suffix).status_code == 422
    assert client.get("/api/hotels/nonexistent/rooms").status_code == 404
