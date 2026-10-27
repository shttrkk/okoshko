"""Panel regressions: conservative extraction, Russian questions and durable API."""
import copy
import re
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.errors import UniqueViolation

from backend.agents.answer_parser import parse_answer
from backend.agents.interviewer import create_question, generate_questions
from backend.api.hotelier import router
from backend.contracts import Fact, Question, Scope, now
from backend.db.repository import Repository, get_repo
from backend.tests.dbapi_fakes import FakeDatabase
from backend.tests.test_api_integration import ApiRepo

PETS = "Собак до 5 кг принимаем, кошек нет. Доплата 500 руб в сутки, в июле и августе с животными не заселяем"
ROOMS = "Номера 201–210 и 301–310 смотрят на море, у всех балкон. 211–220 во двор, без балкона. На первом этаже номеров нет."


def question(cid="pets", **extra):
    return Question(hotel_id="test-hotel", characteristic_ids=[cid], text="Уточните условия", **extra)


def test_unknown_is_a_complete_honest_answer_and_partial_needs_details():
    answer = parse_answer(question("quiet_night"), "Не знаю")
    assert answer.disposition == "unknown" and not answer.updates and answer.needs_clarification is None
    assert answer.unknown_fields == ["quiet_night"]
    partial = parse_answer(question(), "Частично")
    assert partial.disposition == "clarification" and partial.needs_clarification and not partial.updates


def test_bare_no_accepts_unscoped_boolean_but_never_inherits_requested_scopes():
    plain = parse_answer(question("soundproofing", hypothesis="По расчёту есть сведения об окнах"), "Нет")
    assert plain.disposition == "parsed" and plain.updates[0].value == {"available": False}
    assert plain.updates[0].scope == Scope()
    for count in (1, 8):
        scopes = [Scope(type="facade", facade="W", floor_min=index+1) for index in range(count)]
        for raw in ("Да", "Нет"):
            answer = parse_answer(question("sea_view", requested_scopes=scopes), raw)
            assert not answer.updates and answer.disposition == "clarification"


def test_partial_pet_policy_keeps_exact_observations_without_all_year_permission():
    answer = parse_answer(question(), PETS)
    [pets] = answer.updates
    assert pets.characteristic_id == "pets" and pets.scope == Scope()
    assert pets.value == {"available": None, "species": ["dog"], "excluded_species": ["cat"],
                          "max_weight_kg": 5, "fee_rub_night": 500}
    assert pets.quote in PETS and "pets.available" in answer.unknown_fields
    assert answer.disposition == "parsed" and "месяц" in answer.needs_clarification


def test_room_groups_preserve_ranges_views_balconies_and_explicit_absent_floor():
    answer = parse_answer(question("room_orientation"), ROOMS)
    [orientation] = answer.updates
    assert orientation.scope == Scope() and orientation.value["available"] is None
    first, second = orientation.value["groups"]
    assert first == {"rooms": [str(n) for n in [*range(201, 211), *range(301, 311)]], "balcony": True, "sea_view": True}
    assert second == {"rooms": [str(n) for n in range(211, 221)], "balcony": False, "courtyard": True}
    assert orientation.value["absent_floors"] == [1]
    assert orientation.quote == ROOMS and not answer.needs_clarification
    assert all("min_floor" not in group and "max_floor" not in group for group in orientation.value["groups"])


def test_group_boundaries_are_required_when_attributes_change_inside_a_clause():
    raw = "Номера 201–210 на море с балконом, 211–220 во двор без балкона."
    answer = parse_answer(question("room_orientation"), raw)
    assert not answer.updates and answer.needs_clarification


def test_overlapping_room_groups_with_opposite_attributes_still_need_clarification():
    raw = "Номера 201–210 с балконом. Номера 205–215 без балкона."
    answer = parse_answer(question("room_orientation"), raw)
    assert not answer.updates and answer.needs_clarification


def test_partial_pet_period_does_not_bypass_the_period_guard_for_other_properties():
    answer = parse_answer(question(), PETS + ". В июле и августе вид на море есть.")
    assert {update.characteristic_id for update in answer.updates} == {"pets"}


def fact(cid, identity, *, scope=None, confidence=.85, status="computed", value=None):
    return Fact(id=identity, hotel_id="test-hotel", characteristic_id=cid, scope=scope or Scope(),
                value=value if value is not None else {"available": True}, confidence=confidence, status=status,
                valid_until=now()+timedelta(days=30)).model_dump(mode="json")


def test_questions_use_catalogue_names_and_scope_and_refresh_old_seed_wording():
    repo = ApiRepo()
    for cid in ("sea_view", "sunset_view", "quiet_night", "railway_noise", "beach_surface", "accessible_route", "morning_sun", "evening_heat"):
        spec = repo.get("characteristics", cid)
        q = create_question("test-hotel", cid, repo)
        assert cid not in q.text
        if spec["scope"] == "hotel":
            assert "номер" not in q.text and "этаж" not in q.text and "сторон" not in q.text
        else:
            assert "сторон" in q.text
    old = repo.find("questions", hotel_id="test-hotel")[0]
    repo.upsert("questions", {**old, "text": "Живая синтетическая демонстрация: свойство sea_view", "hypothesis": "{'available': True}"})
    refreshed = create_question("test-hotel", old["characteristic_ids"][0], repo)
    assert "синтетическ" not in refreshed.text and "{" not in (refreshed.hypothesis or "")
    spec = repo.get("characteristics", "beach_surface")
    repo.upsert("characteristics", {**spec, "display_name": "Пляж у отеля"})
    assert "Пляж у отеля" in create_question("test-hotel", "beach_surface", repo).text


def test_confident_facts_do_not_repeat_but_guest_and_unresolved_facades_remain():
    repo = ApiRepo()
    west = Scope(type="facade", facade="W")
    repo.upsert("facts", fact("quiet_night", "quiet-w", scope=west))
    automatic = create_question("test-hotel", "quiet_night", repo, reason="low_confidence")
    assert "quiet_night" not in {q.characteristic_ids[0] for q in generate_questions("test-hotel", repo)}
    assert repo.get("questions", automatic.id)["status"] == "expired"
    east = Scope(type="facade", facade="E")
    repo.upsert("facts", fact("quiet_night", "quiet-e", scope=east, status="disputed", value={"available": False}))
    pending = next(q for q in generate_questions("test-hotel", repo) if "quiet_night" in q.characteristic_ids)
    assert "запад" in pending.hypothesis and "восток" in pending.hypothesis
    assert "тихо" in pending.hypothesis and "шумно" in pending.hypothesis and "{" not in pending.hypothesis
    repo.upsert("facts", fact("quiet_night", "quiet-e", scope=east, value={"available": False}))
    guest = create_question("test-hotel", "quiet_night", repo, reason="guest")
    assert "Гость просит проверить" in guest.text
    assert guest.id in {q.id for q in generate_questions("test-hotel", repo)}


@pytest.fixture
def durable_client():
    """Exercise actual Repository/DBAPI SQL binding with fake wire transport."""
    database = FakeDatabase()
    tables = {}

    def query(sql, params):
        match = re.search(r'(?:FROM|INTO) "?(\w+)"?', sql)
        assert match, sql
        table = match[1]
        rows = tables.setdefault(table, {})
        if sql.startswith("INSERT INTO"):
            identity, wrapped = params
            value = copy.deepcopy(wrapped.obj)
            if identity in rows and "ON CONFLICT" not in sql:
                raise UniqueViolation("Synthetic duplicate")
            rows[identity] = value
            return []
        if sql.startswith("DELETE FROM"):
            for identity in [key for key, value in rows.items() if value.get("hotel_id") == params[0]]:
                del rows[identity]
            return []
        assert sql.startswith("SELECT payload FROM"), sql
        selected = list(rows.values())
        if "WHERE id=%s" in sql:
            selected = [value for value in selected if value["id"] == params[0]]
        else:
            keys = re.findall(r"payload->>'([^']+)' = %s", sql)
            for key, value in zip(keys, params or [], strict=True):
                selected = [row for row in selected if row.get(key) == value]
        return [(copy.deepcopy(row),) for row in selected]

    database.query_handler = query
    repo = Repository(connection_factory=database.connect)
    repo.insert("hotels", {"id": "test-hotel", "name": "Synthetic test", "lat": 43.58, "lon": 39.72})
    for path in (Path(__file__).resolve().parents[1] / "data/characteristics").glob("*.yaml"):
        repo.insert("characteristics", yaml.safe_load(path.read_text()))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_repo] = lambda: repo
    with TestClient(app) as client:
        token = client.post("/api/hotelier/login", json={"username": "hotelier", "password": "hotelier123"}).json()["token"]
        yield client, repo, {"Authorization": "Bearer " + token}, database
    repo.close()


def preview(client, auth, cid, raw):
    q = client.post("/api/questions", json={"hotel_id": "test-hotel", "characteristic_id": cid})
    assert q.status_code == 200, q.text
    answer = client.post(f'/api/questions/{q.json()["id"]}/answer', json={"text": raw}, headers=auth)
    assert answer.status_code == 200, answer.text
    return q.json(), answer.json()


def test_unknown_confirm_closes_once_without_claims_and_stays_closed_in_cabinet(durable_client):
    client, repo, auth, _ = durable_client
    q, answer = preview(client, auth, "quiet_night", "Не знаю")
    assert answer["disposition"] == "unknown" and not answer["synthetic"] and not repo.all("claims")
    path = f'/api/answers/{answer["id"]}/confirm'
    confirmed = client.post(path, json={}, headers=auth)
    assert confirmed.status_code == 200 and confirmed.json()["confirmed"]
    assert confirmed.json()["created_claim_ids"] == [] and not repo.all("claims") and not repo.all("sources")
    assert client.post(path, json={}, headers=auth).json() == confirmed.json()
    cabinet = client.get("/api/hotelier/test-hotel", headers=auth).json()
    assert q["id"] not in {item["id"] for item in cabinet["questions"]}
    assert cabinet["answers"] == [confirmed.json()]


@pytest.mark.parametrize("cid,raw", [("pets", PETS), ("room_orientation", ROOMS)])
def test_partial_confirm_preserves_quotes_and_synthetic_provenance_and_is_idempotent(durable_client, cid, raw):
    client, repo, auth, database = durable_client
    _, answer = preview(client, auth, cid, raw)
    assert not repo.all("claims") and answer["updates"] and not answer["synthetic"]
    path = f'/api/answers/{answer["id"]}/confirm'
    response = client.post(path, json={}, headers=auth)
    assert response.status_code == 200, response.text
    claims = repo.all("claims")
    assert claims and all(claim["source"] == "hotelier" and not claim["synthetic"] for claim in claims)
    assert all(claim["evidence"][0]["quote"] in raw and claim["evidence"][0]["message_id"] == answer["id"] for claim in claims)
    assert client.post(path, json={}, headers=auth).json() == response.json()
    assert repo.all("claims") == claims
    assert all(claim["value"]["available"] is None for claim in claims)
    assert all(raw not in sql for sql, _ in database.calls)


def test_simulator_stays_demo_and_new_manual_answer_replaces_same_source_scope(durable_client):
    client, repo, auth, _ = durable_client
    q, manual = preview(client, auth, "pets", "С собакой до 5 кг можно за 500 рублей за ночь.")
    simulated = client.post(f'/api/questions/{q["id"]}/simulate', json={}, headers=auth).json()
    assert simulated["synthetic"] and simulated["raw_text"] and not repo.all("claims")
    assert client.post(f'/api/answers/{manual["id"]}/confirm', json={}, headers=auth).status_code == 200
    old = repo.all("claims")[0]
    new_q, correction = preview(client, auth, "pets", "С собакой до 3 кг можно за 700 рублей за ночь.")
    assert new_q["id"] != q["id"]
    result = client.post(f'/api/answers/{correction["id"]}/confirm', json={}, headers=auth)
    assert result.status_code == 200, result.text
    new = repo.get("claims", result.json()["created_claim_ids"][0])
    assert new["supersedes"] == old["id"] and old in repo.all("claims")
    [current] = repo.find("facts", hotel_id="test-hotel", characteristic_id="pets")
    assert current["value"]["max_weight_kg"] == 3 and current["value"]["fee_rub_night"] == 700


def test_old_alternate_preview_cannot_overwrite_a_later_confirmed_answer(durable_client):
    client, repo, auth, _ = durable_client
    q, first = preview(client, auth, "pets", "С собакой до 5 кг можно.")
    stale = client.post(f'/api/questions/{q["id"]}/answer', json={"text": "С собакой до 30 кг можно."}, headers=auth).json()
    assert client.post(f'/api/answers/{first["id"]}/confirm', json={}, headers=auth).status_code == 200
    before = repo.all("claims")
    response = client.post(f'/api/answers/{stale["id"]}/confirm', json={}, headers=auth)
    assert response.status_code == 409 and "новый вопрос" in response.json()["detail"]
    assert repo.all("claims") == before


def test_summary_is_private_tenant_scoped_and_never_generates_or_modifies_questions(durable_client):
    client, repo, auth, _ = durable_client
    own, foreign = "osm-node-11866202895", "osm-way-201474330"
    for hotel_id in (own, foreign):
        repo.insert("hotels", {"id": hotel_id, "name": "Test", "lat": 43.58, "lon": 39.72})
    for identity, hotel_id, status in [("own-1", own, "pending"), ("own-2", own, "answered"), ("foreign-1", foreign, "pending")]:
        repo.insert("questions", question().model_copy(update={"id": identity, "hotel_id": hotel_id, "status": status}).model_dump(mode="json"))
    before = repo.all("questions")
    generation = repo.search_generation()
    assert client.get("/api/hotelier").status_code == 401
    response = client.get("/api/hotelier", headers=auth)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert {row["hotel_id"]: row["pending_count"] for row in response.json()} == {"test-hotel": 0, own: 1, foreign: 1}
    token = client.post("/api/hotelier/login", json={"username": "cascade", "password": "cascade-demo-2026"}).json()["token"]
    scoped = client.get("/api/hotelier", headers={"Authorization": "Bearer " + token})
    assert scoped.json() == [{"hotel_id": own, "pending_count": 1}]
    assert repo.all("questions") == before and repo.search_generation() == generation


def test_queue_orders_by_real_search_demand_before_calculation_uncertainty():
    repo = ApiRepo()
    repo.upsert("facts", fact("pets", "partial-pets", confidence=.99, status="disputed"))
    for index in range(20):
        repo.insert("search_queries", {"id": f"logged-{index}", "text": "Private text", "results": ["test-hotel"],
                                        "parsed": {"must": [{"characteristic_id": "pets"}]}})
    pending = generate_questions("test-hotel", repo)
    assert pending[0].characteristic_ids == ["pets"] and pending[0].demand_estimate == 20
    assert all(not item.demand_synthetic for item in pending)
    assert [item.demand_estimate for item in pending] == sorted([item.demand_estimate for item in pending], reverse=True)

