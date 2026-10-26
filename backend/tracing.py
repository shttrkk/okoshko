"""Durable, inspectable execution trace. Rationale is a plan explanation, not proof."""
from __future__ import annotations

from backend.contracts import AgentRun, AgentStep, now


class Trace:
    def __init__(self, repo, kind, **kwargs):
        self.repo = repo
        self.run = AgentRun(kind=kind, **kwargs)
        repo.insert("agent_runs", self.run.model_dump(mode="json"))

    def step(self, agent: str, type: str, name: str, *, input=None, output=None, rationale="Детерминированное выполнение", evidence_refs=None, duration_ms=0, mode="offline", cache_hit=False, error=None):
        step = AgentStep(run_id=self.run.id, seq=len(self.run.steps), agent=agent, type=type, name=name,
                         input=input or {}, output=output or {}, rationale=rationale, evidence_refs=evidence_refs or [],
                         duration_ms=duration_ms, mode=mode, cache_hit=cache_hit, error=error)
        self.repo.insert("agent_steps", step.model_dump(mode="json"))
        self.run.steps.append(step)
        self.repo.upsert("agent_runs", self.run.model_dump(mode="json"))
        return step

    def finish(self, status="completed", error=None):
        self.run.status = status
        self.run.finished_at = now()
        self.run.error = error
        self.repo.upsert("agent_runs", self.run.model_dump(mode="json"))
        return self.run

