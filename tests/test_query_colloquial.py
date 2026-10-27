"""Comparatives such as «тише» and inverted amounts such as «минут 5»."""
from pathlib import Path

import pytest
import yaml

from backend.agents.query_parser import parse_query
from backend.contracts import CharacteristicSpec, SearchRequest


@pytest.fixture
def catalog():
    root = Path(__file__).resolve().parents[1] / "data/characteristics"
    return [CharacteristicSpec.model_validate(yaml.safe_load(p.read_text())) for p in sorted(root.glob("*.yaml"))]


def criteria(parsed):
    return {(c.characteristic_id, c.op, c.value) for c in parsed.must + parsed.nice}


@pytest.mark.parametrize("text", ["как Cascade, но тише", "как Cascade, только потише", "что-нибудь поспокойнее у моря", "менее шумный район"])
def test_quieter_comparative_requires_quiet_night(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert "quiet_night" in {c.characteristic_id for c in parsed.must}


@pytest.mark.parametrize(("text", "expected"), [
    ("до пляжа минут 5", ("beach_walk", "lte", 5)),
    ("до пляжа минут 5-10", ("beach_walk", "lte", 10)),
    ("аптека метров 300", ("night_pharmacy", "lte", 300)),
])
def test_inverted_amount_sets_threshold(catalog, text, expected):
    assert expected in criteria(parse_query(SearchRequest(text=text), catalog))


def test_inverted_amount_stays_with_its_condition(catalog):
    parsed = parse_query(SearchRequest(text="до пляжа 10 минут, аптека метров 400"), catalog)
    assert {("beach_walk", "lte", 10), ("night_pharmacy", "lte", 400)} <= criteria(parsed)
