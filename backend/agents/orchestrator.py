"""Plan -> whitelist tools -> immutable claims -> materialized facts -> questions."""
import time

from backend.agents.planner import plan_characteristic
from backend.agents.resolver import resolve_hotel
from backend.contracts import CharacteristicSpec, ToolContext
from backend.tools.registry import Registry
from backend.tracing import Trace


def collect_hotel(hotel_id: str, repo, characteristic_ids=None, *, trigger="user", data_dir=None, trace=None):
    hotel = repo.get("hotels",hotel_id)
    if hotel is None:
        raise ValueError("Отель не найден")
    trace = trace or Trace(repo,"collect",hotel_id=hotel_id,trigger=trigger)
    registry = Registry(repo,**({"data_dir":data_dir} if data_dir else {}))
    specs = [CharacteristicSpec.model_validate(value) for value in repo.all("characteristics") if value.get("status","active")=="active" and (not characteristic_ids or value["id"] in characteristic_ids)]
    try:
        for spec in specs:
            plan, mode = plan_characteristic(spec,hotel,repo,registry)
            trace.run.llm_mode = mode
            trace.step("planner","plan",spec.name_ru,input={"spec":spec.model_dump(mode="json")},output={"steps":[step.model_dump() for step in plan]},
                       rationale="План использует только разрешённые инструменты спецификации; значение получает код",mode=mode)
            for call in plan:
                started = time.monotonic()
                result = registry.execute(ToolContext(hotel_id=hotel_id,characteristic_id=spec.id,run_id=trace.run.id),call)
                if result.claims:
                    repo.insert_claims(result.claims)
                trace.step("collector","tool_call",call.tool,input=call.params,
                           output={"claim_count":len(result.claims),"claim_ids":[claim.id for claim in result.claims],"warnings":result.warnings},
                           rationale=call.rationale,evidence_refs=[evidence.id for claim in result.claims for evidence in claim.evidence],
                           duration_ms=(time.monotonic()-started)*1000,cache_hit=result.artifacts.get("cache_hit",False))
        facts = resolve_hotel(hotel_id,repo)
        trace.step("resolver","decision","Согласование источников",output={"facts":len(facts),"disputed":sum(fact.status=="disputed" for fact in facts)},
                   rationale="Повторные наблюдения одного источника не увеличивают вес; фасады и этажи сохраняются отдельно")
        from backend.agents.interviewer import generate_questions
        questions = generate_questions(hotel_id,repo)
        trace.step("interviewer","question","Пробелы и вопросы отельеру",output={"question_ids":[question.id for question in questions]},rationale="Вопросы выбраны по неопределённости, спросу и возможности отельера ответить")
        return trace.finish()
    except Exception as error:
        trace.step("executor","error","Прогон завершился ошибкой",error=type(error).__name__,rationale=str(error)[:300])
        trace.finish("failed",str(error)[:300])
        raise

