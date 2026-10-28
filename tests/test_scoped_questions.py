"""Requested model slices are question context, never inherited hotel evidence."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.agents.answer_parser import parse_answer
from backend.agents.interviewer import create_question
from backend.api.hotelier import confirm_answer, router
from backend.contracts import Scope
from backend.db.repository import get_repo
from backend.security import Principal
from backend.tests.test_api_integration import ApiRepo


@pytest.fixture
def repo():
    return ApiRepo()


@pytest.fixture
def client(repo):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_repo] = lambda: repo
    with TestClient(app) as value:
        yield value


def requested(floor=7, side="W", month=6):
    return Scope(type="floor_range", facade=side, floor_min=floor, floor_max=floor, month=month)


def ask(client, scope=None):
    body = {"hotel_id": "test-hotel", "characteristic_id": "sea_view"}
    if scope is not None:
        body["scope"] = scope.model_dump(mode="json") if isinstance(scope, Scope) else scope
    return client.post("/api/questions", json=body)


def test_scoped_ask_and_repeat_keep_one_pending_question_and_real_demand(client, repo):
    repo.insert("search_queries", {"id": "private-guest-search-uuid", "text": "PRIVATE GUEST QUERY",
                                   "parsed": {"must": [{"characteristic_id": "sea_view"}]}, "results": ["test-hotel"]})
    response = ask(client, requested())
    assert response.status_code == 200, response.text
    question = response.json()
    assert question["requested_scopes"] == [requested().model_dump(mode="json")]
    assert all(word in question["text"] and word in question["hypothesis"] for word in ("выбранный номер", "запад", "7-й этаж", "июнь"))
    assert "не подтверждение существования номера" in question["text"]
    assert question["demand_estimate"] == 1 and question["demand_synthetic"] is False
    assert "private-guest-search-uuid" not in response.text and "PRIVATE GUEST QUERY" not in response.text
    repeated = ask(client, requested()).json()
    # Display scope.type does not create a duplicate with the same constraints.
    equivalent = requested().model_copy(update={"type": "hotel"})
    assert ask(client, equivalent).json() == repeated == question
    assert len(repo.find("questions", hotel_id="test-hotel")) == 1


def test_different_slices_accumulate_without_replacing_existing_context(client, repo):
    first = ask(client, requested()).json()
    next_scope = requested(3, "E", 9)
    second = ask(client, next_scope).json()
    assert second["id"] == first["id"] and second["asked_at"] == first["asked_at"]
    assert second["requested_scopes"] == [requested().model_dump(mode="json"), next_scope.model_dump(mode="json")]
    assert second["text"].count("Гость просит проверить выбранный номер") == 1
    assert all(word in second["text"] for word in ("запад", "восток", "июнь", "сентябрь"))
    assert len(repo.find("questions", hotel_id="test-hotel")) == 1


def test_concurrent_scope_retries_share_one_pending_question(repo):
    scopes = [requested(floor, side) for floor, side in [(3, "W"), (4, "E"), (5, "N"), (6, "S")]]
    with ThreadPoolExecutor(max_workers=8) as pool:
        questions = list(pool.map(lambda scope: create_question("test-hotel", "sea_view", repo, scope=scope), scopes * 8))
    assert len({question.id for question in questions}) == 1
    [stored] = repo.find("questions", hotel_id="test-hotel")
    assert len(stored["requested_scopes"]) == 4
    assert {(scope["facade"], scope["floor_min"]) for scope in stored["requested_scopes"]} == {(scope.facade, scope.floor_min) for scope in scopes}


def test_answered_question_is_not_reopened_for_the_next_guest(client, repo):
    old = ask(client, requested()).json()
    repo.upsert("questions", {**old, "status": "answered"})
    new = ask(client, requested(2, "E", 5)).json()
    assert new["id"] != old["id"] and new["status"] == "pending"
    assert new["requested_scopes"] == [requested(2, "E", 5).model_dump(mode="json")]
    assert repo.get("questions", old["id"])["status"] == "answered"


def test_scoped_ask_waits_for_an_in_progress_confirmation_before_choosing_pending(client, repo):
    from backend.api.hotelier import _CONFIRM_LOCK

    old = ask(client, requested()).json()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with _CONFIRM_LOCK:
            future = pool.submit(ask, client, requested(2, "E", 5))
            with pytest.raises(TimeoutError):
                future.result(timeout=.15)
            # This is the final state a confirmation commits before releasing
            # its lock. The waiting ask must create a new pending question.
            repo.upsert("questions", {**old, "status": "answered"})
        new = future.result(timeout=5).json()
    assert new["id"] != old["id"] and new["status"] == "pending"
    assert repo.get("questions", old["id"])["status"] == "answered"


def test_ninth_distinct_scope_is_rejected_explicitly_and_does_not_replace_a_slice(client, repo):
    for floor in range(1, 9):
        assert ask(client, requested(floor)).status_code == 200
    [before] = repo.find("questions", hotel_id="test-hotel")
    rejected = ask(client, requested(9))
    assert rejected.status_code == 409 and "не более 8" in rejected.json()["detail"]
    assert repo.get("questions", before["id"]) == before
    assert ask(client, requested(1)).status_code == 200


@pytest.mark.parametrize("scope", [{"facade": "UP"}, {"floor_min": 0}, {"month": 13},
                                  {"type": "floor_range", "floor_min": 7}, {"floor_min": 8, "floor_max": 7}])
def test_invalid_scope_never_creates_a_question(client, repo, scope):
    assert ask(client, scope).status_code == 422
    assert not repo.all("questions")


def test_unscoped_compatibility_does_not_consume_a_requested_scope(repo):
    plain = create_question("test-hotel", "sea_view", repo)
    assert not plain.requested_scopes and plain.hypothesis is None
    assert "модельный срез" not in plain.text
    assert create_question("test-hotel", "sea_view", repo, scope=Scope()) == plain


def test_bare_yes_never_inherits_the_requested_room_slice(repo):
    question = create_question("test-hotel", "sea_view", repo, scope=requested())
    for candidate in (question, question.model_copy(update={"hypothesis": None})):
        answer = parse_answer(candidate, "Да")
        assert not answer.updates and answer.needs_clarification

    class MisleadingLiveProposal:
        def structured(self, model, messages, fallback, **kwargs):
            proposal = model.model_validate({"raw_text": "Да", "updates": [{"characteristic_id": "sea_view",
                                             "scope": requested().model_dump(mode="json"), "value": {"available": True}, "quote": "Да"}]})
            return proposal, "live"

    answer = parse_answer(question, "Да", llm=MisleadingLiveProposal())
    assert not answer.updates and answer.needs_clarification


def test_general_view_answer_does_not_turn_into_the_requested_floor_or_month(repo):
    question = create_question("test-hotel", "sea_view", repo, scope=requested())
    answer = parse_answer(question, "Вид на море есть.")
    [view] = [update for update in answer.updates if update.characteristic_id == "sea_view"]
    assert view.scope == Scope() and "facade" not in view.value and "min_floor" not in view.value


def test_explicit_answer_scope_and_month_come_from_its_quote_and_survive_confirmation(repo):
    # Request another slice so inherited context would produce a wrong result.
    question = create_question("test-hotel", "sea_view", repo, scope=requested(3, "S", 12))
    raw = "В июне из номеров на западной стороне на 7 этаже вид на море есть."
    answer = parse_answer(question, raw)
    [view] = [update for update in answer.updates if update.characteristic_id == "sea_view"]
    assert view.scope == requested() and view.quote == raw
    assert all(update.scope.month == 6 and "июне" in update.quote for update in answer.updates)
    repo.insert("answers", answer.model_dump(mode="json"))
    wrong = Principal("other", "hotelier", ("another-hotel",), synthetic=False)
    with pytest.raises(HTTPException) as denied:
        confirm_answer(answer.id, repo, principal=wrong)
    assert denied.value.status_code == 403 and not repo.all("claims")
    confirmed = confirm_answer(answer.id, repo, principal=Principal("test-owner", "hotelier", ("test-hotel",), synthetic=False))
    assert confirmed.confirmed and repo.get("questions", question.id)["status"] == "answered"
    claims = [repo.get("claims", identity) for identity in confirmed.created_claim_ids]
    assert claims and all(claim["scope"]["month"] == 6 and not claim["synthetic"] for claim in claims)
    assert all(claim["evidence"][0]["quote"] in raw and claim["evidence"][0]["message_id"] == answer.id for claim in claims)
    facts = repo.find("facts", hotel_id="test-hotel", characteristic_id="sea_view")
    assert facts and all(fact["status"] == "confirmed" and fact["scope"]["month"] == 6 for fact in facts)


@pytest.mark.parametrize("raw", ["В июне и июле вид на море есть.", "С июня вид на море есть.",
                                "Летом вид на море есть.", "До июня вид на море есть.",
                                "В конце июня вид на море есть.", "Во второй половине июня вид на море есть.",
                                "С 01.06 по 30.06 вид на море есть."])
def test_multiple_or_unsupported_periods_require_clarification_instead_of_year_round_claims(repo, raw):
    question = create_question("test-hotel", "sea_view", repo, scope=requested())
    answer = parse_answer(question, raw)
    assert not answer.updates and answer.needs_clarification and "sea_view" in answer.unknown_fields


def test_explicit_month_in_its_own_sentence_stays_in_the_evidence_quote(repo):
    question = create_question("test-hotel", "sea_view", repo, scope=requested(4, "E", 8))
    raw = "В июне. На западной стороне на 7 этаже вид на море есть."
    answer = parse_answer(question, raw)
    [view] = [update for update in answer.updates if update.characteristic_id == "sea_view"]
    assert view.scope == requested() and view.quote == raw


def test_period_guard_does_not_confuse_decimal_amenity_units_with_calendar_dates(repo):
    question = create_question("test-hotel", "pets", repo)
    answer = parse_answer(question, "С собакой до 2.5 кг можно за 500 рублей за ночь.")
    [pets] = [update for update in answer.updates if update.characteristic_id == "pets"]
    assert pets.value["max_weight_kg"] == 2.5 and pets.scope.month is None
