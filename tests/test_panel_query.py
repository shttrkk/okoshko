"""Panel intent and evidence policy, without inventing measurements or hotel facts."""
from datetime import timedelta

import pytest

from backend.agents.query_match import assess_hotel
from backend.agents.query_parser import parse_query
from backend.agents.query_rules import apply_region_coverage
from backend.agents.ranker import rank_hotels
from backend.agents.similarity import similar_hotels
from backend.api.catalog import search_coverage
from backend.contracts import (
    Criterion,
    Evidence,
    Fact,
    Hotel,
    ParsedQuery,
    QuerySuggestion,
    SearchRequest,
    now,
)
from scripts.parser_eval import catalogue


@pytest.fixture(scope="module")
def specs():
    return catalogue()


def fact(cid, value, *, hotel_id="h", confidence=.55, **updates):
    return Fact(id=hotel_id + "-" + cid, hotel_id=hotel_id, characteristic_id=cid, value=value,
                status="computed", confidence=confidence, supporting_claim_ids=["claim"],
                evidence=[Evidence(id=hotel_id + "-evidence", kind="open_data", label="Открытая карта",
                                   source_ids=["osm"], url="https://example.org/source")],
                valid_until=now() + timedelta(days=1)).model_copy(update=updates)


def hotel(identifier="h"):
    return Hotel(id=identifier, name=identifier, lat=43.58, lon=39.72, floors=5)


def evaluate(item, criterion, specs):
    query = ParsedQuery(text="", must=[criterion])
    return rank_hotels([hotel()], [item], query, specs)[0]


@pytest.mark.parametrize("confidence", [0, .38, .55, .69, .7, .9])
def test_observed_numeric_failure_is_no_without_a_confidence_cutoff(confidence, specs):
    item = fact("nightlife", {"venues_500m": 9}, confidence=confidence)
    hit = evaluate(item, Criterion(characteristic_id="nightlife", label="Заведения", field="venues_500m", op="gte", value=30), specs)
    assert hit.verdicts[0].status == "no" and hit.group != "all"
    assert "по карте мало — 9 из 30" in hit.verdicts[0].text
    assert "Карта заведений может быть неполной" in hit.verdicts[0].caveat
    assert hit.verdicts[0].confidence == confidence


@pytest.mark.parametrize("field,no_stairs", [("minutes", True), ("has_steps", False)])
def test_known_stairs_reject_both_route_forms_at_low_confidence(field, no_stairs, specs):
    item = fact("beach_walk", {"minutes": 7, "has_steps": True, "return_ascent_m": 37})
    criterion = Criterion(characteristic_id="beach_walk", label="Без лестниц", field=field,
                          op="lte" if field == "minutes" else "eq", value=10 if field == "minutes" else False,
                          no_stairs=no_stairs)
    v = evaluate(item, criterion, specs).verdicts[0]
    assert v.status == "no" and "лестницы" in v.text and "37 м" in v.text


@pytest.mark.parametrize("updates", [{"status": "stale"}, {"status": "disputed"}, {"evidence": []},
                                     {"supporting_claim_ids": []}, {"valid_until": now() - timedelta(seconds=1)}])
def test_unusable_evidence_never_becomes_a_known_negative(updates, specs):
    item = fact("nightlife", {"venues_500m": 9}, **updates)
    assert evaluate(item, Criterion(characteristic_id="nightlife", label="Бары", field="venues_500m", op="gte", value=30), specs).verdicts[0].status == "unknown"


@pytest.mark.parametrize("value", [None, float("nan"), True])
def test_missing_or_invalid_number_is_not_interpreted_as_zero(value, specs):
    item = fact("nightlife", {"venues_500m": value})
    assert evaluate(item, Criterion(characteristic_id="nightlife", label="Бары", field="venues_500m", op="gte", value=30), specs).verdicts[0].status == "unknown"


def test_known_negative_orders_below_an_unknown_without_changing_the_utility_formula(specs):
    query = ParsedQuery(text="", must=[Criterion(characteristic_id="nightlife", label="Бары", field="venues_500m", op="gte", value=30)])
    hits = rank_hotels([hotel("bad"), hotel("unknown")], [fact("nightlife", {"venues_500m": 9}, hotel_id="bad", confidence=.6)], query, specs)
    assert [(h.hotel.id, h.score) for h in hits] == [("unknown", 20), ("bad", 2)]


def test_wifi_tag_is_partial_but_cannot_satisfy_a_speed_requirement(specs):
    item = fact("connectivity", {"wifi_reported": True, "download_mbps": None, "available": None})
    criterion = Criterion(characteristic_id="connectivity", label="Работа", field="download_mbps", op="gte", value=30)
    query = ParsedQuery(text="работа", must=[criterion])
    match = assess_hotel([item], query, specs, hotel=hotel()).matches[0]
    assert match.status == "partial" and match.value is None and not match.condition_met and not match.supported
    hit = evaluate(item, criterion, specs)
    assert hit.group != "all" and hit.must_satisfied == 0
    v = hit.verdicts[0]
    assert v.text == "Wi-Fi есть по карте · скорость неизвестна"
    assert "агрегированные тесты" not in v.caveat and not v.hotelier_confirmed
    assert item.value["download_mbps"] is None


def test_measured_slow_wifi_does_not_hide_behind_a_presence_partial(specs):
    item = fact("connectivity", {"wifi_reported": True, "download_mbps": 5})
    v = evaluate(item, Criterion(characteristic_id="connectivity", label="Скорость", field="download_mbps", op="gte", value=30), specs).verdicts[0]
    assert v.status == "no" and "не измерена" not in v.text


@pytest.mark.parametrize("updates,partial", [({}, True), ({"status": "stale"}, False), ({"status": "disputed"}, False),
                                              ({"evidence": []}, False), ({"supporting_claim_ids": []}, False),
                                              ({"valid_until": now() - timedelta(seconds=1)}, False)])
def test_wifi_partial_coverage_requires_current_supporting_provenance(updates, partial, specs):
    item = fact("connectivity", {"wifi_reported": True, "download_mbps": None}, **updates)
    query = ParsedQuery(text="", must=[Criterion(characteristic_id="connectivity", label="Работа", field="download_mbps", op="gte", value=30)])
    coverage = search_coverage([item.model_dump(mode="json")], specs, 1, query)
    row = coverage["connectivity"]
    assert row["known_hotels"] == 0
    assert bool(row["fields"]["download_mbps"].get("partial_evidence")) is partial
    assert row["fields"]["download_mbps"].get("wifi_reported_count", 0) == int(partial)
    apply_region_coverage(query, coverage)
    assert bool(query.must) is partial
    assert bool(query.no_data_must) is not partial


@pytest.mark.parametrize("text", ["море недалеко", "близко к морю", "море рядом", "рядом с морем", "у моря"])
def test_colloquial_sea_proximity_keeps_the_default_threshold(text, specs):
    query = parse_query(SearchRequest(text=text), specs)
    assert [(c.field, c.op, c.value) for c in query.must if c.characteristic_id == "beach_walk"] == [("minutes", "lte", 10)]
    assert not query.unmapped


@pytest.mark.parametrize("text", ["с коляской", "младший в коляске", "едем с детской коляской", "мама в инвалидной коляске"])
def test_stroller_trip_adds_a_hard_stair_constraint_without_inventing_walking_time(text, specs):
    query = parse_query(SearchRequest(text=text), specs)
    routes = [c for c in query.must if c.characteristic_id == "beach_walk"]
    assert [(c.field, c.op, c.value) for c in routes] == [("has_steps", "eq", False)]
    assert not any(c.field == "minutes" for c in routes)


@pytest.mark.parametrize("text", ["едем без коляски, до пляжа", "коляска не нужна, до пляжа", "если поедем с коляской, до пляжа",
                                 "вопрос о фразе «с коляской», до пляжа", "не поедем с коляской, до пляжа",
                                 "сравните поездку с коляской и без неё, до пляжа"])
def test_negative_quoted_conditional_or_comparison_stroller_mentions_do_not_add_a_constraint(text, specs):
    query = parse_query(SearchRequest(text=text), specs)
    assert not any(c.characteristic_id == "beach_walk" and (c.no_stairs or c.field == "has_steps")
                   for c in [*query.must, *query.nice])


def test_stroller_does_not_promote_an_independent_soft_walking_time(specs):
    query = parse_query(SearchRequest(text="едем с коляской, желательно до моря за 20 минут"), specs)
    assert any(c.field == "has_steps" and c.value is False for c in query.must)
    assert any(c.field == "minutes" and c.value == 20 for c in query.nice)
    assert not any(c.field == "minutes" for c in query.must)


def test_suggestion_is_whitelisted_and_remains_unapplied(specs):
    query = parse_query(SearchRequest(text="море недолеко"), specs)
    assert not query.must and not query.nice and query.unmapped
    [suggestion] = query.suggestions
    assert suggestion.criterion.characteristic_id == "beach_walk" and suggestion.criterion.value == 10
    assert suggestion.fragment == "недолеко" and suggestion.required
    assert not parse_query(SearchRequest(text="недолеко"), specs).suggestions
    assert not parse_query(SearchRequest(text="рядом бассейн"), specs).suggestions


def test_group_suggestion_keeps_its_recipient_and_never_becomes_shared_criteria(specs):
    query = parse_query(SearchRequest(text="Едем компанией: Маше — шумоизаляция; Игорю — тихий номер"), specs)
    assert not query.must and not query.suggestions
    assert query.rooms[0].label == "Маше" and query.rooms[0].suggestions[0].criterion.characteristic_id == "soundproofing"
    assert not query.rooms[0].must and not query.rooms[1].suggestions


def test_edited_state_cannot_return_an_executable_suggestion_outside_the_whitelist(specs):
    parsed = ParsedQuery(text="", suggestions=[QuerySuggestion(label="bad", criterion=Criterion(characteristic_id="not_in_catalogue", label="bad"))])
    assert not parse_query(SearchRequest(parsed=parsed), specs).suggestions


def test_display_name_does_not_expand_the_matching_dictionary(specs):
    renamed = [spec.model_copy(update={"display_name": "Абракалистический"}) for spec in specs]
    assert not parse_query(SearchRequest(text="Абракалистический"), renamed).must
    assert any(c.characteristic_id == "evening_heat" for c in parse_query(SearchRequest(text="без вечерней жары"), renamed).must)


def test_requested_similarity_difference_precedes_an_unrequested_match_and_keeps_score(specs):
    selected_specs = [s for s in specs if s.id in {"nightlife", "pets"}]
    facts = [fact(cid, value, hotel_id=hid, confidence=.9) for hid, count in [("reference", 50), ("candidate", 55)]
             for cid, value in [("nightlife", {"venues_500m": count}), ("pets", {"available": True})]]
    query = ParsedQuery(text="бары рядом", nice=[Criterion(characteristic_id="nightlife", label="Бары", field="venues_500m", op="gte", value=30)])
    [hit] = similar_hotels("reference", [hotel("reference"), hotel("candidate")], facts, selected_specs, parsed=query)
    assert hit.similarity_reasons[0].startswith('Отличается «В гуще событий»')
    assert "С питомцем" in hit.similarity_reasons[1]
    assert hit.score == 88.3
