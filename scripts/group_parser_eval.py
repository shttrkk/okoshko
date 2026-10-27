"""Evaluate authored synthetic group intent cases; this is not guest or hotel accuracy."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from backend.agents.query_parser import parse_query
from backend.contracts import SearchRequest
from scripts.parser_eval import catalogue, load_jsonl, write_report

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ('must', 'nice', 'no_data', 'no_data_must')
SCOPE_KEYS = ('facade', 'floor_min', 'floor_max', 'month', 'room_type')


def canonical_scope(value: dict | None) -> dict:
    return {key: value[key] for key in SCOPE_KEYS if value and value.get(key) is not None}


def canonical_criterion(value: dict, fields: dict[str, str]) -> dict:
    cid = value['characteristic_id']
    scalar = value.get('value', True)
    if isinstance(scalar, float) and scalar.is_integer():
        scalar = int(scalar)
    return {'characteristic_id': cid, 'field': value.get('field') or fields.get(cid, 'available'),
            'op': value.get('op', 'eq'), 'value': scalar,
            'no_stairs': bool(value.get('no_stairs')), 'no_crossings': bool(value.get('no_crossings')),
            'scope': canonical_scope(value.get('scope'))}


def criteria(value: dict, fields: dict[str, str]) -> Counter:
    return Counter(json.dumps([group, canonical_criterion(item, fields)], ensure_ascii=False, sort_keys=True)
                   for group in GROUPS for item in value.get(group, []))


def canonical_room(value: dict, fields: dict[str, str]) -> dict:
    return {'criteria': sorted(criteria(value, fields).elements()), 'scope': canonical_scope(value.get('scope')),
            'unmapped': sorted(value.get('unmapped', []))}


def compare_case(row: dict, actual: dict, fields: dict[str, str]) -> dict:
    expected = row['expected']
    wanted_rooms, got_rooms = expected.get('rooms', []), actual.get('rooms', [])
    room_exact = [canonical_room(room, fields) for room in wanted_rooms] == [
        canonical_room(room, fields) for room in got_rooms]
    shared_exact = criteria(expected, fields) == criteria(actual, fields)
    shared_exact = shared_exact and sorted(expected.get('unmapped', [])) == sorted(actual.get('unmapped', []))
    context_exact = all(actual.get(key) == expected[key] for key in
                        ('month', 'facade', 'min_floor', 'check_in', 'check_out', 'pet_weight_kg') if key in expected)
    label_exact = [room['label'] for room in wanted_rooms] == [room['label'] for room in got_rooms]
    count_exact = len(wanted_rooms) == len(got_rooms)
    result = {'id': row['id'], 'count_exact': count_exact, 'expected_room_count': len(wanted_rooms),
              'actual_room_count': len(got_rooms), 'ordered_room_criteria_scope_exact': room_exact,
              'shared_exact': shared_exact, 'context_exact': context_exact, 'labels_exact': label_exact,
              'semantic_exact': count_exact and room_exact and shared_exact and context_exact}
    if not result['semantic_exact'] or not label_exact:
        result['expected'] = expected
        result['actual'] = {key: actual.get(key) for key in
                            ('must', 'nice', 'no_data', 'no_data_must', 'rooms', 'unmapped',
                             'month', 'check_in', 'check_out', 'facade', 'min_floor', 'clarifications')}
        result['text'] = row['text']
    return result


def aggregate(results: list[dict]) -> dict:
    total = len(results)
    metrics = {'queries': total}
    for key in ('count_exact', 'ordered_room_criteria_scope_exact', 'shared_exact', 'context_exact',
                'labels_exact', 'semantic_exact'):
        count = sum(row[key] for row in results)
        metrics[key] = {'correct': count, 'total': total, 'rate': round(count/total, 6) if total else None}
    return metrics


def evaluate(gold: list[dict], traps: list[dict]) -> dict:
    catalog = catalogue()
    fields = {spec.id: spec.search.field for spec in catalog}
    results = [compare_case(row, parse_query(SearchRequest(text=row['text'], **row.get('request', {})),
                                           catalog).model_dump(mode='json'), fields) for row in gold]
    trap_results = []
    for row in traps:
        parsed = parse_query(SearchRequest(text=row['text'], **row.get('request', {})), catalog)
        trap_results.append({'id': row['id'], 'false_split': bool(parsed.rooms), 'room_count': len(parsed.rooms)})
    false_splits = sum(row['false_split'] for row in trap_results)
    return {'methodology': 'Authored synthetic training intent cases, not independently human validated; '
            'not guest accuracy, room availability or hotel accuracy. Room order is significant; '
            'labels are reported separately; omitted lists are empty.',
            'synthetic': True, 'human_labeled': False, 'gold': aggregate(results),
            'traps': {'queries': len(trap_results), 'false_splits': false_splits,
                      'false_split_rate': round(false_splits/len(trap_results), 6) if trap_results else None},
            'failures': [row for row in results if not row['semantic_exact'] or not row['labels_exact']],
            'trap_failures': [row for row in trap_results if row['false_split']]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gold', type=Path, default=ROOT/'data/eval/group_gold.jsonl')
    parser.add_argument('--traps', type=Path, default=ROOT/'data/eval/group_traps.jsonl')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = evaluate(load_jsonl(args.gold), load_jsonl(args.traps))
    report['corpus_sha256'] = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                               for name, path in (('gold', args.gold), ('traps', args.traps))}
    report['parser_sha256'] = {name: hashlib.sha256((ROOT/'backend/agents'/name).read_bytes()).hexdigest()
                              for name in ('query_parser.py', 'query_rules.py', 'query_text.py', 'group_query.py')
                              if (ROOT/'backend/agents'/name).exists()}
    if args.output:
        write_report(args.output, report)
    print(json.dumps({key: value for key, value in report.items() if key not in {'failures', 'trap_failures'}},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
