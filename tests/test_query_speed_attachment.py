"""Typed network units can qualify one adjacent network request, not another trait."""
import pytest

from backend.agents.query_parser import parse_query
from backend.contracts import SearchRequest
from scripts.parser_eval import catalogue


@pytest.fixture(scope="module")
def specs():
    return catalogue()


def parsed(text, specs):
    return parse_query(SearchRequest(text=text), specs)


def network(plan, group="must"):
    return {(c.field, c.op, c.value) for c in getattr(plan, group) if c.characteristic_id == "connectivity"}


@pytest.mark.parametrize("connector", [", ", ", но ", ", при этом ", "; ", " но "])
@pytest.mark.parametrize("unit, threshold, expected", [("Mbps", "100", 100), ("Мбит/с", "100", 100),
                                                     ("МБ/с", "12.5", 100), ("MB/s", "12,5", 100)])
def test_explicit_typed_speed_supersedes_the_default_without_a_repeated_noun(connector, unit, threshold, expected, specs):
    plan = parsed(f"Wi-Fi{connector}не меньше{threshold}{unit}", specs)
    assert network(plan) == {("download_mbps", "gte", expected)}
    assert not network(plan, "nice")


@pytest.mark.parametrize("text, must, nice", [
    ("Желательно Wi-Fi, не меньше100Mbps", set(), {("download_mbps", "gte", 100)}),
    ("Желательно Wi-Fi, но не меньше100Mbps обязательно", {("download_mbps", "gte", 100)}, set()),
    ("Нужен Wi-Fi, желательно не меньше100Mbps", {("download_mbps", "gte", 30)}, {("download_mbps", "gte", 100)}),
    ("Нужен Wi-Fi, при этом не меньше100Mbps необязательно", {("download_mbps", "gte", 30)}, {("download_mbps", "gte", 100)}),
    ("Нужен Wi-Fi, при этом не больше50Mbps", {("download_mbps", "lte", 50)}, set()),
    ("Wi-Fi, но скорость не ниже100Мбит/с", {("download_mbps", "gte", 100)}, set()),
])
def test_attached_measurement_keeps_its_priority_and_comparison(text, must, nice, specs):
    plan = parsed(text, specs)
    assert network(plan) == must and network(plan, "nice") == nice


def test_a_soft_interval_does_not_accidentally_create_hard_bounds(specs):
    plan = parsed("Желательно Wi-Fi, при этом от20Mbps до100Mbps", specs)
    assert network(plan) == set()
    assert network(plan, "nice") == {("download_mbps", "gte", 20), ("download_mbps", "lte", 100)}


@pytest.mark.parametrize("text, expected", [
    ("Без Wi-Fi, но не меньше100Мбит/с", {("available", "eq", False)}),
    ("Wi-Fi не нужен, но не меньше100Mbps", set()),
    ("Не нужен Wi-Fi, при этом не меньше100Mbps", set()),
])
def test_negated_or_rejected_network_anchor_is_not_revived_by_a_number(text, expected, specs):
    assert network(parsed(text, specs)) == expected


@pytest.mark.parametrize("tail", ["но аптека в100м", "но парковка в100м", "но номер100", "но этаж10",
                                 "но собака весом25кг", "но цена10000руб", "но 100", "но 100кг", "но 100м"])
def test_nonspeed_units_and_numbers_never_become_network_speed(tail, specs):
    assert network(parsed("Нужен Wi-Fi, " + tail, specs)) == {("download_mbps", "gte", 30)}


@pytest.mark.parametrize("text", [
    "Wi-Fi и парковка, но не меньше100Mbps",
    "Wi-Fi, но у аптеки не меньше100Mbps",
    "Wi-Fi, но цена100Mbps",
    "Wi-Fi, но номер100Mbps",
    "Wi-Fi, но на седьмом этаже100Mbps",
])
def test_attachment_does_not_cross_an_incompatible_anchor(text, specs):
    assert network(parsed(text, specs)) == {("download_mbps", "gte", 30)}


def test_a_previous_other_trait_does_not_block_the_last_explicit_network_anchor(specs):
    plan = parsed("Нужен тихий номер с Wi-Fi, но не меньше100Mbps", specs)
    assert network(plan) == {("download_mbps", "gte", 100)}
    assert any(c.characteristic_id == "quiet_night" for c in plan.must)


def test_explicit_speed_wins_over_presence_wording_but_a_waiver_stays_presence(specs):
    assert network(parsed("Просто Wi-Fi, но минимум12МБ/с", specs)) == {("download_mbps", "gte", 96)}
    assert network(parsed("Просто Wi-Fi, измеренная скорость не обязательна", specs)) == {("wifi_reported", "eq", True)}


@pytest.mark.parametrize("text", ["Wi-Fi от12МБ/с", "Wi-Fi от12MB/s", "Wi-Fi от12 мб/с"])
def test_byte_units_with_and_without_spacing_are_converted_consistently(text, specs):
    assert network(parsed(text, specs)) == {("download_mbps", "gte", 96)}
