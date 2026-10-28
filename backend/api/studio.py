"""Compile, preview and publish declarative characteristics with durable backfill."""
from __future__ import annotations

import logging
import threading

import yaml
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from backend.agents.assessment_rules import required_fulfilled
from backend.agents.planner import plan_characteristic
from backend.agents.query_match import assess_hotel
from backend.agents.resolver import resolve_claims
from backend.agents.spec_compiler import compile_spec, validate_spec
from backend.api.catalog import catalog
from backend.catalogue import searchable
from backend.contracts import (
    CharacteristicSpec,
    Claim,
    CompileRequest,
    Criterion,
    DryRunResult,
    Hotel,
    ParsedQuery,
    ToolContext,
    now,
)
from backend.contracts.validation import validate_value as validate
from backend.db.repository import get_repo
from backend.tools.registry import Registry
from backend.tracing import Trace

router = APIRouter(prefix="/api/characteristics", tags=["studio"])
_REPO = Depends(get_repo)
_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


def checked(spec, repo):
    try:
        return validate_spec(spec, Registry(repo))
    except Exception as error:
        raise HTTPException(422, "Спецификация не прошла проверку: " + str(error)[:400]) from error


def execute_preview(spec, hotel, repo, trace):
    registry = Registry(repo)
    plan, mode = plan_characteristic(spec, hotel, repo, registry)
    claims, warnings = [], []
    for call in plan:
        result = registry.execute(ToolContext(hotel_id=hotel["id"], characteristic_id=spec.id, run_id=trace.run.id), call)
        for claim in result.claims:
            claim.spec_version = spec.version
            validate(claim.value, spec.value_schema)
        claims.extend(result.claims)
        warnings.extend(result.warnings)
    return claims, warnings, mode


@router.post("/compile")
def compile_characteristic(body: CompileRequest, repo=_REPO):
    trace = Trace(repo, "compile_spec", trigger="studio")
    try:
        spec, mode, warnings = compile_spec(body.text, body.yaml_text, repo, Registry(repo))
        trace.run.llm_mode = mode
        trace.step("compiler", "plan", "Спецификация проверена", input={"text": body.text},
                   output={"spec": spec.model_dump(mode="json"), "warnings": warnings}, mode=mode,
                   rationale="JSON Schema и параметры проверены; выражения разрешают только безопасный AST")
        trace.finish()
        return {"spec": spec, "yaml_text": yaml.safe_dump(spec.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
                "plan": spec.methods, "warnings": warnings, "mode": mode}
    except Exception as error:
        trace.finish("failed", str(error)[:300])
        raise HTTPException(422, "Не удалось составить спецификацию: " + str(error)[:400]) from error


@router.post("/dry-run", response_model=DryRunResult)
def dry_run(body: CharacteristicSpec, repo=_REPO):
    checked(body, repo)
    trace = Trace(repo, "compile_spec", characteristic_id=body.id, trigger="studio_preview")
    hotel_rows = [row for row in repo.all("hotels") if searchable(row)]
    hotel_rows.sort(key=lambda hotel: (hotel["id"] != "osm-node-10058556917", hotel["id"]))
    results = []
    try:
        for hotel in hotel_rows[:3]:
            claims, warnings, mode = execute_preview(body, hotel, repo, trace)
            hotel_model = Hotel.model_validate(hotel)
            facts, _ = resolve_claims(hotel["id"], claims, [body], repo.all("sources"), known_floors=hotel_model.floors)
            criterion = Criterion(characteristic_id=body.id, label=body.display_name or body.name_ru,
                                  field=body.search.field, op=body.search.default_op, value=body.search.default_value)
            assessment = assess_hotel(facts, ParsedQuery(text="", must=[criterion]), [body], hotel=hotel_model)
            matched = required_fulfilled(assessment, 1)
            results.append({"hotel_id": hotel["id"], "hotel_name": hotel["name"], "claims": claims,
                            "warnings": warnings, "matched": matched,
                            "match_status": assessment.matches[0].status if assessment.matches else "unknown",
                            "match_reason": assessment.matches[0].reason if assessment.matches else "Нет данных"})
            trace.step("compiler", "tool_call", "Предпросмотр без записи claims", input={"hotel_id": hotel["id"]},
                       output={"claims": [claim.model_dump(mode="json") for claim in claims], "warnings": warnings}, mode=mode,
                       evidence_refs=[e.id for claim in claims for e in claim.evidence],
                       rationale="Результаты проверены по схеме; каталог, claims и materialized facts не изменяются")
        trace.finish()
        return {"spec_id": body.id, "results": results, "run_id": trace.run.id, "valid": bool(results),
                "total": len(hotel_rows), "processed": len(results),
                "matched_count": sum(row["matched"] for row in results)}
    except Exception as error:
        trace.finish("failed", str(error)[:300])
        raise HTTPException(422, "Предпросмотр не прошёл проверку: " + str(error)[:400]) from error


def backfill(spec, repo, trace, job):
    try:
        specs = catalog(repo)
        sources = repo.all("sources")
        for hotel in repo.all("hotels"):
            claims, warnings, mode = execute_preview(spec, hotel, repo, trace)
            if claims:
                repo.insert_claims(claims)
            # Reuse the Resolver over this hotel's immutable observation history.
            observed = [Claim.model_validate(row) for row in repo.find("claims", hotel_id=hotel["id"])]
            facts, conflicts = resolve_claims(hotel["id"], observed, specs, sources,
                                             known_floors=Hotel.model_validate(hotel).floors)
            repo.replace_facts(hotel["id"], [fact.model_dump(mode="json") for fact in facts])
            if conflicts:
                repo.upsert_many("conflicts", [conflict.model_dump(mode="json") for conflict in conflicts])
            job["processed"] += 1
            job["claim_count"] += len(claims)
            repo.upsert("jobs", job)
            trace.step("collector", "claim_write", "Публикация: " + hotel["name"], input={"hotel_id": hotel["id"]},
                       output={"processed": job["processed"], "total": job["total"], "claim_count": len(claims), "warnings": warnings}, mode=mode,
                       evidence_refs=[e.id for claim in claims for e in claim.evidence],
                       rationale="Вычислены только заявленные whitelist-инструменты; неизвестные входы сохранены как пробел")
        job["status"] = "completed"
        repo.upsert("jobs", job)
        trace.finish()
    except Exception as error:
        job["status"], job["error"] = "failed", str(error)[:300]
        repo.upsert("jobs", job)
        trace.finish("failed", str(error)[:300])
        logger.exception("Backfill failed for %s", spec.id)


@router.post("/publish")
def publish(body: CharacteristicSpec, background_tasks: BackgroundTasks, repo=_REPO):
    checked(body, repo)
    with _LOCK:
        current = repo.get("characteristics", body.id)
        content = body.model_dump(exclude={"version", "status"})
        if current and CharacteristicSpec.model_validate(current).model_dump(exclude={"version", "status"}) == content:
            jobs = repo.find("jobs", characteristic_id=body.id, version=current["version"])
            if jobs and jobs[-1]["status"] in {"running", "completed"}:
                job = jobs[-1]
                return {"spec": current, "run_id": job["run_id"], "processed": job["processed"], "total": job["total"], "status": job["status"]}
        spec = body.model_copy(update={"version": current["version"]+1 if current else 1, "status": "active"}, deep=True)
        trace = Trace(repo, "backfill", characteristic_id=spec.id, trigger="studio_publish")
        if current and not repo.get("characteristic_versions", f"{body.id}:v{current['version']}"):
            repo.insert("characteristic_versions", {"id": f"{body.id}:v{current['version']}", "characteristic_id": body.id,
                        "version": current["version"], "spec": current, "created_at": now().isoformat(), "run_id": None})
        # A changed definition must not reuse materialized values from its older meaning.
        if current:
            previous_facts = repo.find("facts", characteristic_id=spec.id)
            if previous_facts:
                stale = [{**fact, "status": "stale", "confidence": 0, "valid_until": now().isoformat()} for fact in previous_facts]
                repo.upsert_many("facts", stale)
        repo.upsert("characteristics", spec.model_dump(mode="json"))
        repo.insert("characteristic_versions", {"id": f"{spec.id}:v{spec.version}", "characteristic_id": spec.id,
                    "version": spec.version, "spec": spec.model_dump(mode="json"), "created_at": now().isoformat(), "run_id": trace.run.id})
        job = {"id": trace.run.id, "kind": "backfill", "characteristic_id": spec.id, "version": spec.version,
               "run_id": trace.run.id, "processed": 0, "total": len(repo.all("hotels")), "claim_count": 0, "status": "running"}
        repo.insert("jobs", job)
        trace.step("compiler", "decision", "Характеристика опубликована", output=job,
                   rationale="Версия сохранена; характеристика сразу доступна каталогу и парсеру поиска. Ход заполнения виден в этом прогоне")
        background_tasks.add_task(backfill, spec, repo, trace, job)
        return {"spec": spec, "run_id": trace.run.id, "processed": 0, "total": job["total"], "status": "running"}
