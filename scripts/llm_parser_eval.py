"""Compare offline and local-provider parsing without a database or raw model logs.

The default experiment uses the unchanged product deadline. A larger deadline is
available only through an explicitly labelled experimental flag. Requests are
strictly sequential; the cache is process-local and starts empty. This is a
host-native language benchmark, not Docker/network-bridge or hotel-fact proof.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import statistics
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.contracts import Hotel  # noqa: E402
from backend.llm.client import LLMClient, UnsafeOutput  # noqa: E402
from backend.security import contains_runtime_secret  # noqa: E402
from scripts.parser_eval import aggregate, catalogue, compare_case, load_jsonl, parse_case  # noqa: E402


class MemoryLLMCache:
    """Only validated plan-cache writes are possible; no hotel/claim repository."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def get(self, table: str, item_id: str):
        if table != 'llm_cache':
            raise ValueError('Benchmark repository only supports the plan cache')
        return copy.deepcopy(self.rows.get(item_id))

    def upsert(self, table: str, value: dict):
        if table != 'llm_cache':
            raise ValueError('Benchmark repository only supports the plan cache')
        self.rows[value['id']] = copy.deepcopy(value)
        return value


@dataclass
class Measurements:
    counts: Counter = field(default_factory=Counter)
    statuses: Counter = field(default_factory=Counter)
    requested_budgets: set[float] = field(default_factory=set)
    effective_budgets: set[float] = field(default_factory=set)
    active_requests: int = 0
    max_active_requests: int = 0

    def snapshot(self) -> dict:
        return {'counts': dict(self.counts), 'http_statuses': dict(self.statuses)}

    def delta(self, before: dict) -> dict:
        return {'counts': dict(self.counts-Counter(before['counts'])),
                'http_statuses': dict(self.statuses-Counter(before['http_statuses']))}


class MeasuredStream(httpx.SyncByteStream):
    def __init__(self, inner: httpx.SyncByteStream, measurements: Measurements):
        self.inner = inner
        self.measurements = measurements
        self.finished = False

    def finish(self):
        if not self.finished:
            self.finished = True
            self.measurements.active_requests -= 1

    def __iter__(self):
        try:
            yield from self.inner
        except httpx.TimeoutException:
            self.measurements.counts['timeouts'] += 1
            raise
        except httpx.NetworkError:
            self.measurements.counts['network_errors'] += 1
            raise
        finally:
            self.finish()

    def close(self):
        try:
            self.inner.close()
        finally:
            self.finish()


class MeasuredTransport(httpx.BaseTransport):
    """Observe type/status/count only, never persist request or response content."""

    def __init__(self, measurements: Measurements, inner: httpx.BaseTransport | None = None):
        self.measurements = measurements
        self.inner = inner if inner is not None else httpx.HTTPTransport(retries=0, trust_env=False)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        measurements = self.measurements
        measurements.counts['requests'] += 1
        measurements.active_requests += 1
        measurements.max_active_requests = max(measurements.max_active_requests, measurements.active_requests)
        if measurements.active_requests != 1:
            measurements.active_requests -= 1
            raise RuntimeError('Benchmark allows only one request in flight')
        try:
            response = self.inner.handle_request(request)
        except httpx.TimeoutException:
            measurements.counts['timeouts'] += 1
            measurements.active_requests -= 1
            raise
        except httpx.NetworkError:
            measurements.counts['network_errors'] += 1
            measurements.active_requests -= 1
            raise
        except Exception:
            measurements.active_requests -= 1
            raise
        measurements.statuses[str(response.status_code)] += 1
        if response.status_code in {400, 422}:
            body = json.loads(request.content)
            if body.get('response_format', {}).get('type') == 'json_schema':
                measurements.counts['schema_format_rejections'] += 1
        return httpx.Response(response.status_code, headers=response.headers, extensions=response.extensions,
                              stream=MeasuredStream(response.stream, measurements), request=request)

    def close(self):
        self.inner.close()


class MeasuredLLMClient(LLMClient):
    def __init__(self, cache: MemoryLLMCache, measurements: Measurements,
                 *, transport: httpx.BaseTransport | None = None, experimental_budget_s: float | None = None):
        self.measurements = measurements
        self.experimental_budget_s = experimental_budget_s
        super().__init__(cache, transport=MeasuredTransport(measurements, transport))

    def structured(self, output_model, messages, fallback, *, task='structured', budget_s=2.4):
        self.measurements.counts['structured_calls'] += 1
        self.measurements.requested_budgets.add(budget_s)
        effective = self.experimental_budget_s if self.experimental_budget_s is not None else budget_s
        self.measurements.effective_budgets.add(effective)
        return super().structured(output_model, messages, fallback, task=task, budget_s=effective)

    def _message_content(self, payload):
        try:
            return super()._message_content(payload)
        except UnsafeOutput:
            self.measurements.counts['unsafe_output_rejections'] += 1
            raise
        except (KeyError, TypeError, IndexError):
            self.measurements.counts['envelope_schema_failures'] += 1
            raise

    def _parse(self, output_model, content):
        try:
            return super()._parse(output_model, content)
        except UnsafeOutput:
            self.measurements.counts['semantic_rejections'] += 1
            raise
        except ValidationError as exc:
            kinds = {error['type'] for error in exc.errors(include_input=False, include_context=False, include_url=False)}
            self.measurements.counts['json_failures' if kinds == {'json_invalid'} else 'schema_failures'] += 1
            raise

    def close(self):
        if self.transport is not None:
            self.transport.close()


def latency_summary(values: list[float]) -> dict:
    ordered = sorted(values)
    return {'queries': len(values), 'p50_ms': round(statistics.median(values), 3) if values else None,
            'p95_ms': round(ordered[max(0, math.ceil(.95*len(ordered))-1)], 3) if ordered else None,
            'total_s': round(sum(values)/1000, 3)}


def quality_summary(results: list[dict]) -> dict:
    return {'gold': aggregate(results),
            'splits': {split: aggregate([row for row in results if row['split'] == split])
                       for split in sorted({row['split'] for row in results})},
            'stages': {stage: aggregate([row for row in results if row['stage'] == stage])
                       for stage in sorted({row['stage'] for row in results})}}


def run_comparison(rows: list[dict], catalog: list, hotels: list[Hotel],
                   client_factory: Callable[[], MeasuredLLMClient], measurements: Measurements,
                   *, progress: Callable[[dict], None] | None = None, progress_every: int = 10) -> dict:
    baseline_results, live_results, records = [], [], []
    baseline_latency, live_latency = [], []
    modes = Counter({'live': 0, 'cache': 0, 'offline': 0})
    changed_ids = []
    for index, row in enumerate(rows, 1):
        started = time.monotonic()
        baseline = parse_case(row, catalog, hotels)
        baseline_ms = (time.monotonic()-started)*1000
        before = measurements.snapshot()
        client = client_factory()
        started = time.monotonic()
        try:
            actual = parse_case(row, catalog, hotels, llm=client)
        finally:
            client.close()
        elapsed_ms = (time.monotonic()-started)*1000
        baseline_result, live_result = compare_case(row, baseline), compare_case(row, actual)
        baseline_results.append(baseline_result)
        live_results.append(live_result)
        baseline_latency.append(baseline_ms)
        live_latency.append(elapsed_ms)
        modes[actual['mode']] += 1
        changed = {key: value for key, value in baseline.items() if key != 'mode'} != {
            key: value for key, value in actual.items() if key != 'mode'}
        if changed:
            changed_ids.append(row['id'])
        delta = measurements.delta(before)
        records.append({'id': row['id'], 'split': row['split'], 'mode': actual['mode'],
                        'offline_exact': baseline_result['exact'], 'live_exact': live_result['exact'],
                        'offline_criteria_exact': baseline_result['criteria_exact'], 'live_criteria_exact': live_result['criteria_exact'],
                        'intent_changed': changed, 'offline_latency_ms': round(baseline_ms, 3),
                        'live_latency_ms': round(elapsed_ms, 3), **delta})
        if progress and (index == 1 or index % progress_every == 0 or index == len(rows)):
            progress({'done': index, 'total': len(rows), 'mode_counts': dict(modes), **measurements.snapshot()})
    return {'methodology': 'Authored synthetic intent benchmark without independent human validation. '
            'Identical catalogue, request text and deterministic context projection in both arms. '
            'No hotel factual values, database writes, raw provider responses or prompt logs. '
            'Provider fallback is reported as offline, not as live success.',
            'offline': {**quality_summary(baseline_results), 'latency': latency_summary(baseline_latency)},
            'live_path': {**quality_summary(live_results), 'latency': latency_summary(live_latency), 'mode_counts': dict(modes)},
            'observability': {**measurements.snapshot(), 'max_requests_in_flight': measurements.max_active_requests,
                              'requested_product_budgets_s': sorted(measurements.requested_budgets),
                              'effective_budgets_s': sorted(measurements.effective_budgets),
                              'queries_with_timeout': sum(row['counts'].get('timeouts', 0) > 0 for row in records),
                              'queries_with_schema_failure': sum(row['counts'].get('schema_failures', 0) > 0 or
                                                                row['counts'].get('schema_format_rejections', 0) > 0 or
                                                                row['counts'].get('envelope_schema_failures', 0) > 0 for row in records),
                              'queries_with_json_failure': sum(row['counts'].get('json_failures', 0) > 0 for row in records),
                              'provider_skipped_queries': sum(row['counts'].get('structured_calls', 0) == 0 for row in records),
                              'offline_fallback_queries': sum(row['mode'] == 'offline' and row['counts'].get('structured_calls', 0) > 0 for row in records)},
            'comparison': {'changed_intent_queries': len(changed_ids), 'changed_ids': changed_ids,
                           'exact_gained': sum(not row['offline_exact'] and row['live_exact'] for row in records),
                           'exact_lost': sum(row['offline_exact'] and not row['live_exact'] for row in records)},
            'records': records}


def validate_local_configuration(client: LLMClient) -> None:
    parsed = urlsplit(client.base_url)
    if client.mode != 'live' or not client.model:
        raise ValueError('Configure live mode and a local model explicitly for this benchmark')
    if parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise ValueError('This host-native benchmark only accepts a loopback provider URL')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Provider URL must not contain credentials or query parameters')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gold', type=Path, default=ROOT/'data/eval/queries_gold.jsonl')
    parser.add_argument('--catalog-root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--progress-every', type=int, default=10)
    parser.add_argument('--experimental-budget-s', type=float)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1 or args.progress_every < 1:
        parser.error('limit and progress-every must be positive')
    if args.experimental_budget_s is not None and args.experimental_budget_s <= 0:
        parser.error('experimental-budget-s must be positive')
    configuration = LLMClient()
    try:
        validate_local_configuration(configuration)
    except ValueError as exc:
        parser.error(str(exc))
    rows = load_jsonl(args.gold)
    if args.limit:
        rows = rows[:args.limit]
    catalog = catalogue(args.catalog_root)
    hotels = [Hotel.model_validate(row) for row in json.loads((args.catalog_root/'data/seed/hotels.json').read_text())]
    cache, measurements = MemoryLLMCache(), Measurements()
    started_at = datetime.now(UTC).isoformat()
    report = run_comparison(rows, catalog, hotels,
                            lambda: MeasuredLLMClient(cache, measurements, experimental_budget_s=args.experimental_budget_s),
                            measurements, progress=lambda value: print(json.dumps(value), file=sys.stderr, flush=True),
                            progress_every=args.progress_every)
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    report['provenance'] = {'started_at': started_at, 'finished_at': datetime.now(UTC).isoformat(),
                            'code_commit': commit, 'proof_kind': 'host_native_local_provider',
                            'docker_bridge_validated': False, 'model_reset_before_run': False,
                            'model': configuration.model, 'base_url': configuration.base_url,
                            'configured_timeout_s': configuration.timeout, 'cache': 'standalone_memory_initially_empty',
                            'cache_entries_after_run': len(cache.rows),
                            'catalog_traits': len(catalog),
                            'catalog_sha256': hashlib.sha256(json.dumps([s.model_dump(mode='json') for s in catalog],
                                                                       sort_keys=True).encode()).hexdigest(),
                            'gold_sha256': hashlib.sha256(args.gold.read_bytes()).hexdigest(),
                            'experimental_budget_override_s': args.experimental_budget_s,
                            'product_deadline_unchanged': args.experimental_budget_s is None}
    if contains_runtime_secret(report, (configuration.api_key,)):
        raise RuntimeError('Benchmark report failed the protected-value output check')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'records'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
