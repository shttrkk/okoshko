"""Known-answer boundaries: unsupported plans never reach ranking or its cache."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

from backend.agents.query_parser import parse_query
from backend.agents.query_rules import apply_region_coverage
from backend.contracts import CharacteristicSpec, SearchRequest
from backend.llm.client import LLMClient
from backend.tests.test_hotelier import MemoryRepo
from scripts.parser_eval import criterion_multiset, load_jsonl

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def catalog():
    return [CharacteristicSpec.model_validate(yaml.safe_load(path.read_text()))
            for path in sorted((ROOT/'data/characteristics').glob('*.yaml'))]


@pytest.mark.parametrize('criterion', [
    {'characteristic_id': 'sea_view', 'field': 'password', 'op': 'contains', 'value': {'admin': True}},
    {'characteristic_id': 'pets', 'field': 'available', 'op': 'eq', 'value': 'yes'},
    {'characteristic_id': 'pets', 'field': 'available', 'op': 'gte', 'value': True},
    {'characteristic_id': 'connectivity', 'field': 'download_mbps', 'op': 'gte', 'value': 999999},
    {'characteristic_id': 'sea_view', 'field': 'available', 'op': 'eq', 'value': True},
    {'characteristic_id': 'quiet_night', 'field': 'available', 'value': True, 'no_stairs': True},
])
def test_provider_cannot_add_ungrounded_fields_types_operators_or_numbers(monkeypatch, catalog, criterion):
    monkeypatch.setenv('LLM_MODE', 'live')
    monkeypatch.setenv('LLM_API_KEY', 'local-test-only')
    monkeypatch.setenv('LLM_MODEL', 'synthetic-query-safety')
    calls = []

    def provider(request):
        calls.append(request.url.path)
        payload = {'text': 'provider', 'must': [{'label': 'provider', **criterion}]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(payload)}}]})

    repo = MemoryRepo()
    client = LLMClient(repo, transport=httpx.MockTransport(provider))
    result = parse_query(SearchRequest(text='бассейн'), catalog, llm=client)
    assert not result.must and not result.nice and result.mode == 'offline'
    assert not repo.all('llm_cache') and not repo.all('claims')
    assert len(calls) == 1


@pytest.mark.parametrize('text', [
    'до пляжа 7 минут без лестниц, интернет от 50 Мбит/с, без вида на море',
    'без вида на море, интернет от 50 Мбит/с, до пляжа 7 минут без лестниц',
])
def test_provider_cannot_reorder_away_hard_constraints_or_change_weights(monkeypatch, catalog, text):
    baseline = parse_query(SearchRequest(text=text), catalog)
    monkeypatch.setenv('LLM_MODE', 'live')
    monkeypatch.setenv('LLM_API_KEY', 'local-test-only')
    monkeypatch.setenv('LLM_MODEL', 'synthetic-query-safety')
    payload = baseline.model_dump(mode='json')
    payload['must'], payload['nice'] = [], list(reversed(payload['must']))
    for criterion in payload['nice']:
        criterion['weight'] = 10
        criterion['label'] = 'provider weight'

    def provider(request):
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(payload)}}]})

    repo = MemoryRepo()
    result = parse_query(SearchRequest(text=text), catalog,
                         llm=LLMClient(repo, transport=httpx.MockTransport(provider)))
    assert result.must == baseline.must and result.nice == baseline.nice
    cached = repo.all('llm_cache')
    assert len(cached) == 1 and cached[0]['value']['must'] == baseline.model_dump(mode='json')['must']


def test_coverage_projection_is_authoritative_idempotent_and_preserves_thresholds(catalog):
    parsed = parse_query(SearchRequest(text='интернет от 50 Мбит/с, желательно до пляжа 7 минут'), catalog)
    original = parsed.model_copy(deep=True)
    apply_region_coverage(parsed, {'connectivity': {'status': 'no_data'}})
    assert parsed.no_data == original.must and parsed.must == [] and parsed.nice == original.nice
    projected = parsed.model_dump()
    assert apply_region_coverage(parsed, {'connectivity': {'status': 'no_data'}}).model_dump() == projected
    assert apply_region_coverage(original, {}).must


@pytest.mark.parametrize(('text', 'must', 'nice'), [
    ('не важен вид на море, нужна тишина', ['quiet_night'], []),
    ('тихо и желательно вид на море', ['quiet_night'], ['sea_view']),
    ('вид на море желательно, тихо обязательно', ['quiet_night'], ['sea_view']),
    ('где можно выспаться', ['quiet_night'], []),
    ('мне нужна тишина и вид на горы', ['quiet_night', 'mountain_view'], []),
    ('кондиционер не нужен, шторы блэкаут обязательны', ['soundproofing'], []),
])
def test_local_importance_applies_to_the_intended_condition(catalog, text, must, nice):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert [c.characteristic_id for c in parsed.must] == must
    assert [c.characteristic_id for c in parsed.nice] == nice


def test_eval_numeric_equivalence_does_not_confuse_boolean_with_number():
    criterion = {'characteristic_id': 'beach_walk', 'field': 'minutes', 'op': 'lte', 'value': 10}
    assert criterion_multiset({'must': [criterion]}) == criterion_multiset({'must': [{**criterion, 'value': 10.0}]})
    assert criterion_multiset({'must': [{**criterion, 'value': True}]}) != criterion_multiset({'must': [{**criterion, 'value': 1}]})


def test_authored_corpora_have_disjoint_splits_and_no_parser_generated_labels():
    gold = load_jsonl(ROOT/'data/eval/queries_gold.jsonl')
    negatives = load_jsonl(ROOT/'data/eval/queries_negative.jsonl')
    assert len(gold) >= 200 and len(negatives) >= 100
    assert sum(row['split'] == 'holdout' for row in gold) >= 50
    assert len({row['text'].casefold() for row in [*gold, *negatives]}) == len(gold)+len(negatives)
    assert all(row['label_type'] == 'synthetic_authored_intent' for row in gold)
    assert all(set(('must', 'nice', 'no_data', 'unmapped')) <= row['expected'].keys() for row in gold)
