"""Catalog growth must preserve the meaning of unrelated car requests."""
import pytest

from backend.agents.query_parser import parse_query
from backend.contracts import SearchRequest
from scripts.parser_eval import catalogue


@pytest.mark.parametrize('text', ['парковка рядом', 'рядом парковка до 150 м', 'парковка рядом и парк рядом'])
def test_park_is_not_a_parking_prefix(text):
    parsed = parse_query(SearchRequest(text=text), catalogue())
    ids = [c.characteristic_id for c in parsed.must]
    assert ('green_nearby' in ids) == ('парк рядом' in text)
    assert ids.count('car_access') == 1


@pytest.mark.parametrize('text', ['парк рядом', 'рядом парк', 'рядом с парком', 'сквер поблизости'])
def test_explicit_green_space_remains_supported(text):
    parsed = parse_query(SearchRequest(text=text), catalogue())
    assert [c.characteristic_id for c in parsed.must] == ['green_nearby']
