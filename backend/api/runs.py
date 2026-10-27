"""Durable background collection and resumable server-sent trace events."""
from __future__ import annotations

import asyncio
import copy
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.api.catalog import required
from backend.contracts import AgentRun, RunRequest
from backend.db.repository import get_repo
from backend.security import authorize_hotel, optional_principal
from backend.tracing import Trace

router = APIRouter(prefix="/api", tags=["runs"])
_REPO = Depends(get_repo)
_OPTIONAL_AUTH = Depends(optional_principal)
logger = logging.getLogger(__name__)


def public_run(value: dict) -> dict:
    """Public search traces expose execution, never the guest's free text."""
    result = copy.deepcopy(value)
    if result.get("kind") != "search":
        return result
    result = {key: item for key, item in result.items() if key in AgentRun.model_fields}
    result["trigger"] = "user"
    result["hotel_id"] = None
    result["characteristic_id"] = None
    if result.get("error"):
        result["error"] = "Не удалось обработать запрос гостя"
    for step in result.get("steps", []):
        total = step.get("output", {}).get("total")
        step["agent"] = "search"
        step["type"] = "decision"
        step["name"] = "Запрос гостя" if step.get("seq") == 0 else "Подбор отелей"
        step["input"] = {"summary": "Запрос гостя"}
        step["output"] = {"total": total} if type(total) is int and total >= 0 else {}
        step["rationale"] = "Личный текст скрыт; показан ход обработки запроса."
        step["evidence_refs"] = []
        if step.get("error"):
            step["error"] = "Не удалось выполнить шаг поиска"
    return result


def run_collection(body, repo, trace):
    from backend.agents.orchestrator import collect_hotel
    try:
        collect_hotel(body.hotel_id, repo, body.characteristic_ids, trace=trace)
    except Exception as error:
        # The orchestrator normally persists its own failure; also cover import/start errors.
        if trace.run.status == "running":
            trace.finish("failed", str(error)[:300])
        logger.exception("Collection failed for run %s", trace.run.id)


@router.post("/runs", response_model=AgentRun)
def start(body: RunRequest, background_tasks: BackgroundTasks, repo=_REPO):
    required(repo, "hotels", body.hotel_id, "Отель")
    for item_id in body.characteristic_ids:
        required(repo, "characteristics", item_id, "Характеристика")
    trace = Trace(repo, "collect", hotel_id=body.hotel_id, trigger="user")
    background_tasks.add_task(run_collection, body, repo, trace)
    return trace.run


@router.get("/runs", response_model=list[AgentRun])
def runs(repo=_REPO, _user=_OPTIONAL_AUTH):
    visible = [row for row in repo.all("agent_runs") if row.get("kind") != "interview"
               or _user is not None and _user.permits(row.get("hotel_id"))]
    ordered = sorted(visible, key=lambda row: row.get("started_at", ""), reverse=True)
    latest = ordered[:50]
    # Frequent searches must not push the latest collection, backfill or refresh out of the agent page.
    shown = {row["id"] for row in latest}
    for kind in ("collect", "backfill", "refresh", "compile_spec", "interview"):
        newest = next((row for row in ordered if row.get("kind") == kind), None)
        if newest and newest["id"] not in shown:
            latest.append(newest)
            shown.add(newest["id"])
    return [public_run(value) for value in latest]


@router.get("/runs/{run_id}", response_model=AgentRun)
def run(run_id: str, repo=_REPO, _user=_OPTIONAL_AUTH):
    return _read_run(run_id, repo, _user)


def _read_run(run_id, repo, principal):
    value = copy.deepcopy(required(repo, "agent_runs", run_id, "Прогон"))
    if value.get("kind") == "interview":
        if principal is None:
            raise HTTPException(401, "Войдите в демо-кабинет отельера", headers={"WWW-Authenticate": "Bearer"})
        authorize_hotel(principal, value.get("hotel_id"))
    steps = repo.find("agent_steps", run_id=run_id) or value.get("steps", [])
    value["steps"] = sorted(steps, key=lambda row: row["seq"])
    return public_run(value)


@router.get("/runs/{run_id}/stream")
def stream(run_id: str, request: Request, repo=_REPO, last_event_id: str | None = Header(default=None),
           _user=_OPTIONAL_AUTH):
    _read_run(run_id, repo, _user)

    async def events():
        sent = set()
        resume = last_event_id
        while True:
            if await request.is_disconnected():
                return
            value = await asyncio.to_thread(_read_run, run_id, repo, _user)
            steps = value.get("steps", [])
            if resume:
                previous = next((step["seq"] for step in steps if step["id"] == resume), None)
                if previous is not None:
                    sent.update(step["id"] for step in steps if step["seq"] <= previous)
                resume = None
            for step in steps:
                if step["id"] not in sent:
                    sent.add(step["id"])
                    yield "id: " + step["id"] + "\nevent: step\ndata: " + json.dumps(step, ensure_ascii=False) + "\n\n"
            if value["status"] in {"completed", "failed", "waiting"}:
                yield "event: done\ndata: " + json.dumps({"run_id": run_id, "status": value["status"], "error": value.get("error")}, ensure_ascii=False) + "\n\n"
                return
            yield ": keep-alive\n\n"
            await asyncio.sleep(.25)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
