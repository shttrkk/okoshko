"""Property phrasing, activity context and meaningful residual spans."""
import pytest

from backend.agents.query_parser import parse_query
from backend.contracts import SearchRequest
from scripts.parser_eval import catalogue


@pytest.fixture(scope="module")
def specs():
    return catalogue()


def parse(text, specs):
    return parse_query(SearchRequest(text=text), specs)


def conditions(plan, group="must"):
    return {(c.characteristic_id, c.field, c.op, c.value) for c in getattr(plan, group)}


@pytest.mark.parametrize("phrase", ["фотографии вокруг", "фотографии рядом", "снимки поблизости",
                                   "снимки окрестностей", "фото вокруг"])
def test_existing_local_photo_nouns_share_their_declared_locality_meaning(phrase, specs):
    plan = parse("Желательно " + phrase, specs)
    assert not plan.must
    assert conditions(plan, "nice") == {("nearby_photos", "available", "eq", True)}
    assert not plan.unmapped


@pytest.mark.parametrize("text", ["Сделай фотографии вокруг дома", "Сними фотографии вокруг дома",
                                 "Сделай фото окрестностей",
                                 "Я снимаю фотографии вокруг", "Обработай снимки поблизости",
                                 "Мы вчера снимали фотографии вокруг парка",
                                 "Реферат про дистанционную работу", "Дистанционная работа над ошибками",
                                 "Продам свою машину", "У меня своя машина, парковка не нужна",
                                 "Мы вчера ездили на автомобиле", "Свой автомобиль не беру"])
def test_nonsearch_actions_and_vehicle_ownership_do_not_invent_hard_properties(text, specs):
    plan = parse(text, specs)
    assert not plan.must and not plan.nice


def test_photo_purpose_does_not_add_a_gallery_requirement_to_a_real_view_request(specs):
    plan = parse("Хочу вид на море для фотографий вокруг", specs)
    assert conditions(plan) == {("sea_view", "available", "eq", True)}


@pytest.mark.parametrize("text", ["Помоги сделать фото вокруг отеля",
                                 "Хочу сделать снимки поблизости",
                                 "Хочу снять фото вокруг отеля",
                                 "Хочу снять фотографии окрестностей",
                                 "Сделай мне фото вокруг отеля"])
def test_photo_creation_infinitive_is_not_a_request_for_existing_photos(text, specs):
    plan = parse(text, specs)
    assert not plan.must and not plan.nice


@pytest.mark.parametrize("text", ["Хочу снять номер с фотографиями окрестностей",
                                 "Хочу снимать номер с фотографиями окрестностей"])
def test_renting_a_room_does_not_hide_its_requested_photographs(text, specs):
    plan = parse(text, specs)
    assert conditions(plan) == {("nearby_photos", "available", "eq", True)}


def test_creation_clause_preserves_a_real_property_in_the_next_clause(specs):
    plan = parse("Хочу сделать снимки поблизости, но вид на море обязателен", specs)
    assert conditions(plan) == {("sea_view", "available", "eq", True)}


def test_activity_does_not_invent_a_hard_temperature_beside_an_explicit_nice_one(specs):
    plan = parse("Купаться в июле, желательно вода от 22 градусов", specs)
    assert plan.month == 7 and not plan.must
    assert conditions(plan, "nice") == {("climate", "water_c", "gte", 22)}
    assert not plan.unmapped


def test_activity_still_has_the_catalogue_default_without_an_explicit_property(specs):
    plan = parse("Купаться в июле", specs)
    assert conditions(plan) == {("climate", "water_c", "gte", 20)}


def test_explicit_hard_temperature_is_not_removed_by_a_later_nice_bound(specs):
    plan = parse("Купаться, вода не ниже 20 градусов обязательна, желательно вода от 23 градусов", specs)
    assert conditions(plan) == {("climate", "water_c", "gte", 20)}
    assert conditions(plan, "nice") == {("climate", "water_c", "gte", 23)}


@pytest.mark.parametrize("object_noun", ["комнату", "номер"])
def test_blackout_property_consumes_the_object_it_actually_describes(object_noun, specs):
    plan = parse("Шторы должны полностью затемнять " + object_noun, specs)
    assert conditions(plan) == {("soundproofing", "blackout", "eq", True)}
    assert not plan.unmapped


def test_distant_work_is_a_work_domain_phrase_with_the_same_explicit_speed(specs):
    plan = parse("Для дистанционной работы интернет хотя бы 70 мегабит", specs)
    assert conditions(plan) == {("connectivity", "download_mbps", "gte", 70)}
    assert not plan.unmapped


@pytest.mark.parametrize("text", ["Шторы не должны затемнять комнату",
                                 "Шторы не должны полностью затемнять номер"])
def test_a_negated_blackout_predicate_is_not_reported_as_positive(specs, text):
    plan = parse(text, specs)
    assert conditions(plan) == {("soundproofing", "blackout", "eq", False)}
    assert not plan.unmapped


@pytest.mark.parametrize("supported", ["Шторы должны полностью затемнять комнату",
                                      "Для дистанционной работы интернет хотя бы 70 мегабит",
                                      "Желательно фотографии вокруг"])
def test_consuming_a_known_phrase_does_not_remove_an_unsupported_wish(supported, specs):
    plan = parse(supported + ", бассейн обязателен", specs)
    assert any("бассейн" in fragment for fragment in plan.unmapped)
    assert all(c.characteristic_id != "pool" for c in [*plan.must, *plan.nice])
