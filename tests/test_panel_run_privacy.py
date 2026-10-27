"""Public list, detail and SSE never expose guest search payloads."""
import copy

from backend.contracts import AgentRun, AgentStep
from backend.tests.test_api_integration import client, repo  # noqa: F401


def private_search(store):
    secret = "PRIVATE-PANEL-QUERY: fictional guest detail"
    run = AgentRun(kind="search", status="completed", error=secret, trigger=secret)
    step = AgentStep(run_id=run.id, seq=0, agent=secret, type="decision", name=secret,
                     input={"text": secret}, output={"parsed": {"text": secret, "rooms": [{"label": secret}]}},
                     rationale=secret, evidence_refs=[secret], error=secret)
    run.steps = [step]
    store.insert("agent_runs", run.model_dump(mode="json"))
    store.insert("agent_steps", step.model_dump(mode="json"))
    return run.id, secret


def test_search_trace_redaction_is_recursive_consistent_and_does_not_mutate_storage(client, repo):  # noqa: F811
    run_id, secret = private_search(repo)
    original_run = copy.deepcopy(repo.get("agent_runs", run_id))
    original_steps = copy.deepcopy(repo.find("agent_steps", run_id=run_id))
    listing = client.get("/api/runs")
    detail = client.get(f"/api/runs/{run_id}")
    stream = client.get(f"/api/runs/{run_id}/stream")
    for response in (listing, detail, stream):
        assert response.status_code == 200
        assert secret not in response.text and "fictional guest detail" not in response.text
        assert "Запрос гостя" in response.text
    assert detail.json()["id"] == run_id
    assert detail.json()["steps"][0]["id"] == original_steps[0]["id"]
    assert repo.get("agent_runs", run_id) == original_run
    assert repo.find("agent_steps", run_id=run_id) == original_steps
    # Authenticated team users also receive the anonymised public projection.
    token = client.post("/api/hotelier/login", json={"username":"hotelier","password":"hotelier123"}).json()["token"]
    assert secret not in client.get(f"/api/runs/{run_id}", headers={"Authorization":"Bearer "+token}).text


def test_collection_trace_remains_observable_without_destroying_methods(client, repo):  # noqa: F811
    run = AgentRun(kind="collect", hotel_id="test-2", status="completed")
    step = AgentStep(run_id=run.id, seq=0, agent="geo", type="tool", name="Расчёт по карте",
                     input={"method":"line_of_sight"}, output={"visible":True}, rationale="Проверка источника")
    run.steps = [step]
    repo.insert("agent_runs", run.model_dump(mode="json"))
    repo.insert("agent_steps", step.model_dump(mode="json"))
    response = client.get(f"/api/runs/{run.id}")
    assert response.status_code == 200
    assert response.json()["steps"][0]["input"] == {"method":"line_of_sight"}
    assert response.json()["steps"][0]["output"] == {"visible":True}
