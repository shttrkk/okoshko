"""Compile a description to a declarative, validated plan; never to Python/SQL."""
import hashlib
import re

import yaml
from jsonschema import Draft202012Validator

from backend.agents.spec_templates import SUPPORTED, compile_template
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
        from backend.agents.query_parser import landmark_mentions
        from backend.agents.query_text import normalize_query_text
        targets = landmark_mentions(normalize_query_text(text))
        if re.search(r'вид\w*\s+на|view of', low) and len(targets) == 1:
            identity, mention = targets[0]
            return CharacteristicSpec(
                id='landmark_' + identity, name_ru=text[:80], description=(
                    'Видимость верхней части выбранного объекта по подтверждённому контуру и высоте. '
                    'Без этих входов результат остаётся неизвестным; соседние объекты не используются.'),
                synonyms=[text.lower()], scope='floor_range',
                value_schema={'type': 'object', 'properties': {'available': {'type': ['boolean', 'null']}}, 'additionalProperties': True},
                methods=[ToolCall(tool='viewshed_facade', params={'target': 'landmark', 'landmark_id': identity},
                                  rationale='Проверить вид на ' + mention[0] + ' по сохранённым доказательствам')],
                search=SearchSpec(kind='boolean', field='available', default_op='eq', default_value=True),
                ttl_days=90, hotelier_answerable=False, status='draft')
        template = compile_template(text)
        if template is not None:
            return template
        matches = [spec for spec in catalog if any(synonym in low for synonym in spec.synonyms)]
        if len(matches)==1:
            spec=matches[0].model_copy(deep=True)
            spec.id="custom_"+hashlib.sha256(text.encode()).hexdigest()[:12]
            spec.name_ru=text[:80]
            spec.description=text
            spec.synonyms=[text.lower()]
            spec.status="draft"
            return spec
        raise ValueError("Описание не сопоставлено с доступными инструментами. " + SUPPORTED)
    client=LLMClient(repo)
    spec,mode=client.structured(CharacteristicSpec,[{"role":"user","content":"Составь спецификацию характеристики без фактических значений отелей. Только инструменты: "+", ".join(registry.specs)+". Описание: "+text}],fallback,task="compile_spec",budget_s=2.4)
    validate_spec(spec,registry)
    warnings=[]
    if mode=="offline":
        warnings.append("Офлайн: детерминированный шаблон, YAML или уточнение существующей характеристики; LLM сеть не использовалась")
    return spec,mode,warnings

