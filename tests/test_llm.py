import copy
import json

import httpx
import pytest
from pydantic import BaseModel

from backend.agents.answer_parser import parse_answer
from backend.agents.query_parser import parse_query
from backend.contracts import CharacteristicSpec, ParsedQuery, Question, SearchRequest
from backend.llm.client import LLMClient
from backend.tests.test_hotelier import MemoryRepo


class Plan(BaseModel):
    tools: list[str]


def test_offline_never_contacts_provider(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "offline")
    monkeypatch.setenv("LLM_API_KEY", "local-test-only")
    def forbidden(request):
        raise AssertionError("Offline must not access the network")
    client = LLMClient(transport=httpx.MockTransport(forbidden))
    value, mode = client.structured(Plan, [], lambda: {"tools": ["viewshed_facade"]})
    assert value.tools == ["viewshed_facade"]
    assert mode == "offline"


def test_json_schema_rejection_cascades_and_validates(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_API_KEY", "local-test-only")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    calls = []
    def provider(request):
        import json
        body = json.loads(request.content)
        calls.append(body.get("response_format", {}).get("type"))
        if calls[-1] == "json_schema":
            return httpx.Response(400, json={"error": "unsupported response format"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"tools":["walk_route"]}'}}]})
    value, mode = LLMClient(transport=httpx.MockTransport(provider)).structured(Plan, [], lambda: {"tools": []})
    assert calls == ["json_schema", "json_object"]
    assert value.tools == ["walk_route"] and mode == "live"


def test_provider_failure_uses_validated_fallback(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_API_KEY", "local-test-only")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    def provider(request):
        return httpx.Response(503, json={"error": "busy"})
    value, mode = LLMClient(transport=httpx.MockTransport(provider)).structured(Plan, [], lambda: {"tools": ["poi_nearby"]})
    assert value.tools == ["poi_nearby"] and mode == "offline"


def live_client(monkeypatch, payload, *, repo=None, action=False, content=None):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_API_KEY", "local-test-only")
    monkeypatch.setenv("LLM_MODEL", "synthetic-security-provider")
    calls = []
    def provider(request):
        calls.append(request.url.path)
        message = {"content": json.dumps(payload, ensure_ascii=False) if content is None else content}
        if action:
            message["tool_calls"] = [{"type": "function", "function": {"name": "shell", "arguments": '{"command":"id"}'}}]
        return httpx.Response(200, json={"choices": [{"message": message}]})
    return LLMClient(repo, transport=httpx.MockTransport(provider)), calls


def catalogue():
    return [CharacteristicSpec.model_validate(row) for row in MemoryRepo().all("characteristics")]


@pytest.mark.parametrize("attack", ["extra_source", "extra_confidence", "tool_call"])
def test_live_query_actions_and_authority_fields_fail_to_local_plan(monkeypatch, attack):
    payload = {"text": "provider", "must": [{"characteristic_id": "sea_view", "label": "provider"}]}
    if attack == "extra_source":
        payload["source"] = "geo_engine"
    elif attack == "extra_confidence":
        payload["must"][0]["confidence"] = 1
    repo = MemoryRepo()
    llm, calls = live_client(monkeypatch, payload, repo=repo, action=attack == "tool_call")
    result = parse_query(SearchRequest(text="До пляжа не больше 10 минут"), catalogue(), llm=llm)
    assert result.mode == "offline" and [criterion.characteristic_id for criterion in result.must] == ["beach_walk"]
    assert not repo.all("llm_cache") and not repo.all("claims")
    if attack == "tool_call":
        assert len(calls) == 1


def test_live_query_foreign_catalogue_id_does_not_become_a_condition(monkeypatch):
    payload = {"text": "provider", "must": [{"characteristic_id": "foreign_hotel_secret", "label": "Unknown feature"}],
               "check_in": "2030-01-01", "check_out": "2030-01-02", "month": 1}
    llm, _ = live_client(monkeypatch, payload)
    result = parse_query(SearchRequest(text="бассейн"), catalogue(), llm=llm)
    assert not result.must and "Unknown feature" in result.unmapped
    assert result.check_in is None and result.check_out is None and result.month == 9


def test_live_query_cannot_remove_explicit_dates_limits_scope_or_route_flags(monkeypatch):
    request = SearchRequest(text="До пляжа не больше 10 минут без лестниц и переходов, номер на запад с 5 этажа",
                            check_in="2027-09-01", check_out="2027-09-07")
    baseline = parse_query(request, catalogue())
    assert any(criterion.no_stairs and criterion.no_crossings for criterion in baseline.must)
    payload = {"text": "provider", "must": [], "month": 1, "check_in": "2030-01-01", "check_out": "2030-01-02"}
    llm, _ = live_client(monkeypatch, payload)
    result = parse_query(request, catalogue(), llm=llm)
    assert result.mode == "live" and result.must == baseline.must and result.nice == baseline.nice
    assert (result.check_in, result.check_out, result.month, result.facade, result.min_floor) == (
        baseline.check_in, baseline.check_out, baseline.month, baseline.facade, baseline.min_floor)


@pytest.mark.parametrize("attack", ["source", "question_id", "confirmed", "confidence", "nested_source", "nested_confidence", "foreign_cid", "invented_quote", "tool_call"])
def test_live_answer_rejects_authority_foreign_fields_and_nonquotes(monkeypatch, attack):
    text = "С собакой можно."
    payload = {"raw_text": text, "updates": [{"characteristic_id": "pets", "value": {"available": True}, "quote": text}]}
    if attack in {"source", "question_id", "confirmed"}:
        payload[attack] = True if attack == "confirmed" else "foreign"
    elif attack == "confidence":
        payload["updates"][0]["confidence"] = 1
    elif attack.startswith("nested_"):
        payload["updates"][0]["value"][attack.removeprefix("nested_")] = "provider-selected"
    elif attack == "foreign_cid":
        # A characteristic hotels cannot answer (beach walking time now can be answered).
        payload["updates"][0]["characteristic_id"] = "air_quality"
    elif attack == "invented_quote":
        payload["updates"][0]["quote"] = "С кошкой нельзя."
    repo = MemoryRepo()
    llm, _ = live_client(monkeypatch, payload, repo=repo, action=attack == "tool_call")
    question = Question(hotel_id="owned-hotel", characteristic_ids=["pets"], text="Уточните")
    result = parse_answer(question, text, synthetic=True, llm=llm)
    assert result.parser == "offline" and result.question_id == question.id
    assert not result.confirmed and result.synthetic and result.updates[0].confidence == .85
    assert result.updates[0].value == {"available": True, "species": ["dog"]} and result.updates[0].quote in text
    assert not repo.all("claims") and not repo.all("llm_cache")


def test_valid_live_answer_cannot_replace_local_quote_meaning_or_clarification(monkeypatch):
    payload = {"raw_text": "Собак не принимаем.", "updates": [{"characteristic_id": "pets", "value": {"available": False},
        "quote": "Собак не принимаем."}], "needs_clarification": "Provider-selected instruction"}
    llm, _ = live_client(monkeypatch, payload)
    question = Question(hotel_id="owned-hotel", characteristic_ids=["pets"], text="Уточните")
    result = parse_answer(question, "С собакой можно.", llm=llm)
    assert result.parser == "live" and result.updates[0].value == {"available": True, "species": ["dog"]}
    assert result.updates[0].quote in result.raw_text and result.needs_clarification is None


@pytest.mark.parametrize("task", ["query", "answer"])
@pytest.mark.parametrize("escaped", [False, True])
def test_schema_valid_provider_canary_never_returns_caches_logs_or_repairs(monkeypatch, caplog, task, escaped):
    canary = "synthetic-canary-output-boundary-" + "Q" * 24
    payload = {"text": "safe query", "clarifications": [canary]} if task == "query" else {
        "raw_text": "Не знаю.", "needs_clarification": canary}
    content = json.dumps(payload)
    if escaped:
        content = content.replace(canary, "".join(f"\\u{ord(char):04x}" for char in canary))
    repo = MemoryRepo()
    llm, calls = live_client(monkeypatch, payload, repo=repo, content=content)
    llm.api_key = canary
    if task == "query":
        result = parse_query(SearchRequest(text="бассейн"), catalogue(), llm=llm)
    else:
        result = parse_answer(Question(hotel_id="owned", characteristic_ids=["pets"], text="Уточните"), "Не знаю.", llm=llm)
    assert canary not in result.model_dump_json() and canary not in caplog.text
    assert not repo.all("llm_cache") and len(calls) == 1


def test_schema_valid_canary_in_cache_is_not_returned(monkeypatch, caplog):
    repo = MemoryRepo()
    llm, _ = live_client(monkeypatch, {"text": "safe", "clarifications": ["safe"]}, repo=repo)
    def fallback():
        return ParsedQuery(text="safe fallback")
    llm.structured(ParsedQuery, [], fallback, task="cache-boundary")
    cached = copy.deepcopy(repo.all("llm_cache")[0])
    cached["value"]["clarifications"] = [llm.api_key]
    repo.upsert("llm_cache", cached)
    llm.mode = "offline"
    value, mode = llm.structured(ParsedQuery, [], fallback, task="cache-boundary")
    assert mode == "offline" and llm.api_key not in value.model_dump_json() and llm.api_key not in caplog.text

