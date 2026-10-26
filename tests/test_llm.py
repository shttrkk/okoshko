import httpx
from pydantic import BaseModel

from backend.llm.client import LLMClient


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

