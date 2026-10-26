"""Reproducible intent evaluation, separate from hotel relevance or factual accuracy.

The JSONL gold is authored synthetic language, not user logs or human validation. Its fixed
development / holdout split is published before parser changes. Context cases use
explicit unavailable trait IDs and the existing hotel-name resolver; these stages
are reported separately and do not imply that text parsing can infer coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agents.query_parser import parse_query  # noqa: E402
from backend.agents.query_rules import apply_region_coverage  # noqa: E402
from backend.agents.similarity import reference_hotel  # noqa: E402
from backend.contracts import CharacteristicSpec, Hotel, SearchRequest  # noqa: E402

GROUPS = ('must', 'nice', 'no_data')
CRITERION_KEYS = ('characteristic_id', 'field', 'op', 'value', 'scope', 'no_stairs', 'no_crossings')


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def catalogue(root: Path = ROOT) -> list[CharacteristicSpec]:
    return [CharacteristicSpec.model_validate(yaml.safe_load(path.read_text()))
            for path in sorted((root / 'data/characteristics').glob('*.yaml'))]


def canonical_criterion(value: dict) -> dict:
    result = {key: value.get(key) for key in CRITERION_KEYS}
    result['no_stairs'] = bool(result['no_stairs'])
    result['no_crossings'] = bool(result['no_crossings'])
    result['scope'] = {key: item for key, item in (result['scope'] or {}).items()
                       if item is not None and key != 'type'} or None
    if isinstance(result['value'], float) and result['value'].is_integer():
        result['value'] = int(result['value'])
    return result


def criterion_multiset(value: dict) -> Counter:
    return Counter(json.dumps([group, canonical_criterion(c)], ensure_ascii=False, sort_keys=True)
                   for group in GROUPS for c in value.get(group, []))


def parse_case(row: dict, catalog: list[CharacteristicSpec], hotels: list[Hotel], llm=None) -> dict:
    parsed = parse_query(SearchRequest(text=row['text'], **row.get('request', {})), catalog, llm=llm)
    context = row.get('context', {})
    unavailable = set(context.get('no_data_ids', []))
    if unavailable:
        apply_region_coverage(parsed, {cid: {'status': 'no_data'} for cid in unavailable})
    if 'similar_to_name' in row['expected']:
        hotel, ambiguous = reference_hotel(row['text'], hotels)
        if hotel is not None and not ambiguous:
            parsed.similar_to, parsed.similar_to_name = hotel.id, hotel.name
            parsed.unmapped = []
    return parsed.model_dump(mode='json')


def compare_case(row: dict, actual: dict) -> dict:
    expected = row['expected']
    want, got = criterion_multiset(expected), criterion_multiset(actual)
    errors = []
    missing, extra = want-got, got-want
    if missing:
        errors.append({'missing_criteria': [json.loads(item) for item in missing.elements()]})
    if extra:
        errors.append({'extra_criteria': [json.loads(item) for item in extra.elements()]})
    for key in ('month', 'facade', 'min_floor', 'pet_weight_kg', 'check_in', 'check_out', 'persona', 'similar_to_name'):
        if key in expected and actual.get(key) != expected[key]:
            errors.append({key: {'expected': expected[key], 'actual': actual.get(key)}})
    unmapped_ok = True
    if expected.get('unmapped') is not None and 'unmapped' in expected:
        if sorted(actual.get('unmapped', [])) != sorted(expected['unmapped']):
            unmapped_ok = False
            errors.append({'unmapped': {'expected': expected['unmapped'], 'actual': actual.get('unmapped')}})
    for fragment in expected.get('unmapped_contains', []):
        if not any(fragment in value for value in actual.get('unmapped', [])):
            unmapped_ok = False
            errors.append({'missing_unmapped': fragment})
    if expected.get('clarification_contains') and not any(
            expected['clarification_contains'] in value for value in actual.get('clarifications', [])):
        errors.append({'missing_clarification': expected['clarification_contains']})
    return {'id': row['id'], 'text': row['text'], 'split': row['split'], 'exact': not errors,
            'stage': ('coverage_projection' if row.get('context', {}).get('no_data_ids')
                      else 'similarity_resolution' if 'similar_to_name' in expected else 'semantic_extraction'),
            'criteria_exact': not missing and not extra,
            'true_positive': sum((want & got).values()), 'expected_count': sum(want.values()),
            'actual_count': sum(got.values()), 'unmapped_ok': unmapped_ok,
            'unmapped_evaluated': (expected.get('unmapped') is not None and 'unmapped' in expected)
                                  or bool(expected.get('unmapped_contains')),
            'has_unmapped': bool(actual.get('unmapped')), 'errors': errors}


def aggregate(results: list[dict]) -> dict:
    total = len(results)
    true_positive = sum(row['true_positive'] for row in results)
    expected = sum(row['expected_count'] for row in results)
    actual = sum(row['actual_count'] for row in results)
    unmapped_checked = [row for row in results if row.get('unmapped_evaluated', True)]
    return {'queries': total, 'exact_matches': sum(row['exact'] for row in results),
            'exact_match_rate': round(sum(row['exact'] for row in results)/total, 6) if total else None,
            'criterion_precision': round(true_positive/actual, 6) if actual else None,
            'criterion_recall': round(true_positive/expected, 6) if expected else None,
            'criterion_true_positive': true_positive, 'criterion_expected': expected, 'criterion_actual': actual,
            'criteria_exact_match_rate': round(sum(row['criteria_exact'] for row in results)/total, 6) if total else None,
            'unmapped_expectation_accuracy': round(sum(row['unmapped_ok'] for row in unmapped_checked)/len(unmapped_checked), 6) if unmapped_checked else None,
            'unmapped_evaluated_queries': len(unmapped_checked),
            'queries_with_unmapped': sum(row['has_unmapped'] for row in results)}


def evaluate(gold: list[dict], negatives: list[dict], catalog=None, hotels=None) -> dict:
    catalog = catalog or catalogue()
    hotels = hotels if hotels is not None else [Hotel.model_validate(row) for row in json.loads(
        (ROOT/'data/seed/hotels.json').read_text())]
    results = [compare_case(row, parse_case(row, catalog, hotels)) for row in gold]
    negative_results = [compare_case(row, parse_case(row, catalog, hotels)) for row in negatives]
    false_queries = sum(row['actual_count'] > 0 for row in negative_results)
    return {'methodology': 'Authored synthetic intent cases, without independent human validation; not hotel accuracy or nDCG. '
            'no_data uses explicit authoritative fixture context; similarity uses existing catalogue resolver.',
            'gold': aggregate(results),
            'splits': {split: aggregate([row for row in results if row['split'] == split])
                       for split in sorted({row['split'] for row in results})},
            'stages': {stage: aggregate([row for row in results if row['stage'] == stage])
                       for stage in sorted({row['stage'] for row in results})},
            'negative': {'queries': len(negative_results), 'queries_with_false_criteria': false_queries,
                         'false_criteria_rate': round(false_queries/len(negative_results), 6) if negative_results else None,
                         'criteria_emitted': sum(row['actual_count'] for row in negative_results)},
            'failures': [row for row in [*results, *negative_results] if not row['exact']]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gold', type=Path, default=ROOT/'data/eval/queries_gold.jsonl')
    parser.add_argument('--negative', type=Path, default=ROOT/'data/eval/queries_negative.jsonl')
    parser.add_argument('--split', choices=('development', 'holdout'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--failures', action='store_true')
    args = parser.parse_args()
    gold = load_jsonl(args.gold)
    if args.split:
        gold = [row for row in gold if row['split'] == args.split]
    report = evaluate(gold, load_jsonl(args.negative))
    report['corpus_sha256'] = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                               for name, path in [('gold', args.gold), ('negative', args.negative)]}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report if args.failures else {key: value for key, value in report.items() if key != 'failures'},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
