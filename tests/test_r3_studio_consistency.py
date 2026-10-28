"""Known-answer parity: preview and published search use the same evidence."""
from datetime import timedelta

import pytest

from backend.api import studio
from backend.contracts import Claim, Evidence, now
from backend.tests.test_api_integration import client, repo  # noqa: F401


@pytest.mark.parametrize("distance,confidence", [(192, .65), (192, .95), (301, .65), (192, .4)])
def test_preview_and_published_search_agree(client, repo, monkeypatch, distance, confidence):  # noqa: F811
    spec = client.post("/api/characteristics/compile", json={"text": "Аптека ближе 300 м"}).json()["spec"]

    def preview(body, hotel, store, trace):
        value = {"available": True, "distance_m": distance, "near_m": 300}
        claim = Claim(hotel_id=hotel["id"], characteristic_id=body.id, value=value,
                      source="known_answer_fixture", method="synthetic_distance_fixture@1",
                      confidence=confidence, synthetic=True, observed_at=now(), valid_until=now()+timedelta(days=3),
                      evidence=[Evidence(kind="simulation", label="Known distance, synthetic test input",
                                         artifact="/data/eval/README.md",
                                         source_ids=["known_answer_fixture"], output=value)])
        return [claim], [], "offline"

    monkeypatch.setattr(studio, "execute_preview", preview)
    before_claims, before_facts = repo.all("claims"), repo.all("facts")
    response = client.post("/api/characteristics/dry-run", json=spec)
    assert response.status_code == 200, response.text
    dry = response.json()
    assert repo.all("claims") == before_claims and repo.all("facts") == before_facts
    published = client.post("/api/characteristics/publish", json=spec)
    assert published.status_code == 200, published.text
    parsed = {"text": "", "must": [{"characteristic_id": spec["id"], "label": "Аптека ≤ 300 м",
                                    "field": "distance_m", "op": "lte", "value": 300}]}
    found = client.post("/api/search", json={"text": "", "parsed": parsed}).json()
    actual = {row["hotel"]["id"]: row for row in found["results"]}
    for row in dry["results"]:
        hit = actual[row["hotel_id"]]
        assert row["matched"] == (hit["group"] == "all")
        assert row["match_status"] == hit["verdicts"][0]["status"]
    expected = distance <= 300 and confidence >= .6
    assert all(row["matched"] is expected for row in dry["results"])
