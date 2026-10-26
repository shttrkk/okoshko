"""Adversarial synthetic boundary checks; no external provider or shared DB."""
from __future__ import annotations

import base64
import copy
import json
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jsonschema import ValidationError as SchemaValidationError

from backend.agents.answer_parser import parse_answer
from backend.agents.interviewer import create_question
from backend.agents.spec_compiler import validate_spec
from backend.api.changes import router as changes_router
from backend.api.hotelier import confirm_answer
from backend.api.hotelier import router as hotelier_router
from backend.api.passports import create_passport
from backend.api.passports import router as passports_router
from backend.api.runs import router as runs_router
from backend.contracts import (
    AgentRun,
    AgentStep,
    ChangeEvent,
    CharacteristicSpec,
    PassportCreate,
    PassportItem,
    ToolCall,
)
from backend.db.repository import get_repo
from backend.security import DEMO_ACCOUNTS, DEMO_ADMIN, SESSION_COOKIE, Principal, issue_token, read_token
from backend.tests.test_hotelier import MemoryRepo
from backend.tools.registry import Registry

A = "osm-node-11866202895"
B = "osm-way-201474330"


@pytest.fixture
def security_context():
    repo = MemoryRepo()
    repo.tables["hotels"] = {}
    for hotel_id in (A, B):
        repo.insert("hotels", {"id": hotel_id, "name": "Synthetic security fixture", "lat": 43.58, "lon": 39.72})
    application = FastAPI()
    for router in (hotelier_router, passports_router, runs_router, changes_router):
        application.include_router(router)
    application.dependency_overrides[get_repo] = lambda: repo
    with TestClient(application) as client:
        yield client, repo


def credentials(client, username):
    response = client.post("/api/hotelier/login", json={"username": username, "password": DEMO_ACCOUNTS[username][0]})
    assert response.status_code == 200
    value = response.json()
    return {"Authorization": "Bearer " + value["token"]}, value


def passport(repo, hotel_id):
    return create_passport(PassportCreate(hotel_id=hotel_id, guest_name="Synthetic guest",
        items=[PassportItem(characteristic_id="pets", text="С собакой")]), repo)


def test_demo_roles_signed_scope_and_invalid_credentials(security_context, monkeypatch):
    client, _ = security_context
    for name, role, hotel_ids in (("hotelier", "demo_admin", []), ("cascade", "hotelier", [A]), ("brevis", "hotelier", [B])):
        _, value = credentials(client, name)
        assert value["role"] == role and value["hotel_ids"] == hotel_ids and value["synthetic"] is True
        principal = read_token(value["token"])
        assert principal.synthetic is True
        encoded, signature = value["token"].split(".")
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        payload.update(role="demo_admin", hotel_ids=[A, B])
        altered = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=") + "." + signature
        with pytest.raises(HTTPException) as error:
            read_token(altered)
        assert error.value.status_code == 401
    assert client.post("/api/hotelier/login", json={"username": "cascade", "password": DEMO_ACCOUNTS["brevis"][0]}).status_code == 401
    token = issue_token(DEMO_ACCOUNTS["cascade"][1])
    monkeypatch.setattr("backend.security.time.time", lambda: 10**12)
    with pytest.raises(HTTPException) as error:
        read_token(token)
    assert error.value.status_code == 401


def test_tenant_cannot_read_or_write_foreign_cabinet_answer_or_passport(security_context):
    client, repo = security_context
    auth, _ = credentials(client, "cascade")
    question = create_question(B, "pets", repo)
    answer = parse_answer(question, "С собакой можно.", synthetic=True)
    repo.insert("answers", answer.model_dump(mode="json"))
    foreign = passport(repo, B)
    before = copy.deepcopy(repo.tables)
    requests = [
        ("get", "/api/hotelier/" + B, None),
        ("post", f"/api/questions/{question.id}/answer", {"text": "С собакой можно.", "synthetic": False}),
        ("post", f"/api/questions/{question.id}/simulate", {}),
        ("post", f"/api/answers/{answer.id}/confirm", {}),
        ("post", f"/api/passports/{foreign.id}/action", {"action": "alternative", "text": "Foreign update"}),
    ]
    for method, url, body in requests:
        response = client.request(method, url, headers=auth, json=body)
        assert response.status_code == 403, (method, url, response.text)
        assert repo.tables == before
    with pytest.raises(HTTPException) as error:
        confirm_answer(answer.id, repo, principal=DEMO_ACCOUNTS["cascade"][1])
    assert error.value.status_code == 403 and repo.tables == before
    assert client.get("/api/hotelier/" + A, headers=auth).status_code == 200


def test_public_demo_answer_cannot_claim_real_hotel_confirmation(security_context):
    client, repo = security_context
    auth, _ = credentials(client, "cascade")
    question = create_question(A, "pets", repo)
    preview = client.post(f"/api/questions/{question.id}/answer", headers=auth,
                          json={"text": "С собакой можно.", "synthetic": False}).json()
    assert preview["synthetic"] is True and preview["confirmed"] is False and not repo.all("claims")
    response = client.post(f'/api/answers/{preview["id"]}/confirm', headers=auth, json={})
    assert response.status_code == 200 and response.json()["synthetic"] is True
    claim = repo.all("claims")[0]
    assert claim["source"] == "hotelier_simulator" and claim["synthetic"] is True and claim["confidence"] == .85
    assert claim["evidence"][0]["quote"] in preview["raw_text"]
    assert repo.all("facts")[0]["status"] == "computed"


def test_answer_request_cannot_supply_claim_authority_or_foreign_ids(security_context):
    client, repo = security_context
    auth, _ = credentials(client, "cascade")
    question = create_question(A, "pets", repo)
    before = copy.deepcopy(repo.tables)
    for field, value in (("hotel_id", B), ("characteristic_id", "sea_view"), ("source", "geo_engine"),
                         ("confidence", 1), ("confirmed", True), ("updates", [])):
        response = client.post(f"/api/questions/{question.id}/answer", headers=auth,
                               json={"text": "С собакой можно.", field: value})
        assert response.status_code == 422 and repo.tables == before


def test_future_trusted_principal_is_explicit_and_still_scoped(security_context):
    _, repo = security_context
    question = create_question(A, "pets", repo)
    answer = parse_answer(question, "С собакой можно.")
    repo.insert("answers", answer.model_dump(mode="json"))
    principal = Principal("in-memory-trusted-adapter-fixture", "hotelier", (A,), synthetic=False)
    confirm_answer(answer.id, repo, principal=principal)
    claim = repo.all("claims")[0]
    assert claim["source"] == "hotelier" and claim["synthetic"] is False and claim["confidence"] == .85
    assert repo.all("facts")[0]["status"] == "confirmed"


@pytest.mark.parametrize("tamper", ["foreign_characteristic", "confidence", "nested_source", "nested_confidence", "invented_value", "quote"])
def test_confirm_rechecks_stored_preview_before_any_durable_write(security_context, tamper):
    _, repo = security_context
    question = create_question(A, "pets", repo)
    answer = parse_answer(question, "С собакой можно.", synthetic=True)
    value = answer.model_dump(mode="json")
    update = value["updates"][0]
    if tamper == "foreign_characteristic":
        update["characteristic_id"] = "sea_view"
    elif tamper == "confidence":
        update["confidence"] = 1
    elif tamper.startswith("nested_"):
        update["value"][tamper.removeprefix("nested_")] = "provider-selected"
    elif tamper == "invented_value":
        update["value"]["available"] = False
    else:
        update["quote"] = "можно"  # Still verbatim, but not the parser's supported extraction.
    repo.insert("answers", value)
    before = copy.deepcopy(repo.tables)
    with pytest.raises(HTTPException) as error:
        confirm_answer(answer.id, repo, principal=DEMO_ADMIN)
    assert error.value.status_code == 422 and repo.tables == before


def test_passport_listing_is_private_and_omitting_hotel_never_expands_scope(security_context):
    client, repo = security_context
    own, foreign = passport(repo, A), passport(repo, B)
    assert UUID(own.id) and UUID(foreign.id)
    assert client.get("/api/passports").status_code == 401
    assert client.get("/api/passports/" + foreign.id).status_code == 200  # Guest capability by UUID only.
    auth, _ = credentials(client, "cascade")
    assert [value["id"] for value in client.get("/api/passports", headers=auth).json()] == [own.id]
    assert client.get("/api/passports?hotel_id=" + B, headers=auth).status_code == 403
    assert client.get("/api/passports/" + foreign.id, headers=auth).status_code == 403
    assert client.get("/api/passports/" + own.id, headers=auth).status_code == 200
    updated = client.post(f"/api/passports/{own.id}/action", headers=auth, json={"action": "alternative", "text": "Synthetic own alternative"})
    assert updated.status_code == 200 and updated.json()["status"] == "alternative"
    assert repo.get("expectation_passports", foreign.id)["status"] == "sent"
    admin, _ = credentials(client, "hotelier")
    assert len(client.get("/api/passports", headers=admin).json()) == 2


def test_interview_traces_and_sse_are_private_with_scoped_cookie(security_context):
    client, repo = security_context
    interview = AgentRun(kind="interview", hotel_id=B, status="waiting")
    interview.steps = [AgentStep(run_id=interview.id, seq=0, agent="answer_parser", type="extraction", name="Private preview",
        input={"raw_text": "Synthetic private answer"}, output={}, rationale="Synthetic security fixture")]
    repo.insert("agent_runs", interview.model_dump(mode="json"))
    public = AgentRun(kind="collect", hotel_id=B, status="completed")
    repo.insert("agent_runs", public.model_dump(mode="json"))
    assert [value["id"] for value in client.get("/api/runs").json()] == [public.id]
    assert client.get(f"/api/runs/{interview.id}").status_code == 401
    assert client.get(f"/api/runs/{interview.id}/stream").status_code == 401
    assert client.get(f"/api/runs/{public.id}/stream").status_code == 200
    auth, _ = credentials(client, "cascade")
    assert client.get(f"/api/runs/{interview.id}", headers=auth).status_code == 403
    assert client.get(f"/api/runs/{interview.id}/stream", headers=auth).status_code == 403
    assert [value["id"] for value in client.get("/api/runs", headers=auth).json()] == [public.id]
    credentials(client, "brevis")
    cookie = next(cookie for cookie in client.cookies.jar if cookie.name == SESSION_COOKIE)
    assert cookie.path == "/api/runs" and cookie.has_nonstandard_attr("HttpOnly") and cookie.get_nonstandard_attr("SameSite") == "strict"
    stream = client.get(f"/api/runs/{interview.id}/stream")
    assert stream.status_code == 200 and "Synthetic private answer" in stream.text
    assert client.get("/api/hotelier/" + B).status_code == 401  # Cookie grants no cabinet or mutation access.
    copied_cookie = {"Cookie": SESSION_COOKIE + "=" + cookie.value}
    assert client.get("/api/hotelier/" + B, headers=copied_cookie).status_code == 401
    assert client.post(f"/api/runs/{interview.id}/stream").status_code == 405
    admin, _ = credentials(client, "hotelier")
    assert client.get(f"/api/runs/{interview.id}", headers=admin).status_code == 200


def test_explicit_tenant_or_invalid_bearer_never_uses_old_admin_cookie_and_logout_clears_it(security_context):
    client, repo = security_context
    interview = AgentRun(kind="interview", hotel_id=B, status="waiting")
    repo.insert("agent_runs", interview.model_dump(mode="json"))
    tenant, _ = credentials(client, "cascade")
    admin, admin_value = credentials(client, "hotelier")
    assert client.get(f"/api/runs/{interview.id}").status_code == 200
    assert client.get(f"/api/runs/{interview.id}", headers=tenant).status_code == 403
    assert client.get(f"/api/runs/{interview.id}/stream", headers=tenant).status_code == 403
    assert client.get(f"/api/runs/{interview.id}", headers={"Authorization": "Bearer invalid"}).status_code == 401
    response = client.post("/api/hotelier/logout")
    assert response.status_code == 200 and response.json()["logged_out"] is True
    assert SESSION_COOKIE not in client.cookies
    assert client.get(f"/api/runs/{interview.id}/stream").status_code == 401
    # Cookie removal is local logout, not server-side revocation of copied tokens.
    assert client.get(f"/api/runs/{interview.id}", headers=admin).status_code == 200
    assert admin_value["token"] not in response.text


def test_change_feed_preserves_history_but_hides_guest_capabilities_and_scopes_notifications(security_context):
    client, repo = security_context
    own, foreign = passport(repo, A), passport(repo, B)
    notices = [{"id": "private-notice-" + item.id, "passport_id": item.id, "item_ids": [item.items[0].id],
                "text": "Synthetic private guest text " + item.guest_name} for item in (own, foreign)]
    event = ChangeEvent(kind="new_building", title="Synthetic security change", affected_hotel_ids=[A, B], notifications=notices)
    repo.insert("change_events", event.model_dump(mode="json"))
    before = copy.deepcopy(repo.tables)
    response = client.get("/api/changes")
    public = response.json()[0]
    assert public["notifications"] == [] and public["payload"]["notification_count"] == 2
    for notice in notices:
        assert notice["id"] not in response.text and notice["passport_id"] not in response.text
        assert notice["item_ids"][0] not in response.text and notice["text"] not in response.text
    tenant, _ = credentials(client, "cascade")
    scoped = client.get("/api/changes", headers=tenant).json()[0]
    assert scoped["notifications"] == [notices[0]] and scoped["payload"]["notification_count"] == 2
    admin, _ = credentials(client, "hotelier")
    assert client.get("/api/changes", headers=admin).json()[0]["notifications"] == notices
    assert repo.tables == before


def test_global_change_mutations_require_demo_admin_before_service_call(security_context, monkeypatch):
    client, repo = security_context
    event = ChangeEvent(kind="new_building", title="Synthetic security change", affected_hotel_ids=[B])
    calls = []
    def simulate(stored, hotel_id):
        assert stored is repo
        calls.append(("simulate", hotel_id))
        return event
    def refresh(stored):
        assert stored is repo
        calls.append(("refresh", None))
        return {"processed": 0, "run_id": "synthetic-no-db"}
    monkeypatch.setattr("backend.api.changes.simulate_change", simulate)
    monkeypatch.setattr("backend.api.changes.refresh_expired", refresh)
    before = copy.deepcopy(repo.tables)
    for endpoint, body in (("simulate", {"hotel_id": B}), ("refresh", {})):
        assert client.post("/api/changes/" + endpoint, json=body).status_code == 401
    tenant, _ = credentials(client, "cascade")
    for endpoint, body in (("simulate", {"hotel_id": A}), ("simulate", {"hotel_id": B}), ("refresh", {})):
        assert client.post("/api/changes/" + endpoint, headers=tenant, json=body).status_code == 403
    assert calls == [] and repo.tables == before
    admin, _ = credentials(client, "hotelier")
    assert client.post("/api/changes/simulate", headers=admin, json={"hotel_id": B}).status_code == 200
    assert client.post("/api/changes/refresh", headers=admin, json={}).status_code == 200
    assert calls == [("simulate", B), ("refresh", None)]


_BAD_CALLS = [ToolCall(tool=name) for name in ("shell", "network", "http_get", "python", "subprocess", "sql")]
_BAD_CALLS += [ToolCall(tool="hotelier_ask", params={name: "synthetic-forbidden"}) for name in ("url", "command", "code", "path", "headers", "source")]
_BAD_CALLS += [ToolCall(tool="derive", params={"expr": expression}) for expression in (
    "__import__('os').system('id')", "open('/etc/passwd').read()", "beach_walk['minutes'] < 10",
    "beach_walk.__class__", "[x for x in climate]", "lambda: True")]


@pytest.mark.parametrize("call", _BAD_CALLS, ids=lambda call: call.tool)
def test_untrusted_spec_shell_network_or_nonwhitelist_refuses_before_execution(security_context, tmp_path, call):
    _, repo = security_context
    spec = CharacteristicSpec.model_validate(repo.get("characteristics", "pets"))
    spec.methods = [call]
    before = copy.deepcopy(repo.tables)
    with pytest.raises((ValueError, SchemaValidationError)):
        validate_spec(spec, Registry(repo, data_dir=tmp_path))
    assert repo.tables == before


@pytest.mark.parametrize("reference", ["https://example.org/schema", "http://127.0.0.1:1/private", "file:///etc/passwd"])
def test_untrusted_spec_remote_references_are_rejected_without_retrieval(security_context, reference):
    _, repo = security_context
    spec = repo.get("characteristics", "pets")
    spec["value_schema"] = {"$ref": reference}
    with pytest.raises(ValueError):
        CharacteristicSpec.model_validate(spec)
