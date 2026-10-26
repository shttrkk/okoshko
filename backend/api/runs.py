"""Durable background collection and resumable server-sent trace events."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from fastapi.responses import StreamingResponse

from backend.api.catalog import required
from backend.contracts import AgentRun, RunRequest
from backend.db.repository import get_repo
from backend.tracing import Trace

router = APIRouter(prefix="/api", tags=["runs"])
_REPO = Depends(get_repo)
logger = logging.getLogger(__name__)


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
def runs(repo=_REPO):
    return sorted(repo.all("agent_runs"), key=lambda row: row.get("started_at", ""), reverse=True)[:50]


@router.get("/runs/{run_id}", response_model=AgentRun)
def run(run_id: str, repo=_REPO):
    value = required(repo, "agent_runs", run_id, "Прогон")
    value["steps"] = sorted(repo.find("agent_steps", run_id=run_id), key=lambda row: row["seq"])
    return value


@router.get("/runs/{run_id}/stream")
def stream(run_id: str, request: Request, repo=_REPO, last_event_id: str | None = Header(default=None)):
    required(repo, "agent_runs", run_id, "Прогон")

    async def events():
        sent = set()
        resume = last_event_id
        while True:
            if await request.is_disconnected():
                return
            value = await asyncio.to_thread(run, run_id, repo)
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
