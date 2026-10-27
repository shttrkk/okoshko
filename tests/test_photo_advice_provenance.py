"""Agreement adds provenance; numbers still need complete matching coverage."""
from datetime import timedelta

import pytest

from backend.agents.room_profiles import build_room_advice
from backend.contracts import Evidence, Fact, Hotel, Scope, now

GOALS = ("sea_view", "quiet_night", "morning_sun", "sunset_view")


def fixture(cid, *, low=1, high=5, side="W", numeric=True, available=True, identifier="numeric"):
    value = {"available": available}
    if numeric:
        value.update({"night_db_min": 33.7, "night_db_max": 49.7} if cid == "quiet_night" else
                     {"visible_minutes_min": 45, "visible_minutes_max": 140})
    return Fact(id=identifier, hotel_id="photo-advice", characteristic_id=cid,
                scope=Scope(type="floor_range", facade=side, floor_min=low, floor_max=high,
                            month=9 if cid in {"morning_sun", "sunset_view"} else None),
                value=value, status="computed", confidence=.38 if cid == "quiet_night" and numeric else .9,
                synthetic=not numeric, supporting_claim_ids=[identifier + "-claim"],
                valid_until=now() + timedelta(days=1),
                evidence=[Evidence(id=identifier + "-evidence", kind="calculation" if numeric else "simulation",
                                   label="Known-answer fixture", source_ids=["fixture"],
                                   artifact="/tests/photo-advice.json")])


def advice(facts):
    return build_room_advice(Hotel(id="photo-advice", name="Synthetic fixture", floors=5,
                                  lat=43.58, lon=39.72), facts, [])


@pytest.mark.parametrize("cid", GOALS)
@pytest.mark.parametrize("confidence", [.38, .9])
def test_agreeing_boolean_inside_numeric_domain_preserves_whole_range_and_both_refs(cid, confidence):
    numeric = fixture(cid)
    boolean = fixture(cid, low=5, numeric=False, identifier="boolean")
    boolean.confidence = confidence
    [item] = advice([numeric, boolean])
    assert [(o.scope.facade, o.scope.floor_min, o.scope.floor_max) for o in item.options] == [("W", 1, 5)]
    assert set(item.options[0].fact_ids) == {numeric.id, boolean.id}
    assert set(item.options[0].evidence_refs) == {numeric.evidence[0].id, boolean.evidence[0].id}
    assert item.synthetic and item.options[0].synthetic
    assert item.status == ("partial" if cid == "quiet_night" or confidence < .7 else "yes")
    if cid == "quiet_night":
        assert "≈ 34–50 дБ" in item.text
    elif cid in {"morning_sun", "sunset_view"}:
        assert "≈ 45–140 мин" in item.text
    assert item.model_dump() == advice([boolean, numeric])[0].model_dump()


@pytest.mark.parametrize("cid", GOALS)
@pytest.mark.parametrize("state", ["expired", "stale"])
def test_fresh_numeric_domain_supersedes_old_boolean_observations(cid, state):
    old = fixture(cid, low=3, high=3, numeric=False, available=False, identifier="old")
    if state == "expired":
        old.valid_until = now() - timedelta(seconds=1)
    else:
        old.status = "stale"
    [item] = advice([fixture(cid), old])
    assert [(o.scope.floor_min, o.scope.floor_max) for o in item.options] == [(1, 5)]
    assert old.id not in item.fact_ids


@pytest.mark.parametrize("cid", GOALS)
@pytest.mark.parametrize("confidence", [.38, .9])
def test_contrary_boolean_excludes_its_slice_even_with_lower_confidence(cid, confidence):
    negative = fixture(cid, low=3, high=3, numeric=False, available=False, identifier="negative")
    negative.confidence = confidence
    [item] = advice([fixture(cid), negative])
    assert [(o.scope.floor_min, o.scope.floor_max) for o in item.options] == [(1, 2), (4, 5)]
    assert negative.id not in item.fact_ids and not item.synthetic


@pytest.mark.parametrize("cid", GOALS)
def test_broad_boolean_contradiction_cannot_disappear_behind_specific_numeric_yes(cid):
    negative = fixture(cid, numeric=False, available=False, identifier="negative")
    negative.confidence = .38
    assert advice([negative, fixture(cid, low=3, high=3)]) == []


@pytest.mark.parametrize("cid", GOALS)
@pytest.mark.parametrize("state", ["unknown", "disputed", "no-evidence", "no-claims"])
def test_boolean_holes_are_not_filled_by_numeric_broad_domain(cid, state):
    hole = fixture(cid, low=3, high=3, numeric=False, identifier="hole")
    if state in {"unknown", "disputed"}:
        hole.status = state
    elif state == "no-evidence":
        hole.evidence = []
    else:
        hole.supporting_claim_ids = []
    [item] = advice([fixture(cid), hole])
    assert [(o.scope.floor_min, o.scope.floor_max) for o in item.options] == [(1, 2), (4, 5)]
    assert hole.id not in item.fact_ids


@pytest.mark.parametrize("cid", ["morning_sun", "sunset_view"])
def test_solar_numbers_do_not_extend_past_numeric_floors_or_to_another_side(cid):
    numeric = fixture(cid, high=4)
    boolean = fixture(cid, low=5, numeric=False, identifier="boolean")
    [item] = advice([numeric, boolean])
    assert [(o.scope.floor_min, o.scope.floor_max) for o in item.options] == [(1, 5)]
    assert "мин" not in item.text
    boolean.scope.facade = "E"
    [item] = advice([numeric, boolean])
    assert {o.scope.facade for o in item.options} == {"W", "E"}
    assert "мин" not in item.text


@pytest.mark.parametrize("cid", ["quiet_night", "morning_sun", "sunset_view"])
def test_numeric_coverage_cannot_jump_an_internal_floor_gap(cid):
    lower = fixture(cid, high=2)
    upper = fixture(cid, low=4, identifier="upper")
    boolean = fixture(cid, numeric=False, identifier="boolean")
    [item] = advice([lower, upper, boolean])
    if cid == "quiet_night":
        assert [(o.scope.floor_min, o.scope.floor_max) for o in item.options] == [(1, 2), (4, 5)]
        assert set(item.options[0].fact_ids) == {lower.id, boolean.id}
        assert set(item.options[1].fact_ids) == {upper.id, boolean.id}
    else:
        assert [(o.scope.floor_min, o.scope.floor_max) for o in item.options] == [(1, 5)]
        assert "мин" not in item.text


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), True, None])
@pytest.mark.parametrize("field", ["night_db_min", "night_db_max"])
def test_invalid_noise_numbers_cannot_use_boolean_corroboration_to_become_advice(invalid, field):
    numeric = fixture("quiet_night")
    numeric.value[field] = invalid
    assert advice([numeric, fixture("quiet_night", numeric=False, identifier="boolean")]) == []


@pytest.mark.parametrize("cid", ["morning_sun", "sunset_view"])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_solar_bounds_never_appear_in_guest_text(cid, invalid):
    numeric = fixture(cid)
    numeric.value["visible_minutes_max"] = invalid
    [item] = advice([numeric, fixture(cid, numeric=False, identifier="boolean")])
    assert "мин" not in item.text and "nan" not in item.text and "inf" not in item.text


def test_primorskaya_noise_known_answer_keeps_w5_next_to_sw():
    west = fixture("quiet_night")
    southwest = fixture("quiet_night", side="SW", identifier="southwest")
    southwest.value.update(night_db_min=33.9, night_db_max=49.9)
    w5 = fixture("quiet_night", low=5, numeric=False, identifier="demo-w5")
    [quiet] = advice([west, southwest, w5])
    assert [(o.scope.facade, o.scope.floor_min, o.scope.floor_max) for o in quiet.options] == [
        ("SW", 1, 5), ("W", 1, 5)]
    assert "Юго-запад и запад 1–5-й этажи; ночью ≈ 34–50 дБ по расчёту." == quiet.text
    assert quiet.status == "partial" and quiet.synthetic
    assert set(quiet.options[1].fact_ids) == {west.id, w5.id}
    assert set(quiet.options[1].evidence_refs) == {west.evidence[0].id, w5.evidence[0].id}
