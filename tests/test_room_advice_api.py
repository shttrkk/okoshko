"""Advice transport retains scope provenance and isolates hotels."""
import pytest

from backend.tests.test_api_integration import client, repo  # noqa: F401
from tests.test_group_matching import fixtures


@pytest.fixture
def advice_fixture(repo):  # noqa: F811
    hotel, facts, _, _ = fixtures()
    repo.upsert("hotels", hotel.model_dump(mode="json"))
    for fact in facts:
        repo.upsert("facts", fact.model_dump(mode="json"))
    return hotel, facts


def test_advice_api_is_bounded_and_exposes_only_own_fact_references(client, advice_fixture):  # noqa: F811
    hotel, facts = advice_fixture
    result = client.get(f"/api/hotels/{hotel.id}/room-advice?month=9")
    assert result.status_code == 200, result.text
    rows = result.json()
    assert 1 <= len(rows) <= 4
    sea = next(r for r in rows if r["characteristic_id"] == "sea_view")
    assert sea["synthetic"]
    assert all(option["scope"]["facade"] == "W" and option["scope"]["floor_min"] >= 4 for option in sea["options"])
    own_ids = {f.id for f in facts}
    assert set(sea["fact_ids"]) <= own_ids and sea["fact_ids"]
    for option in sea["options"]:
        assert option["fact_ids"] and set(option["fact_ids"]) <= own_ids
        for fid in option["fact_ids"]:
            loaded = client.get(f"/api/hotels/{hotel.id}/facts/{fid}/evidence")
            assert loaded.status_code == 200 and loaded.json()["fact"]["evidence"]
    assert len(result.content) < 30000
    assert client.get(f"/api/hotels/{hotel.id}/room-advice?month=13").status_code == 422
    assert client.get("/api/hotels/nonexistent/room-advice").status_code == 404
