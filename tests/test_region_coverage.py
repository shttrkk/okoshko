"""Characteristics without any regional data are explicit, not 250 unknowns."""
from datetime import timedelta
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from backend.contracts import CharacteristicSpec, Evidence, Fact, now
from backend.coverage import region_coverage
from backend.db.repository import get_repo
from backend.main import app
from backend.tests.test_api_integration import repo  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = [CharacteristicSpec.model_validate(yaml.safe_load(path.read_text()))
           for path in sorted((ROOT / "data/characteristics").glob("*.yaml"))]


def row(hotel_id, cid, value, status="computed"):
    return Fact(hotel_id=hotel_id, characteristic_id=cid, value=value, status=status, confidence=.6, supporting_claim_ids=["c"],
                evidence=[Evidence(kind="calculation", label="x", source_ids=["geo_engine"], artifact="/data/x.json")],
                valid_until=now() + timedelta(days=30)).model_dump(mode="json")


def test_open_data_without_values_is_no_data_but_hotel_answerable_waits_for_hotels():
    rows = [row("a", "beach_walk", {"available": True, "minutes": 5}), row("a", "connectivity", None, "unknown"),
            row("a", "soundproofing", None, "unknown")]
    coverage = region_coverage(rows, CATALOG, 2)
    assert coverage["beach_walk"] == {"known_hotels": 1, "hotels": 2, "status": "partial", "note": None}
    assert coverage["connectivity"]["status"] == "no_data" and coverage["connectivity"]["note"] == "нет данных в регионе"
    assert coverage["building_history"]["status"] == "no_data"
    assert coverage["soundproofing"]["status"] == "awaiting_hotels"


def test_search_moves_no_data_conditions_out_of_ranking(repo):  # noqa: F811
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        with TestClient(app) as client:
            body = client.post("/api/search", json={"text": "быстрый интернет для работы, до пляжа 10 минут"}).json()
            status = client.get("/api/status").json()
    finally:
        app.dependency_overrides.clear()
    parsed = body["parsed"]
    assert [c["characteristic_id"] for c in parsed["no_data"]] == ["connectivity"]
    assert "connectivity" not in {c["characteristic_id"] for c in [*parsed["must"], *parsed["nice"]]}
    assert parsed["clarifications"][0].startswith("В демо-регионе нет данных: «Связь для работы»")
    assert all(v["characteristic_id"] != "connectivity" for hit in body["results"] for v in hit["verdicts"])
    assert "connectivity" in status["no_data_characteristics"] and status["region_coverage"]["beach_walk"]["known_hotels"] == 3
