"""LLM may order approved tools; deterministic data access remains authoritative."""
from pydantic import BaseModel

from backend.contracts import CharacteristicSpec, ToolCall
from backend.llm.client import LLMClient


class Plan(BaseModel):
    steps: list[ToolCall]


def plan_characteristic(spec: CharacteristicSpec, hotel: dict, repo, registry, llm=None):
    facts = repo.find("facts",hotel_id=hotel["id"],characteristic_id=spec.id)
    confidence = max((fact.get("confidence",0) for fact in facts),default=0)
    steps = []
    for method in spec.methods:
        registry.validate(method)
        if method.tool == "hotelier_ask" and confidence >= .85:
            continue
        steps.append(method.model_copy(update={"rationale":method.rationale+f"; доступны локальные данные, текущая уверенность {confidence:.2f}"}))
    fallback = Plan(steps=steps)
    client = llm or LLMClient(repo)
    result, mode = client.structured(Plan,[{"role":"user","content":"Выбери порядок только из разрешённых methods, не меняя параметры. Не выдавай значения характеристик. Спецификация: "+spec.model_dump_json()+"; контекст отеля: "+str({"id":hotel["id"],"confidence":confidence,"known_facts":len(facts)})}],lambda:fallback,task="plan",budget_s=2.4)
    approved = {(method.tool, str(sorted(method.params.items()))) for method in spec.methods}
    try:
        for step in result.steps:
            registry.validate(step)
            if (step.tool,str(sorted(step.params.items()))) not in approved:
                raise ValueError("Plan changed approved parameters")
    except ValueError:
        return fallback.steps,"offline"
    return result.steps,mode

