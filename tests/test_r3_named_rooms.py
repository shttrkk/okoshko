"""Confirmed hotel room labels stay separate from modelled facade/floor advice."""
from datetime import timedelta

import pytest

from backend.agents.room_profiles import build_room_advice
from backend.contracts import Evidence, Fact, Hotel, Scope, now
from tests.test_room_advice import fact as geographic_fact
from tests.test_room_advice import hotel as geographic_hotel


def hotel(floors=None):
    return Hotel(id="named-hotel", name="Synthetic named room test hotel", lat=43.58, lon=39.72, floors=floors)


def named_fact(*, groups=None, status="confirmed", synthetic=False, kind="hotelier", identifier="named-fact"):
    return Fact(id=identifier, hotel_id="named-hotel", characteristic_id="room_orientation", scope=Scope(),
                value={"available": None, "groups": groups if groups is not None else [
                    {"rooms": [str(room) for room in range(201, 211)] + [str(room) for room in range(301, 311)],
                     "sea_view": True, "balcony": True},
                    {"rooms": [str(room) for room in range(211, 221)], "courtyard": True, "balcony": False}],
                    "absent_floors": [1]},
                status=status, confidence=.9, synthetic=synthetic, valid_until=now() + timedelta(days=5),
                supporting_claim_ids=[identifier + "-claim"], evidence=[Evidence(id=identifier + "-evidence",
                    kind=kind, label="Synthetic fixture; not a real hotel statement", source_ids=["test-hotelier"],
                    quote="201–210, 301–310 на море, с балконом. 211–220 во двор, без балкона. На первом этаже номеров нет.")])


def advice(facts, actual=None, month=9):
    return build_room_advice(actual or hotel(), facts, [], month)


def test_named_groups_remain_visible_with_unknown_height_and_never_invent_a_floor():
    source = named_fact()
    before = source.model_dump()
    [item] = advice([source])
    assert item.characteristic_id == "room_orientation"
    assert "Номера 201–210, 301–310: на море, с балконом." in item.text
    assert "Номера 211–220: без балкона, окна во двор." in item.text
    assert item.status == "yes" and not item.synthetic
    assert "подтверждённому" in item.title
    assert item.fact_ids == item.options[0].fact_ids == [source.id]
    assert item.evidence_refs == item.options[0].evidence_refs == [source.evidence[0].id]
    assert item.options[0].scope.model_dump(exclude_none=True) == {}
    assert "этаж" not in item.text and source.model_dump() == before


@pytest.mark.parametrize("state", ["computed", "unknown", "stale", "disputed", "expired", "no-claims", "no-evidence", "no-quote", "calculation", "foreign-hotel", "conflict"])
def test_unconfirmed_unusable_or_unproven_groups_never_become_room_advice(state):
    source = named_fact()
    if state in {"computed", "unknown", "stale", "disputed"}:
        source.status = state
    elif state == "expired":
        source.valid_until = now() - timedelta(seconds=1)
    elif state == "no-claims":
        source.supporting_claim_ids = []
    elif state == "no-evidence":
        source.evidence = []
    elif state == "no-quote":
        source.evidence[0].quote = None
    elif state == "calculation":
        source.evidence[0].kind = "calculation"
    elif state == "foreign-hotel":
        source.hotel_id = "another-hotel"
    elif state == "conflict":
        source.conflicting_claim_ids = ["unresolved-contrary-claim"]
    assert advice([source]) == []


@pytest.mark.parametrize("kind,synthetic", [("hotelier", True), ("simulation", False), ("mock", False)])
def test_demo_group_is_marked_demo_and_never_called_real_confirmation(kind, synthetic):
    [item] = advice([named_fact(kind=kind, synthetic=synthetic)])
    assert item.synthetic and item.status == item.options[0].status == "partial"
    assert item.options[0].synthetic and "подтверждённому" not in item.title
    assert "Ответ отеля · демо" in " ".join(item.caveats)


def test_simulator_source_is_demo_even_if_the_aggregate_flag_was_missing():
    source = named_fact()
    source.evidence[0].source_ids = ["hotelier_simulator"]
    [item] = advice([source])
    assert item.synthetic and item.status == "partial"


def test_false_is_stated_as_false_but_null_and_missing_traits_are_not_invented():
    source = named_fact(groups=[{"rooms": ["401", "402"], "sea_view": False, "balcony": None},
                                {"rooms": ["501"], "courtyard": True},
                                {"rooms": ["601"], "sea_view": None},
                                {"rooms": ["701"], "balcony": "yes"}])
    [item] = advice([source])
    assert "Номера 401–402: без вида на море." in item.text
    assert "Номера 501: окна во двор." in item.text
    assert "балкон" not in item.text and "601" not in item.text and "701" not in item.text


def test_every_confirmed_group_survives_the_geographic_advice_cap_and_keeps_exact_refs():
    actual = geographic_hotel()
    named = named_fact()
    named.hotel_id = actual.id
    geo = [geographic_fact(), geographic_fact("quiet_night", value={"available": True, "night_db_min": 34, "night_db_max": 50}),
           geographic_fact("morning_sun", month=9), geographic_fact("sunset_view", month=9)]
    result = advice([*geo, named], actual)
    assert len(result) == 5 and result[0].characteristic_id == "room_orientation"
    assert [item.characteristic_id for item in result[1:]] == ["sea_view", "quiet_night", "morning_sun", "sunset_view"]
    assert result[0].fact_ids == [named.id]
    assert all(named.id not in item.fact_ids for item in result[1:])
    assert [item.model_dump() for item in result] == [item.model_dump() for item in advice(list(reversed([*geo, named])), actual)]


def test_more_than_four_named_groups_are_kept_in_full_without_crossing_room_id_gaps():
    groups = [{"rooms": [str(100 * index + 1), str(100 * index + 3)], "balcony": True} for index in range(1, 8)]
    [item] = advice([named_fact(groups=groups)])
    assert len(item.text.split("Номера ")) == 8
    assert "101, 103" in item.text and "701, 703" in item.text and "101–103" not in item.text


def test_contradictory_named_room_is_omitted_without_poisoning_other_room_labels():
    source = named_fact(groups=[{"rooms": ["201", "202", "203"], "sea_view": True},
                                {"rooms": ["202"], "sea_view": False}])
    [item] = advice([source])
    assert "Номера 201, 203: на море." in item.text and "202" not in item.text
    assert any("расходятся" in text for text in item.caveats)


def test_month_qualifier_is_respected_and_not_inferred_from_room_number():
    source = named_fact()
    source.scope = Scope(month=9)
    assert advice([source], month=8) == []
    [item] = advice([source])
    assert "В сентябре:" in item.text
    assert item.options[0].scope.model_dump(exclude_none=True) == {}
