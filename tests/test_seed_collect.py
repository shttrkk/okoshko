"""A fresh stand ships one real, completed collection trace."""
import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from backend.contracts import CharacteristicSpec
from backend.db.repository import get_repo
from backend.main import app
from backend.seed import SEED_COLLECT_HOTEL, seed_collect_run
from backend.tests.test_api_integration import ApiRepo

ROOT = Path(__file__).resolve().parents[1]


def stand():
    repo = ApiRepo()
    hotel = next(row for row in json.loads((ROOT / "data/seed/hotels.json").read_text()) if row["id"] == SEED_COLLECT_HOTEL)
    repo.insert("hotels", hotel)
    for path in sorted((ROOT / "data/characteristics").glob("*.yaml")):
        repo.upsert("characteristics", CharacteristicSpec.model_validate(yaml.safe_load(path.read_text())).model_dump(mode="json"))
    return repo


def test_seed_collect_runs_once_with_planner_tools_resolver_and_interviewer():
    repo = stand()
    run = seed_collect_run(repo)
    assert run is not None and run.status == "completed" and run.trigger == "seed" and run.hotel_id == SEED_COLLECT_HOTEL
    agents = [step.agent for step in run.steps]
    assert {"planner", "collector", "resolver", "interviewer"} <= set(agents)
    assert agents[-2:] == ["resolver", "interviewer"]
    assert seed_collect_run(repo) is None  # idempotent across restarts


def test_runs_listing_keeps_the_latest_collection_visible_after_many_searches():
    repo = stand()
    seed_collect_run(repo)
    for index in range(60):
        repo.insert("agent_runs", {"id": f"search-{index}", "kind": "search", "status": "completed", "trigger": "user",
                                   "llm_mode": "offline", "started_at": f"2099-01-01T00:{index:02d}:00Z", "steps": []})
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        with TestClient(app) as client:
            runs = client.get("/api/runs").json()
    finally:
        app.dependency_overrides.clear()
    assert len(runs) == 51 and runs[-1]["kind"] == "collect" and runs[-1]["steps"]
