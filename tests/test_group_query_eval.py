"""Evaluation checks ownership and domain, with labels reported independently."""
from scripts.group_parser_eval import canonical_criterion, compare_case


def test_criterion_defaults_numeric_values_and_scope_are_canonical():
    expected = {'characteristic_id': 'beach_walk', 'value': 10, 'op': 'lte'}
    actual = dict(expected, value=10.0, field='minutes', label='До моря', weight=2,
                  no_stairs=False, scope={'type': 'hotel', 'month': None})
    assert canonical_criterion(expected, {'beach_walk': 'minutes'}) == canonical_criterion(actual, {'beach_walk': 'minutes'})


def test_omitted_lists_are_empty_and_labels_are_not_semantic_exact():
    row = {'id': 'case', 'text': 'x', 'expected': {'rooms': [{'label': 'Маме', 'scope': {'floor_max': 3}}]}}
    actual = {'rooms': [{'label': 'Номер родителей', 'scope': {'floor_max': 3}}]}
    report = compare_case(row, actual, {})
    assert report['semantic_exact'] and not report['labels_exact']


def test_room_assignment_and_floor_ceiling_affect_exact_even_with_same_count():
    sea = {'characteristic_id': 'sea_view'}
    quiet = {'characteristic_id': 'quiet_night'}
    row = {'id': 'case', 'text': 'x', 'expected': {'rooms': [
        {'label': 'Маме', 'must': [quiet], 'scope': {'floor_max': 3}}, {'label': 'Нам', 'must': [sea]}]}}
    actual = {'rooms': [{'label': 'Маме', 'must': [sea], 'scope': {'floor_max': 3}},
                        {'label': 'Нам', 'must': [quiet]}]}
    report = compare_case(row, actual, {})
    assert report['count_exact'] and report['labels_exact'] and not report['ordered_room_criteria_scope_exact']
    actual = row['expected'].copy()
    actual['rooms'] = [dict(row['expected']['rooms'][0], scope={}), row['expected']['rooms'][1]]
    assert not compare_case(row, actual, {})['ordered_room_criteria_scope_exact']


def test_required_unknown_is_one_condition_with_or_without_presentation_copy():
    from scripts.group_parser_eval import criteria
    unknown = {"characteristic_id": "room_orientation", "field": "courtyard", "value": True}
    assert criteria({"no_data_must": [unknown]}, {}) == criteria({"no_data_must": [unknown], "no_data": [unknown]}, {})
    assert criteria({"no_data": [unknown]}, {}) != criteria({"no_data_must": [unknown]}, {})


def test_positive_case_cannot_be_counted_as_a_false_split_trap():
    import pytest

    from scripts.group_parser_eval import evaluate
    with pytest.raises(ValueError, match="expected.rooms"):
        evaluate([], [{"id": "misplaced", "text": "two rooms", "expected": {"rooms": [{"label": "A"}]}}])


def test_single_room_guard_applies_to_inflected_unique_room_reference():
    from backend.agents.query_parser import parse_query
    from backend.contracts import SearchRequest
    from scripts.parser_eval import catalogue
    parsed = parse_query(SearchRequest(text="Раньше маме дали номер с морем, а брату — с горами. Для нашего единственного номера нужна тишина."), catalogue())
    assert parsed.rooms == []
