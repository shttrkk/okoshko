"""«Похожий на <отель>» in free text resolves to a reference hotel without an LLM."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.agents.similarity import reference_hotel
from backend.contracts import Hotel
from backend.db.repository import get_repo
from backend.main import app
from backend.tests.test_api_integration import repo  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
HOTELS = [Hotel.model_validate(row) for row in json.loads((ROOT / "data/seed/hotels.json").read_text())]


@pytest.mark.parametrize(("text", "name"), [
    ("похожий на Brevis apartments", "Brevis apartments"),
    ("найди похожий на бревис", "Brevis apartments"),
    ("как Cascade, но тише", "Cascade"),
    ("хочу как в Марко Поло", "Marco Polo"),
    ("вроде Swissotel", "Swissotel Resort Сочи Камелия"),
    ("похожий на отель Приморская", "Приморская"),
])
def test_reference_hotel_is_found_by_normalized_or_transliterated_name(text, name):
    hotel, ambiguous = reference_hotel(text, HOTELS)
    assert hotel is not None and hotel.name == name and not ambiguous


@pytest.mark.parametrize("text", ["как можно ближе к морю", "похожий на фывапро", "тихий номер с видом на закат"])
def test_ordinary_queries_never_become_similarity_requests(text):
    assert reference_hotel(text, HOTELS) == (None, [])


def test_ambiguous_names_are_returned_for_clarification():
    hotel, ambiguous = reference_hotel("похож на сочи", HOTELS)
    assert hotel is None and set(ambiguous) >= {"Сочи", "Гостиница Сочи"}


def test_search_api_switches_to_profile_similarity(repo):  # noqa: F811
    for fact in repo.all("facts"):
        # Similarity fingerprints use only facts with a supporting provenance chain.
        repo.upsert("facts", {**fact, "supporting_claim_ids": ["claim-" + fact["id"]]})
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        with TestClient(app) as client:
            body = client.post("/api/search", json={"text": "похожий на Synthetic 2"}).json()
    finally:
        app.dependency_overrides.clear()
    parsed = body["parsed"]
    assert parsed["similar_to"] == "test-2" and parsed["similar_to_name"] == "Synthetic 2"
    assert parsed["unmapped"] == [] and parsed["clarifications"] == []
    assert body["results"] and "test-2" not in {hit["hotel"]["id"] for hit in body["results"]}
    assert all(hit["summary"].startswith("Сходство профиля") for hit in body["results"])
