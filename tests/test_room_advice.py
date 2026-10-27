"""Advice proves each clickable side/floor slice before grouping guest wording."""
from datetime import timedelta

import pytest

from backend.agents.room_profiles import build_room_advice, build_room_profiles
from backend.contracts import Evidence, Fact, Hotel, Scope, now


def hotel(floors=8):
    return Hotel(id="advice-fixture", name="Synthetic advice fixture", lat=43.58, lon=39.72, floors=floors)


def fact(cid="sea_view", *, side="W", low=1, high=8, value=None, month=None,
         confidence=.9, status="computed", synthetic=False, identifier=None):
    identifier = identifier or f"{cid}-{side}-{low}-{high}-{month}"
    return Fact(id=identifier, hotel_id="advice-fixture", characteristic_id=cid,
                scope=Scope(type="floor_range", facade=side, floor_min=low, floor_max=high, month=month),
                value={"available": True} if value is None else value, confidence=confidence,
                status=status, synthetic=synthetic, supporting_claim_ids=[identifier + "-claim"],
                valid_until=now() + timedelta(days=1),
                evidence=[Evidence(id=identifier + "-evidence", kind="calculation", label="Known-answer fixture",
                                   source_ids=["test-fixture"], artifact="/tests/advice-fixture.json")])


def advice(facts, *, actual=None, month=9):
    return build_room_advice(actual or hotel(), facts, [], month)


def bounds(item):
    return [(o.scope.facade, o.scope.floor_min, o.scope.floor_max) for o in item.options]


def test_upper_floor_sea_strengths_survive_twelve_position_limit_and_group_sides():
    sides = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    useful = {"SE", "S", "SW", "W"}
    facts = [fact(side=side, low=1, high=4, value={"available": False}) for side in sides]
    facts += [fact(side=side, low=5, high=8, status="computed" if side in useful else "unknown")
              for side in sides]
    profiles = build_room_profiles(hotel(), facts, [])
    assert len(profiles) == 12
    assert {p.scope.facade for p in profiles[:4]} == useful
    assert all(p.scope.floor_min == 5 for p in profiles[:4])
    [sea] = advice(facts)
    assert bounds(sea) == [(s, 5, 8) for s in sides if s in useful]
    assert sea.text == "Вид на море — юго-восток, юг, юго-запад и запад с 5-го этажа."
    assert sea.status == "yes" and all(o.status == "yes" for o in sea.options)


def test_advice_uses_all_intervals_even_when_profiles_cap_to_one():
    facts = [fact(side=side, low=5, high=8) for side in ("SE", "S", "SW", "W")]
    assert len(build_room_profiles(hotel(), facts, [], limit=1)) == 1
    [sea] = advice(facts)
    assert len(sea.options) == 4


def test_adjacent_positive_floors_glue_with_complete_refs_but_interior_negative_stays_out():
    broad = fact()
    negative = fact(low=4, high=4, value={"available": False}, identifier="interior-negative")
    [sea] = advice([broad, negative])
    assert bounds(sea) == [("W", 1, 3), ("W", 5, 8)]
    assert negative.id not in sea.fact_ids
    floors = [fact(low=floor, high=floor) for floor in range(1, 9)]
    [merged] = advice(floors)
    assert bounds(merged) == [("W", 1, 8)]
    assert set(merged.fact_ids) == {f.id for f in floors}
    assert set(merged.options[0].evidence_refs) == {f.evidence[0].id for f in floors}


@pytest.mark.parametrize("state", ["disputed", "unknown", "stale", "expired", "no-evidence", "no-claims"])
def test_unusable_inner_interval_does_not_inherit_broad_positive(state):
    inner = fact(low=4, high=4, identifier="inner", status=state if state in {"disputed", "unknown", "stale"} else "computed")
    if state == "expired":
        inner.valid_until = now() - timedelta(seconds=1)
    elif state == "no-evidence":
        inner.evidence = []
    elif state == "no-claims":
        inner.supporting_claim_ids = []
    # Expired/stale observations are superseded by fresh wider evidence under
    # the existing resolver policy; current unknown/disputed holes are retained.
    [sea] = advice([fact(), inner])
    expected = [("W", 1, 8)] if state in {"expired", "stale"} else [("W", 1, 3), ("W", 5, 8)]
    assert bounds(sea) == expected
    assert inner.id not in sea.fact_ids
    assert advice([inner]) == []


def test_equal_domain_conflict_and_missing_floor_coverage_never_form_positive_advice():
    positive = fact()
    negative = fact(value={"available": False}, identifier="contradiction")
    assert advice([positive, negative]) == []
    [single] = advice([fact(low=5, high=5)])
    assert bounds(single) == [("W", 5, 5)]
    assert "с 5-го" not in single.text
    assert "5-й этаж" in single.text


def test_month_is_proven_and_internal_month_or_facade_mismatch_stays_unknown():
    sun = fact("morning_sun", month=9, value={"available": True, "visible_minutes_min": 45,
               "visible_minutes_max": 140}, confidence=.54)
    assert advice([sun], month=8) == []
    [morning] = advice([sun])
    assert morning.status == morning.options[0].status == "partial"
    assert "в сентябре" in morning.text and "≈ 45–140 мин" in morning.text
    assert "Пока неточно — уточните у отеля" in morning.text
    sun.value["month"] = 7
    assert advice([sun]) == []
    sun.value["month"] = 9
    sun.value["facade"] = "E"
    assert advice([sun]) == []


def test_each_atomic_solar_interval_is_checked_before_equivalent_presentations_glue():
    lower = fact("morning_sun", low=1, high=4, month=9, value={"available": True, "month": 9})
    upper = fact("morning_sun", low=5, high=8, month=9, value={"available": True, "month": 7})
    [morning] = advice([lower, upper])
    assert bounds(morning) == [("W", 1, 4)] and upper.id not in morning.fact_ids


def test_solar_mean_without_floor_bounds_is_not_a_numeric_promise():
    sun = fact("sunset_view", month=9, value={"available": True, "visible_minutes": 47.1})
    [sunset] = advice([sun])
    assert "47" not in sunset.text
    assert sunset.options[0].fact_ids == [sun.id]
    assert "Минуты означают сумму" in " ".join(sunset.caveats)


def test_numeric_noise_uses_outer_range_and_partial_without_promoting_boolean_quiet():
    facts = [fact("quiet_night", side=side, value={"available": available, "night_db_min": low,
                  "night_db_max": high}, confidence=.38)
             for side, available, low, high in (("W", True, 33.7, 49.7), ("SW", True, 33.9, 49.9),
                                               ("S", False, 34.8, 50.8), ("E", False, 40.2, 56.2))]
    before = [f.model_dump() for f in facts]
    [quiet] = advice(facts)
    assert quiet.title == "Меньше уличного шума"
    assert bounds(quiet) == [("SW", 1, 8), ("W", 1, 8)]
    assert quiet.status == "partial" and "≈ 34–50 дБ" in quiet.text
    assert "Пока неточно" in quiet.text and "Тише всего" not in quiet.text
    assert any("только стороны и этажи" in note for note in quiet.caveats)
    assert [f.model_dump() for f in facts] == before


def test_boolean_noise_answer_does_not_borrow_numeric_values_or_cover_a_missing_slice():
    boolean = fact("quiet_night", low=5, high=8, synthetic=True)
    assert advice([boolean]) == []
    numeric = fact("quiet_night", value={"available": True, "night_db_min": 34, "night_db_max": 50}, confidence=.38)
    [quiet] = advice([numeric, boolean])
    assert bounds(quiet) == [("W", 1, 4)]
    assert quiet.fact_ids == [numeric.id] and not quiet.synthetic


def test_synthetic_aggregation_is_preserved_and_partial_never_becomes_yes():
    real = fact(low=1, high=4)
    synthetic = fact(low=5, high=8, synthetic=True, confidence=.65)
    [sea] = advice([synthetic, real])
    assert sea.synthetic and sea.options[0].synthetic
    assert sea.status == sea.options[0].status == "partial"
    assert set(sea.fact_ids) == {real.id, synthetic.id}
    assert "Пока неточно" in sea.text
    assert advice([fact(confidence=.29)]) == []


def test_same_scope_agreement_keeps_all_refs_and_synthetic_flag_while_contradictions_block():
    real = fact()
    demo = fact(identifier="demo-corroboration", synthetic=True)
    [sea] = advice([demo, real])
    assert sea.synthetic and sea.options[0].synthetic
    assert set(sea.fact_ids) == {real.id, demo.id}
    demo.value = {"available": False}
    demo.confidence = .38
    assert advice([real, demo]) == []
    first = fact("quiet_night", value={"available": True, "night_db_min": 34, "night_db_max": 50})
    conflict = fact("quiet_night", identifier="conflicting-noise",
                    value={"available": True, "night_db_min": 60, "night_db_max": 76})
    assert advice([first, conflict]) == []


def test_unknown_height_or_hotel_wide_view_cannot_invent_sides_and_floors():
    assert advice([fact()], actual=hotel(None)) == []
    general = fact()
    general.scope = Scope()
    assert advice([general]) == []
    beyond = fact(low=9, high=12)
    assert advice([beyond]) == []


def test_four_goals_are_stable_referenced_and_guest_language_has_no_technical_terms():
    facts = [fact(), fact("quiet_night", value={"available": True, "night_db_min": 34, "night_db_max": 50}),
             fact("morning_sun", month=9), fact("sunset_view", month=9)]
    actual = advice(facts)
    assert len(actual) == 4
    assert [x.model_dump() for x in actual] == [x.model_dump() for x in advice(list(reversed(facts)))]
    for item in actual:
        assert set(item.fact_ids) == {fid for option in item.options for fid in option.fact_ids}
        assert set(item.evidence_refs) == {ref for option in item.options for ref in option.evidence_refs}
        assert all(o.scope.month == 9 and o.scope.facade == "W" for o in item.options)
        assert not any(term in (item.title + item.text).lower() for term in
                       ("модел", "прокси", "синтет", "нормал", "pmtiles"))
    with pytest.raises(ValueError):
        advice(facts, month=13)
