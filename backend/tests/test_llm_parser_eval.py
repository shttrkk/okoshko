"""Benchmark accounting against controlled transports, with no provider or DB."""
from __future__ import annotations

import json

import httpx
import pytest

from backend.llm.client import LLMClient
from scripts.llm_parser_eval import (
    MeasuredLLMClient,
    Measurements,
    MemoryLLMCache,
    latency_summary,
    run_comparison,
    validate_local_configuration,
)
from scripts.parser_eval import catalogue


@pytest.fixture
def live_environment(monkeypatch):
    monkeypatch.setenv('LLM_MODE', 'live')
    monkeypatch.setenv('LLM_API_KEY', 'synthetic-local-evaluation-key')
    monkeypatch.setenv('LLM_MODEL', 'synthetic-provider')
    monkeypatch.setenv('LLM_BASE_URL', 'http://localhost:11434/v1')


def row(text='тихо', *, item_id='evaluation-1'):
    return {'id': item_id, 'text': text, 'split': 'development', 'expected': {
        'must': [{'characteristic_id': 'quiet_night', 'field': 'available', 'op': 'eq', 'value': True}],
        'nice': [], 'no_data': [], 'unmapped': []}}


def response(payload=None, *, content=None):
    return httpx.Response(200, json={'choices': [{'message': {
        'content': content if content is not None else json.dumps(payload or {'text': 'provider'})}}]})


def comparison(rows, provider, *, experimental_budget_s=None):
    cache, measurements = MemoryLLMCache(), Measurements()
    report = run_comparison(rows, catalogue(), [], lambda: MeasuredLLMClient(
        cache, measurements, transport=httpx.MockTransport(provider), experimental_budget_s=experimental_budget_s), measurements)
    return report, cache, measurements


def test_cold_live_and_warm_cache_are_accounted_separately(live_environment):
    calls = []

    def provider(request):
        calls.append(request.url.path)
        return response()

    report, cache, measurements = comparison([row(), row(item_id='evaluation-2')], provider)
    assert report['live_path']['mode_counts'] == {'live': 1, 'cache': 1, 'offline': 0}
    assert report['offline']['gold']['exact_matches'] == report['live_path']['gold']['exact_matches'] == 2
    assert report['comparison']['changed_intent_queries'] == 0
    assert calls == ['/v1/chat/completions'] and len(cache.rows) == 1
    assert report['observability']['requested_product_budgets_s'] == [2.2]
    assert report['observability']['effective_budgets_s'] == [2.2]
    assert measurements.active_requests == 0 and measurements.max_active_requests == 1


def test_schema_format_rejection_is_visible_even_when_json_fallback_succeeds(live_environment):
    formats = []

    def provider(request):
        kind = json.loads(request.content).get('response_format', {}).get('type')
        formats.append(kind)
        return httpx.Response(400, json={'error': 'unsupported format'}) if kind == 'json_schema' else response()

    report, _, _ = comparison([row()], provider)
    assert formats == ['json_schema', 'json_object']
    assert report['live_path']['mode_counts']['live'] == 1
    assert report['observability']['counts']['schema_format_rejections'] == 1
    assert report['observability']['queries_with_schema_failure'] == 1
    assert report['observability']['http_statuses'] == {'400': 1, '200': 1}


@pytest.mark.parametrize(('content', 'counter'), [
    ('{"unrecognized": true}', 'schema_failures'),
    ('{malformed json', 'json_failures'),
])
def test_validation_failures_count_as_fallback_not_live_success(live_environment, content, counter):
    report, cache, _ = comparison([row()], lambda request: response(content=content))
    assert report['live_path']['mode_counts'] == {'live': 0, 'cache': 0, 'offline': 1}
    assert report['observability']['offline_fallback_queries'] == 1
    assert report['observability']['counts'][counter] == 4  # Three modes and one bounded JSON repair.
    assert report['live_path']['gold']['exact_matches'] == 1 and not cache.rows


def test_semantic_refusal_is_not_repaired_or_cached(live_environment):
    payload = {'text': 'provider', 'must': [{'characteristic_id': 'sea_view', 'field': 'available',
                                           'op': 'eq', 'value': True, 'label': 'provider'}]}
    report, cache, _ = comparison([row()], lambda request: response(payload))
    assert report['observability']['counts']['requests'] == 1
    assert report['observability']['counts']['semantic_rejections'] == 1
    assert report['live_path']['mode_counts']['offline'] == 1 and not cache.rows


def test_timeouts_are_counted_without_exception_messages_or_raw_secrets(live_environment):
    canary = 'synthetic-exception-content-never-log-this'

    def provider(request):
        raise httpx.ReadTimeout(canary, request=request)

    report, _, measurements = comparison([row()], provider)
    assert report['observability']['counts']['timeouts'] == 3
    assert report['observability']['queries_with_timeout'] == 1
    assert report['observability']['offline_fallback_queries'] == 1
    assert measurements.active_requests == 0 and canary not in json.dumps(report)


def test_response_body_timeout_releases_the_single_request_slot(live_environment):
    class TimedOutBody(httpx.SyncByteStream):
        def __iter__(self):
            raise httpx.ReadTimeout('synthetic body timeout')
            yield b''

    report, _, measurements = comparison([row()], lambda request: httpx.Response(200, stream=TimedOutBody()))
    assert report['observability']['counts']['timeouts'] == 3
    assert measurements.max_active_requests == 1 and measurements.active_requests == 0
    assert report['live_path']['mode_counts']['offline'] == 1


def test_region_guard_is_provider_skipped_instead_of_a_timeout(live_environment):
    def forbidden(request):
        raise AssertionError('Region guard must not contact a provider')

    report, _, measurements = comparison([row('в Москве тихо')], forbidden)
    assert report['observability']['provider_skipped_queries'] == 1
    assert report['observability']['offline_fallback_queries'] == 0
    assert measurements.counts['requests'] == 0


def test_experimental_deadline_cannot_be_mistaken_for_the_product_deadline(live_environment):
    report, _, _ = comparison([row()], lambda request: response(), experimental_budget_s=7.5)
    assert report['observability']['requested_product_budgets_s'] == [2.2]
    assert report['observability']['effective_budgets_s'] == [7.5]


@pytest.mark.parametrize('url', ['https://example.com/v1', 'http://user:secret@localhost:11434/v1',
                                'http://localhost:11434/v1?key=secret'])
def test_host_native_harness_rejects_remote_or_credential_bearing_urls(live_environment, monkeypatch, url):
    monkeypatch.setenv('LLM_BASE_URL', url)
    with pytest.raises(ValueError):
        validate_local_configuration(LLMClient())


def test_provider_key_echo_never_enters_the_report_or_cache(live_environment, monkeypatch):
    canary = 'synthetic-evaluation-canary-protected-value'
    monkeypatch.setenv('LLM_API_KEY', canary)
    content = json.dumps({'text': 'safe', 'clarifications': [canary]})
    content = content.replace(canary, ''.join(f'\\u{ord(char):04x}' for char in canary))
    report, cache, _ = comparison([row()], lambda request: response(content=content))
    assert report['live_path']['mode_counts']['offline'] == 1
    assert not cache.rows and canary not in json.dumps(report)


def test_memory_cache_has_no_hotel_or_claim_write_path():
    cache = MemoryLLMCache()
    with pytest.raises(ValueError):
        cache.upsert('claims', {'id': 'not-a-claim'})
    with pytest.raises(ValueError):
        cache.get('hotels', 'not-a-hotel')


def test_latency_summary_has_defined_empty_and_nearest_rank_tail_behavior():
    assert latency_summary([]) == {'queries': 0, 'p50_ms': None, 'p95_ms': None, 'total_s': 0.0}
    assert latency_summary([1, 2, 3, 100]) == {'queries': 4, 'p50_ms': 2.5, 'p95_ms': 100, 'total_s': .106}
