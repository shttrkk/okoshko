"""Context boundaries and Wi-Fi presence are semantic regressions, not hotel facts."""
from copy import deepcopy

import pytest

from backend.agents.query_parser import parse_query
from backend.agents.query_rules import apply_region_coverage, validate_query_plan
from backend.contracts import SearchRequest
from backend.llm.client import UnsafeOutput
from scripts.parser_eval import catalogue


@pytest.fixture(scope="module")
def specs():
    return catalogue()


def parse(text, specs):
    return parse_query(SearchRequest(text=text), specs)


def conditions(parsed, group="must"):
    return {(c.characteristic_id, c.field, c.op, c.value) for c in getattr(parsed, group)}


@pytest.mark.parametrize("text", [
    "Мы вчера гуляли в парке, потом у аптеки встретили приятеля.",
    "Вчера мы гуляли возле моря, потом сидели в парке.",
    "Позавчера мы были в парке и у аптеки.",
    "Я на прошлой неделе гуляла у моря, потом увидела аптеку.",
    "Поставь зарядку телефона у восточного окна, я заберу после работы.",
    "Положите смартфон у западного окна, мы заберем его после работы.",
    "Подключи планшет возле южного окна, потом возьму после работы.",
])
def test_past_report_or_device_errand_does_not_become_hotel_criteria(text, specs):
    parsed = parse(text, specs)
    assert not parsed.must and not parsed.nice and not parsed.no_data
    assert parsed.facade is None and parsed.min_floor is None
    assert parsed.clarifications  # Ambiguous discourse can ask what the guest needs.


@pytest.mark.parametrize("context", [
    "Мы вчера гуляли в парке, потом у аптеки встретили приятеля",
    "Поставь зарядку телефона у восточного окна, я заберу после работы",
])
@pytest.mark.parametrize("text", ["{context}, но сейчас хочу тихий номер.",
                                 "Нужен тихий номер, {context}.",
                                 "{context}. Нужен тихий номер."])
def test_neighbouring_explicit_current_requirement_survives(context, text, specs):
    parsed = parse(text.format(context=context), specs)
    assert conditions(parsed) == {("quiet_night", "available", "eq", True)}
    assert parsed.facade is None


def test_strong_sentence_boundary_does_not_swallow_a_new_short_nominal_query(specs):
    parsed = parse("Вчера гуляли в парке; парковка рядом", specs)
    assert conditions(parsed) == {("car_access", "parking_distance_m", "lte", 500)}


@pytest.mark.parametrize("text, expected", [
    ("парковка рядом", {"car_access"}),
    ("с собакой 25 кг", {"pets"}),
    ("тихий номер с видом на закат недалеко от моря", {"quiet_night", "sunset_view", "beach_walk"}),
    ("Хочу восточное окно для телефонных созвонов", {"room_orientation", "connectivity"}),
    ("Нужен тихий номер, телефон для работы беру с собой", {"quiet_night", "connectivity"}),
])
def test_no_global_phone_or_bare_query_ban(text, expected, specs):
    parsed = parse(text, specs)
    assert {c.characteristic_id for c in parsed.must} == expected


@pytest.mark.parametrize("text", [
    "Ищу гостиницу с Wi-Fi, измеренная скорость не обязательна.",
    "Нужен Wi-Fi, скорость мне не важна.",
    "Ищу гостиницу с Wi-Fi без требований к скорости.",
    "Достаточно наличия Wi-Fi.",
    "Просто Wi-Fi.",
    "Хочу, чтобы был Wi-Fi.",
    "Нужен Wi-Fi, скорость Wi-Fi не принципиальна.",
])
def test_explicit_presence_is_not_a_measured_mbps_constraint(text, specs):
    parsed = parse(text, specs)
    assert conditions(parsed) == {("connectivity", "wifi_reported", "eq", True)}
    assert "Сообщено наличие Wi-Fi" in parsed.must[0].label
    assert not parsed.no_data


def test_presence_uses_coverage_of_the_requested_field_not_the_default_numeric_field(specs):
    parsed = parse("Ищу гостиницу с Wi-Fi, измеренная скорость не обязательна.", specs)
    apply_region_coverage(parsed, {"connectivity": {"status": "no_data", "fields": {
        "wifi_reported": {"status": "partial"}, "download_mbps": {"status": "no_data"}}}})
    assert conditions(parsed) == {("connectivity", "wifi_reported", "eq", True)}
    assert not parsed.no_data and not parsed.no_data_must


@pytest.mark.parametrize("text", ["Нужен интернет", "Интернет для работы", "Нужен Wi-Fi для работы",
                                 "Нужны созвоны", "Скорость не важна. Нужен Wi-Fi для работы."])
def test_unqualified_work_and_internet_defaults_stay_measured(text, specs):
    assert conditions(parse(text, specs)) == {("connectivity", "download_mbps", "gte", 30)}


@pytest.mark.parametrize("text", [
    "Нужен Wi-Fi от 100 Мбит/с.",
    "Просто Wi-Fi; для работы нужен интернет от 100 Мбит/с.",
    "Ищу гостиницу с Wi-Fi, измеренная скорость не обязательна, но интернет от 100 Мбит/с нужен.",
])
def test_presence_wording_never_removes_an_explicit_mbps_bound(text, specs):
    assert conditions(parse(text, specs)) == {("connectivity", "download_mbps", "gte", 100)}


@pytest.mark.parametrize("value, op", [(30, "eq"), ("true", "eq"), (True, "gte"), (True, "contains")])
def test_wifi_presence_plan_only_accepts_boolean_equality(value, op, specs):
    baseline = parse("Просто Wi-Fi", specs)
    plan = deepcopy(baseline)
    plan.must[0].value, plan.must[0].op = value, op
    with pytest.raises(UnsafeOutput):
        validate_query_plan(plan, baseline, specs)


def test_live_proposal_cannot_turn_presence_into_a_speed_claim(specs):
    baseline = parse("Просто Wi-Fi", specs)
    assert conditions(validate_query_plan(baseline, baseline, specs)) == conditions(baseline)
    proposal = deepcopy(baseline)
    proposal.must[0].field, proposal.must[0].op, proposal.must[0].value = "download_mbps", "gte", 30
    with pytest.raises(UnsafeOutput):
        validate_query_plan(proposal, baseline, specs)
