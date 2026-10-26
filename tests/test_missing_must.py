"""Unavailable regional evidence cannot silently remove a hard requirement."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from backend.agents.query_rules import apply_region_coverage
from backend.contracts import Criterion, Evidence, Fact, ParsedQuery, now
from backend.db.repository import get_repo
from backend.main import app
from backend.tests.test_api_integration import repo  # noqa: F401 (fixture)


def condition(cid, field=None, value=True, op="eq", **kwargs):
    return Criterion(characteristic_id=cid, label=cid, field=field, value=value, op=op, **kwargs)


def add_fact(repository, hotel_id, cid, value):
    fact = Fact(id=f"missing-must-{hotel_id}-{cid}", hotel_id=hotel_id, characteristic_id=cid,
                scope={"facade": "W", "floor_min": 2, "floor_max": 4} if cid == "sea_view" else {},
                value=value, confidence=.9, status="computed", supporting_claim_ids=["synthetic-input"],
                evidence=[Evidence(kind="simulation", label="Synthetic missing-must input",
                                   source_ids=["geo_engine"], artifact="/data/eval/README.md")],
                valid_until=now() + timedelta(days=30), synthetic=True)
    repository.upsert("facts", fact.model_dump(mode="json"))


@pytest.fixture
def client(repo):  # noqa: F811
    for fact in repo.all("facts"):
        repo.upsert("facts", {**fact, "supporting_claim_ids": ["synthetic-input"]})
    for hotel in repo.all("hotels"):
        repo.upsert("hotels", {**hotel, "floors": 4})
        add_fact(repo, hotel["id"], "sea_view", {"available": hotel["id"] != "test-3"})
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        with TestClient(app) as value:
            yield value
    finally:
        app.dependency_overrides.clear()


def search(client, parsed=None, **kwargs):
    if parsed is not None:
        kwargs["parsed"] = parsed.model_dump(mode="json") if isinstance(parsed, ParsedQuery) else parsed
    response = client.post("/api/search", json=kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def test_sea_and_missing_hard_never_claim_all(client):
    body = search(client, text="вид на море, быстрый интернет")
    assert [c["characteristic_id"] for c in body["parsed"]["no_data_must"]] == ["connectivity"]
    assert body["parsed"]["no_data_must"] == body["parsed"]["no_data"]
    hits = {hit["hotel"]["id"]: hit for hit in body["results"]}
    assert (hits["test-2"]["group"], hits["test-2"]["must_satisfied"], hits["test-2"]["must_total"]) == ("almost", 1, 2)
    assert (hits["test-3"]["group"], hits["test-3"]["must_satisfied"], hits["test-3"]["must_total"]) == ("unknown", 0, 2)
    assert all(v["characteristic_id"] != "connectivity" for hit in body["results"] for v in hit["verdicts"])


def test_only_missing_hard_is_unknown_not_browse(client):
    body = search(client, text="быстрый интернет")
    assert body["results"]
    assert {hit["group"] for hit in body["results"]} == {"unknown"}
    assert all(hit["must_total"] == 1 and hit["must_satisfied"] == 0 and not hit["verdicts"] for hit in body["results"])
    assert all(hit["summary"].startswith("Полное совпадение нельзя подтвердить") for hit in body["results"])
    assert all("обзор" not in c for c in body["parsed"]["clarifications"])


def test_missing_nice_does_not_demote_all(client):
    body = search(client, ParsedQuery(text="", must=[condition("sea_view")],
                                      nice=[condition("connectivity", "download_mbps", 30, "gte")]))
    assert body["parsed"]["no_data"] and not body["parsed"]["no_data_must"]
    hit = next(hit for hit in body["results"] if hit["hotel"]["id"] == "test-2")
    assert (hit["group"], hit["must_satisfied"], hit["must_total"]) == ("all", 1, 1)


def test_same_trait_uses_coverage_of_each_requested_field(client):
    body = search(client, ParsedQuery(text="", must=[condition("climate", "water_c", 20, "gte"),
                                                     condition("climate", "sunshine_hours", 8, "gte")]))
    assert [c["field"] for c in body["parsed"]["must"]] == ["water_c"]
    assert [c["field"] for c in body["parsed"]["no_data_must"]] == ["sunshine_hours"]
    assert all((hit["group"], hit["must_satisfied"], hit["must_total"]) == ("almost", 1, 2) for hit in body["results"])


def test_known_connectivity_flag_does_not_invent_speed(client, repo):  # noqa: F811
    add_fact(repo, "test-2", "connectivity", {"available": True})
    body = search(client, ParsedQuery(text="", must=[condition("connectivity", "available"),
                                                     condition("connectivity", "download_mbps", 30, "gte")]))
    assert [c["field"] for c in body["parsed"]["must"]] == ["available"]
    assert [c["field"] for c in body["parsed"]["no_data_must"]] == ["download_mbps"]
    assert all(hit["group"] != "all" for hit in body["results"])


def test_roundtrip_preserves_hardness_after_presentation_edits(client):
    first = search(client, text="вид на море, быстрый интернет")
    parsed = first["parsed"]
    parsed["no_data"][0].update(label="Интернет от 30", weight=3)
    second = search(client, parsed)
    assert second["parsed"]["no_data_must"] == second["parsed"]["no_data"]
    assert second["parsed"]["no_data_must"][0]["weight"] == 3
    assert [(h["group"], h["must_satisfied"], h["must_total"]) for h in second["results"]] == [
        (h["group"], h["must_satisfied"], h["must_total"]) for h in first["results"]]
    assert len([c for c in second["parsed"]["clarifications"] if c.startswith("В демо-регионе нет данных:")]) == 1


@pytest.mark.parametrize("edit", ["delete", "demote"])
def test_chip_deletion_or_demotion_overrides_stale_hard_marker(client, edit):
    parsed = search(client, text="вид на море, быстрый интернет")["parsed"]
    missing = parsed["no_data"].pop()
    if edit == "demote":
        parsed["nice"].append({**missing, "label": "Просто пожелание", "weight": 2})
    body = search(client, parsed)
    assert not body["parsed"]["no_data_must"]
    assert bool(body["parsed"]["no_data"]) == (edit == "demote")
    assert next(hit for hit in body["results"] if hit["hotel"]["id"] == "test-2")["group"] == "all"


def test_explicit_promotion_overrides_stale_nice_marker(client):
    parsed = search(client, ParsedQuery(text="", nice=[condition("connectivity", "download_mbps", 30, "gte")]))["parsed"]
    parsed["must"].append({**parsed["no_data"][0], "label": "Теперь обязательно"})
    body = search(client, parsed)
    assert body["parsed"]["no_data_must"] == body["parsed"]["no_data"]
    assert all(hit["group"] == "unknown" and hit["must_total"] == 1 for hit in body["results"])


@pytest.mark.parametrize("required", [True, False])
def test_new_coverage_restores_original_priority_and_loads_missing_trait(client, repo, required):  # noqa: F811
    parsed = ParsedQuery(text="", must=[condition("sea_view")])
    (parsed.must if required else parsed.nice).append(condition("connectivity", "download_mbps", 30, "gte"))
    first = search(client, parsed)
    calls = []

    def compact(ids=None, month=None):
        calls.append(ids)
        return [row for row in repo.all("facts") if ids is None or row["characteristic_id"] in ids]

    repo.search_facts = compact
    add_fact(repo, "test-2", "connectivity", {"download_mbps": 50})
    body = search(client, first["parsed"])
    assert "connectivity" in calls[0]
    assert not body["parsed"]["no_data"] and not body["parsed"]["no_data_must"]
    restored = body["parsed"]["must" if required else "nice"]
    assert any(c["characteristic_id"] == "connectivity" for c in restored)
    assert all(not c.startswith("В демо-регионе нет данных:") for c in body["parsed"]["clarifications"])
    hit = next(hit for hit in body["results"] if hit["hotel"]["id"] == "test-2")
    assert (hit["group"], hit["must_total"]) == ("all", 2 if required else 1)


def test_semantic_markers_keep_distinct_thresholds_and_scope():
    base = condition("connectivity", "download_mbps", 30, "gte")
    higher = base.model_copy(update={"value": 50})
    scoped = condition("connectivity", "download_mbps", 30, "gte", scope={"facade": "W"})
    parsed = ParsedQuery(text="", no_data=[base.model_copy(update={"label": "renamed", "weight": 4}), higher, scoped],
                         no_data_must=[base, scoped])
    apply_region_coverage(parsed, {"connectivity": {"status": "no_data"}})
    assert len(parsed.no_data) == 3 and len(parsed.no_data_must) == 2
    apply_region_coverage(parsed, {"connectivity": {"status": "available"}})
    assert len(parsed.must) == 2 and len(parsed.nice) == 1
    assert not parsed.no_data and not parsed.no_data_must


@pytest.mark.parametrize("sea_required", [True, False])
def test_constrained_similarity_keeps_alternatives_without_claiming_all(client, sea_required):
    hard = [condition("connectivity", "download_mbps", 30, "gte")]
    if sea_required:
        hard.append(condition("sea_view"))
    body = search(client, ParsedQuery(text="", must=hard, similar_to="test-hotel"))
    assert body["results"] and "test-hotel" not in {hit["hotel"]["id"] for hit in body["results"]}
    assert {hit["group"] for hit in body["results"]} == ({"almost"} if sea_required else {"unknown"})
    assert all(hit["must_total"] == 1 + sea_required for hit in body["results"])
    if sea_required:
        assert {hit["hotel"]["id"] for hit in body["results"]} == {"test-2"}


def test_missing_nice_does_not_remove_similarity_candidates(client):
    body = search(client, ParsedQuery(text="", must=[condition("sea_view")],
                                      nice=[condition("connectivity", "download_mbps", 30, "gte")],
                                      similar_to="test-hotel"))
    assert {hit["hotel"]["id"] for hit in body["results"]} == {"test-2"}
    assert all(hit["group"] == "all" and hit["must_total"] == 1 for hit in body["results"])
