"""Synthetic API integration invariants; these are not real hotel accuracy labels."""
from __future__ import annotations

import copy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from backend.contracts import Evidence, Fact, now
from backend.db.repository import get_repo
from backend.main import app
from backend.tests.test_hotelier import MemoryRepo


class ApiRepo(MemoryRepo):
    def healthy(self):
        return True

    def insert_claims(self, claims):
        return [self.add_claim(claim) for claim in claims]

    def upsert_many(self, table, rows):
        for row in rows:
            self.upsert(table, row)

    def replace_facts(self, hotel_id, rows):
        self.tables["facts"] = {key: row for key, row in self.tables.get("facts", {}).items() if row["hotel_id"] != hotel_id}
        self.upsert_many("facts", rows)


@pytest.fixture
def repo():
    value = ApiRepo()
    for index in range(2, 4):
        value.insert("hotels", {"id": f"test-{index}", "name": f"Synthetic {index}", "lat": 43.58, "lon": 39.72})
    for hotel in value.all("hotels"):
        for cid, observed, month in (("beach_walk", {"available": True, "minutes": 7}, None),
                                     ("climate", {"month": 9, "water_c": 23}, 9)):
            evidence = Evidence(kind="simulation", label="Synthetic integration input", source_ids=["geo_engine"],
                                artifact="/data/eval/README.md", output=observed)
            fact = Fact(id=hotel["id"]+cid, hotel_id=hotel["id"], characteristic_id=cid,
                        scope={"month": month}, value=observed, status="computed", confidence=.7,
                        evidence=[evidence], valid_until=now()+timedelta(days=90), synthetic=True)
            value.insert("facts", fact.model_dump(mode="json"))
    return value


@pytest.fixture
def client(repo):
    app.dependency_overrides[get_repo] = lambda: repo
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()


def test_health_status_public_files_and_catalog(client, repo):
    assert client.get("/health").json() == {"status": "ok"}
    status = client.get("/api/status").json()
    assert status["hotel_count"] == 3 and status["characteristic_count"] == 16
    assert "Сочи" in status["region_name"]
    assert client.get("/api/tools").json()
    assert client.get("/api/characteristics").json()
    assert client.get("/data/hotelier_profiles/parser_eval.json").status_code == 404
    assert client.get("/data/llm_cache.jsonl").status_code == 404
    repo.tables["hotels"] = {}
    assert client.get("/health").status_code == 503


def test_search_logs_trace_and_rejects_other_city(client, repo):
    response = client.post("/api/search", json={"text": "Хочу отель в Казани"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["results"] == [] and result["parsed"]["clarifications"]
    assert "region_out_of_scope" in result["parsed"]["unmapped"]
    assert result["latency_ms"] < 3000
    assert repo.get("agent_runs", result["run_id"])["status"] == "completed"
    assert repo.find("agent_steps", run_id=result["run_id"])
    assert repo.all("search_queries")[0]["run_id"] == result["run_id"]


def test_search_detail_compare_and_quality_labels(client, repo):
    result = client.post("/api/search", json={"text": "До пляжа не больше 10 минут"}).json()
    assert result["results"] and result["results"][0]["verdicts"][0]["evidence_refs"]
    detail = client.get("/api/hotels/test-hotel").json()
    assert detail["facts"] and detail["hotel"]["id"] == "test-hotel"
    assert "не подготовлен" in detail["geo"]["limitations"][0]
    assert client.get("/api/compare?ids=test-hotel").status_code == 422
    assert len(client.get("/api/compare?ids=test-hotel,test-2").json()["hotels"]) == 2
    assert client.get("/api/hotels/missing").status_code == 404
    label = client.post("/api/quality/label", json={"hotel_id": "test-hotel", "characteristic_id": "beach_walk",
                        "value": {"available": True}, "evidence_url": "https://example.org/synthetic-label", "note": "Synthetic test label"})
    assert label.status_code == 200, label.text
    assert label.json()["observed_at"] and repo.get("eval_labels", label.json()["id"])
    assert client.get("/api/quality/label").json()[0]["existing_labels"]
    quality = client.get("/api/quality").json()
    assert quality["manual_label_count"] >= 1
    assert quality["synthetic_parser"]["synthetic"] and quality["synthetic_parser"]["evaluated"] == 62
    assert quality["parser_accuracy"] is None


def test_studio_preview_is_read_only_publish_is_durable_and_searchable(client, repo):
    compiled = client.post("/api/characteristics/compile", json={"text": "Искупаться до завтрака: до 10 минут до пляжа, вода от 20°"})
    assert compiled.status_code == 200, compiled.text
    spec = compiled.json()["spec"]
    previous_claims = copy.deepcopy(repo.all("claims"))
    preview = client.post("/api/characteristics/dry-run", json=spec)
    assert preview.status_code == 200, preview.text
    assert preview.json()["valid"] and len(preview.json()["results"]) == 3
    assert all(row["claims"][0]["value"]["available"] for row in preview.json()["results"])
    assert repo.all("claims") == previous_claims
    assert repo.get("characteristics", "morning_swim") is None
    published = client.post("/api/characteristics/publish", json=spec)
    assert published.status_code == 200, published.text
    run_id = published.json()["run_id"]
    job = repo.get("jobs", run_id)
    assert job["processed"] == 3 and job["status"] == "completed"
    assert repo.get("characteristic_versions", "morning_swim:v1")
    claims = repo.find("claims", characteristic_id="morning_swim")
    assert len(claims) == 3 and all(row["run_id"] == run_id for row in claims)
    assert all(row["evidence"][0]["url"] == "/api/runs/"+run_id for row in claims)
    assert client.post("/api/characteristics/publish", json=spec).json()["run_id"] == run_id
    found = client.post("/api/search", json={"text": "Хочу искупаться до завтрака"}).json()
    assert any(criterion["characteristic_id"] == "morning_swim" for criterion in found["parsed"]["must"]+found["parsed"]["nice"])
    spec["description"] += "; новая версия"
    updated = client.post("/api/characteristics/publish", json=spec)
    assert updated.json()["spec"]["version"] == 2
    assert repo.get("characteristic_versions", "morning_swim:v2")


def test_studio_rejects_untrusted_code_before_claims(client, repo):
    spec = repo.all("characteristics")[0]
    spec["methods"] = [{"tool": "derive", "params": {"expr": "__import__('os').system('id')"}}]
    response = client.post("/api/characteristics/dry-run", json=spec)
    assert response.status_code == 422
    assert not repo.all("claims")


def test_background_run_identity_and_sse_resume(client, repo):
    response = client.post("/api/runs", json={"hotel_id": "test-hotel", "characteristic_ids": ["soundproofing"]})
    assert response.status_code == 200, response.text
    run_id = response.json()["id"]
    run = client.get("/api/runs/"+run_id).json()
    assert run["status"] == "completed" and run["steps"]
    assert len(repo.all("agent_runs")) == 1
    stream = client.get("/api/runs/"+run_id+"/stream")
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert stream.headers["x-accel-buffering"] == "no"
    assert stream.text.count("event: step") == len(run["steps"])
    assert 'event: done\ndata: {"run_id": "'+run_id in stream.text
    resumed = client.get("/api/runs/"+run_id+"/stream", headers={"Last-Event-ID": run["steps"][-1]["id"]})
    assert "event: step" not in resumed.text and "event: done" in resumed.text
    assert client.get("/api/runs/missing/stream").status_code == 404


def test_search_uses_compact_month_projection_and_skips_it_outside_region(client, repo):
    calls = []

    def compact(ids=None, month=None):
        calls.append((ids, month))
        return repo.all("facts")

    repo.search_facts = compact
    response = client.post("/api/search", json={"text": "В сентябре до пляжа не больше 10 минут"})
    assert response.status_code == 200
    assert calls == [(["beach_walk"], 9)]
    response = client.post("/api/search", json={"text": "Отель в Казани"})
    assert response.status_code == 200 and response.json()["results"] == []
    assert len(calls) == 1


def test_new_definition_does_not_reuse_materialized_old_meaning(client, repo):
    from unittest.mock import patch
    spec = client.post("/api/characteristics/compile", json={"text": "Искупаться до завтрака"}).json()["spec"]
    assert client.post("/api/characteristics/publish", json=spec).status_code == 200
    assert all(fact["value"]["available"] for fact in repo.find("facts", characteristic_id="morning_swim"))
    spec["description"] = "Другой температурный порог"
    spec["methods"][0]["params"]["expr"] = "beach_walk.minutes <= 10 and climate.water_c >= 25"
    spec["combine"] = spec["methods"][0]["params"]["expr"]
    # Hold execution so the externally visible period before backfill is tested.
    with patch("backend.api.studio.backfill"):
        response = client.post("/api/characteristics/publish", json=spec)
    assert response.json()["spec"]["version"] == 2
    assert all(fact["status"] == "stale" for fact in repo.find("facts", characteristic_id="morning_swim"))
    search = client.post("/api/search", json={"text": "Искупаться до завтрака"}).json()
    assert all(hit["verdicts"][0]["status"] == "unknown" for hit in search["results"])


def test_background_start_failure_is_durably_failed(client, repo):
    from unittest.mock import patch
    with patch("backend.agents.orchestrator.collect_hotel", side_effect=RuntimeError("synthetic start failure")):
        response = client.post("/api/runs", json={"hotel_id": "test-hotel", "characteristic_ids": ["soundproofing"]})
    assert response.status_code == 200
    run = repo.get("agent_runs", response.json()["id"])
    assert run["status"] == "failed" and run["error"] == "synthetic start failure"
    stream = client.get("/api/runs/"+run["id"]+"/stream")
    assert "event: done" in stream.text and '"status": "failed"' in stream.text
