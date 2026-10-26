"""«Город» must not be read as «горы»; work-and-internet phrases are consumed."""
from pathlib import Path

import pytest
import yaml

from backend.agents.query_parser import parse_query
from backend.contracts import CharacteristicSpec, SearchRequest


@pytest.fixture
def catalog():
    root = Path(__file__).resolve().parents[1] / "data/characteristics"
    return [CharacteristicSpec.model_validate(yaml.safe_load(p.read_text())) for p in sorted(root.glob("*.yaml"))]


def ids(criteria):
    return [c.characteristic_id for c in criteria]


def test_city_view_is_not_mountain_view(catalog):
    parsed = parse_query(SearchRequest(text="номер с видом на город для работы с хорошим интернетом"), catalog)
    assert "mountain_view" not in ids(parsed.must + parsed.nice)
    assert any("Вид на город" in c for c in parsed.clarifications)
    assert not parsed.unmapped, parsed.unmapped


@pytest.mark.parametrize("text", ["вид на горы", "номер с видом на горы", "горный пейзаж из окна"])
def test_mountain_view_still_recognised(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert "mountain_view" in ids(parsed.must + parsed.nice)


@pytest.mark.parametrize("text", ["быстрый интернет для созвонов", "нужна стабильная связь для работы", "удалённая работа, нужен быстрый интернет"])
def test_work_internet_phrases_are_consumed(catalog, text):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert "connectivity" in ids(parsed.must + parsed.nice + parsed.no_data)
    assert not parsed.unmapped, parsed.unmapped
