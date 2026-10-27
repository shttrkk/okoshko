"""Preview counts describe the actual sample; missing evidence never counts as yes."""
import copy
from datetime import timedelta

from backend.api import studio
from backend.contracts import Claim, Evidence, now
from backend.tests.test_api_integration import client, repo  # noqa: F401


def test_preview_reports_names_sample_size_and_real_yes_count_without_publishing(client, repo, monkeypatch):  # noqa: F811
    repo.insert("hotels", {"id": "test-z-extra", "name": "Outside sample", "lat": 43.58, "lon": 39.72})
    repo.insert("hotels", {"id": "held", "name": "Identity held", "lat": 43.58, "lon": 39.72})
    monkeypatch.setattr(studio, "searchable", lambda hotel: hotel["id"] != "held")
    spec = client.post("/api/characteristics/compile", json={"text": "Искупаться до завтрака"}).json()["spec"]
    before = {name: copy.deepcopy(repo.all(name)) for name in ("claims", "facts", "characteristics")}

    def preview(body, hotel, store, trace):
        if hotel["id"] == "test-hotel":
            return [], ["Нет исходных данных"], "offline"
        evidence = Evidence(kind="calculation", label="Known-answer preview input", source_ids=["geo_engine"],
                            artifact="/data/eval/README.md")
        claim = Claim(hotel_id=hotel["id"], characteristic_id=body.id,
                      value={"available": hotel["id"] == "test-2"}, source="geo_engine", method="test_preview",
                      confidence=.95, observed_at=now(), valid_until=now()+timedelta(days=1), evidence=[evidence])
        return [claim], [], "offline"

    monkeypatch.setattr(studio, "execute_preview", preview)
    response = client.post("/api/characteristics/dry-run", json=spec)
    assert response.status_code == 200, response.text
    data = response.json()
    assert (data["total"], data["processed"], data["matched_count"]) == (4, 3, 1)
    assert {row["hotel_id"]: row["matched"] for row in data["results"]} == {
        "test-2": True, "test-3": False, "test-hotel": False,
    }
    assert all(row["hotel_name"] == repo.get("hotels", row["hotel_id"])["name"] for row in data["results"])
    assert {name: repo.all(name) for name in before} == before
