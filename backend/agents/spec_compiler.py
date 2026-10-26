"""Compile a description to a declarative, validated plan; never to Python/SQL."""
import hashlib
import re

import yaml
from jsonschema import Draft202012Validator

from backend.contracts import CharacteristicSpec, SearchSpec, ToolCall
from backend.llm.client import LLMClient
from backend.tools.derive import validate_expression


def validate_spec(spec, registry):
    Draft202012Validator.check_schema(spec.value_schema)
    for method in spec.methods:
        registry.validate(method)
        if method.tool=="derive":
            validate_expression(method.params.get("expr", ""))
    if spec.combine:
        validate_expression(spec.combine)
    return spec


def compile_spec(text, yaml_text, repo, registry):
    if yaml_text:
        spec = CharacteristicSpec.model_validate(yaml.safe_load(yaml_text))
        return validate_spec(spec,registry),"offline",[]
    low = text.lower()
    catalog = [CharacteristicSpec.model_validate(value) for value in repo.all("characteristics")]
    def fallback():
        if any(word in low for word in ["искуп", "до завтрака", "утреннее купание", "утром", "поплавать"]):
            minutes = int(re.search(r"(?:≤|<=|до)\s*(\d{1,2})\s*мин",low).group(1)) if re.search(r"(?:≤|<=|до)\s*(\d{1,2})\s*мин",low) else 10
            water = int(re.search(r"(?:≥|>=|от)\s*(\d{1,2})\s*°",low).group(1)) if re.search(r"(?:≥|>=|от)\s*(\d{1,2})\s*°",low) else 20
            expression=f"beach_walk.minutes <= {minutes} and climate.water_c >= {water}"
            return CharacteristicSpec(id="morning_swim",name_ru="Искупаться до завтрака",description=text,
                synonyms=["искупаться до завтрака","утреннее купание","поплавать утром"],value_schema={"type":"object","properties":{"available":{"type":"boolean"},"minutes":{"type":["number","null"]},"water_c":{"type":["number","null"]},"month":{"type":"integer"}},"required":["available"]},
                methods=[ToolCall(tool="derive",params={"expr":expression},rationale="Объединить реальный пеший маршрут и архивную температуру воды для месяца поездки")],
                combine=expression,ttl_days=90,search=SearchSpec(explain_ok="Пляж рядом и вода достаточно тёплая по архивным данным",explain_bad="Условие по времени до моря или температуре воды не выполнено"))
        matches = [spec for spec in catalog if any(synonym in low for synonym in spec.synonyms)]
        if len(matches)==1:
            spec=matches[0].model_copy(deep=True)
            spec.id="custom_"+hashlib.sha256(text.encode()).hexdigest()[:12]
            spec.name_ru=text[:80]
            spec.description=text
            spec.synonyms=[text.lower()]
            spec.status="draft"
            return spec
        raise ValueError("Описание не сопоставлено с доступными инструментами. Используйте пример «искупаться до завтрака» или введите YAML спецификацию")
    client=LLMClient(repo)
    spec,mode=client.structured(CharacteristicSpec,[{"role":"user","content":"Составь спецификацию характеристики без фактических значений отелей. Только инструменты: "+", ".join(registry.specs)+". Описание: "+text}],fallback,task="compile_spec",budget_s=2.4)
    validate_spec(spec,registry)
    warnings=[]
    if mode=="offline":
        warnings.append("Офлайн: детерминированный пример или YAML; LLM сеть не использовалась")
    return spec,mode,warnings

