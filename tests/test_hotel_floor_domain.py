"""Room eligibility uses hotel identity/domain, independently of model confidence."""
from datetime import timedelta

import pytest

from backend.agents.explainer import recommend_room
from backend.agents.query_match import FLOOR_DOMAIN_UNKNOWN, assess_hotel
from backend.agents.ranker import rank_hotels
from backend.api.catalog import detail
from backend.contracts import Criterion, Evidence, Fact, Hotel, ParsedQuery, Scope, now
from backend.tests.test_api_integration import ApiRepo
from backend.tests.test_search import catalog  # noqa: F401 (fixture)


def hotel(floors=None, id="domain"):
    return Hotel(id=id, name="Synthetic hotel domain", lat=43.58, lon=39.72, floors=floors)


def model(cid="sea_view", *, low=1, high=8, confidence=.9, available=True, hotel_id="domain"):
    return Fact(id="domain-"+cid, hotel_id=hotel_id, characteristic_id=cid,
                scope=Scope(type="floor_range", facade="W", floor_min=low, floor_max=high),
                value={"available": available, "facade": "W"}, status="computed", confidence=confidence,
                supporting_claim_ids=["synthetic-domain-input"], valid_until=now()+timedelta(days=30),
                evidence=[Evidence(kind="calculation", label="Synthetic geometric domain fixture", source_ids=["test_fixture"],
                                   artifact="/data/eval/README.md", inputs={"building_id": "conditional-contour"})])


def query(cid="sea_view", **kwargs):
    return ParsedQuery(text="", must=[Criterion(characteristic_id=cid, label=cid, field="available")], **kwargs)


@pytest.mark.parametrize("cid", ["sea_view", "sunset_view", "mountain_view", "custom_view_alias", "morning_sun"])
@pytest.mark.parametrize("low,confidence", [(1, .63), (3, .9)])
def test_unknown_domain_never_supports_scoped_geometry_even_at_conditional_first_level(cid, low, confidence, catalog):  # noqa: F811
    fact = model(cid, low=low, confidence=confidence)
    original = fact.model_dump(mode="json")
    [hit] = rank_hotels([hotel()], [fact], query(cid), catalog)
    [verdict] = hit.verdicts
    assert hit.group == "unknown" and hit.must_satisfied == 0
    assert verdict.status == "unknown" and verdict.confidence == confidence
    assert verdict.fact_ids == [fact.id] and verdict.evidence_refs == [fact.evidence[0].id]
    assert FLOOR_DOMAIN_UNKNOWN in verdict.caveat
    assert FLOOR_DOMAIN_UNKNOWN in hit.room_recommendation and "Просите номер" not in hit.room_recommendation
    assert fact.model_dump(mode="json") == original


def test_known_floor_domain_caps_context_and_recommendation(catalog):  # noqa: F811
    fact = model(low=3, high=9)
    parsed = query(min_floor=3)
    actual = hotel(5)
    assessment = assess_hotel([fact], parsed, catalog, hotel=actual)
    assert assessment.context.floor <= 5 and assessment.matches[0].supported
    [hit] = rank_hotels([actual], [fact], parsed, catalog)
    assert hit.group == "all" and "3–5-й этажи" in hit.room_recommendation
    assert fact.scope.floor_max == 9
    outside = assess_hotel([fact], query(min_floor=7), catalog, hotel=actual)
    assert outside.context.floor <= 5 and not outside.matches[0].supported
    [hit] = rank_hotels([actual], [fact], query(min_floor=7), catalog)
    assert hit.group == "unknown" and "за пределы известной этажности" in hit.verdicts[0].caveat
    assert "Просите номер" not in hit.room_recommendation


@pytest.mark.parametrize("scoped", [False, True], ids=["global-floor", "criterion-floor"])
def test_known_hotel_wide_parking_cannot_establish_an_unknown_requested_floor(scoped, catalog):  # noqa: F811
    parking = model("car_access").model_copy(update={"scope": Scope(), "value": {"available": True, "parking_distance_m": 50}})
    criterion = Criterion(characteristic_id="car_access", label="Парковка", field="parking_distance_m", op="lte", value=500,
                          scope=Scope(floor_min=7) if scoped else None)
    parsed = ParsedQuery(text="", must=[criterion], min_floor=None if scoped else 7)
    [hit] = rank_hotels([hotel()], [parking], parsed, catalog)
    assert hit.group == "unknown" and FLOOR_DOMAIN_UNKNOWN in hit.verdicts[0].caveat
    assert FLOOR_DOMAIN_UNKNOWN in hit.room_recommendation
    parsed.min_floor, criterion.scope = None, None
    assert rank_hotels([hotel()], [parking], parsed, catalog)[0].group == "all"


def test_facade_only_outdoor_noise_does_not_require_a_floor_count(catalog):  # noqa: F811
    noise = model("quiet_night").model_copy(update={"scope": Scope(type="facade", facade="W")})
    [hit] = rank_hotels([hotel()], [noise], query("quiet_night"), catalog)
    assert hit.group == "all" and hit.verdicts[0].confidence == .9


def test_alias_with_implicit_floor_range_cannot_bypass_the_domain_guard(catalog):  # noqa: F811
    fact = model("custom_view_alias").model_copy(update={"scope": Scope(type="floor_range", facade="W")})
    [hit] = rank_hotels([hotel()], [fact], query("custom_view_alias"), catalog)
    assert hit.group == "unknown" and FLOOR_DOMAIN_UNKNOWN in hit.verdicts[0].caveat


@pytest.mark.parametrize("synthetic", [False, True])
def test_only_real_scoped_hotel_confirmation_is_independent_floor_evidence(synthetic, catalog):  # noqa: F811
    answer = model(low=7, high=8).model_copy(update={
        "status": "confirmed", "synthetic": synthetic,
        "evidence": [Evidence(kind="hotelier", label="Synthetic test of a partner confirmation", source_ids=["test_partner"], message_id="partner-answer")],
    })
    [hit] = rank_hotels([hotel()], [answer], query(min_floor=7), catalog)
    assert (hit.group == "all") is (not synthetic)
    assert hit.verdicts[0].confidence == .9
    assert (hit.verdicts[0].status == "yes") is (not synthetic)


def test_scoped_website_quote_is_not_a_confirmed_hotel_floor_domain(catalog):  # noqa: F811
    website = model(low=7).model_copy(update={
        "evidence": [Evidence(kind="quote", label="Synthetic website statement", source_ids=["hotel_website"], quote="Sea view from floor seven")],
    })
    [hit] = rank_hotels([hotel()], [website], query(), catalog)
    assert hit.group == "unknown" and FLOOR_DOMAIN_UNKNOWN in hit.verdicts[0].caveat


def test_low_level_calls_without_hotel_metadata_remain_explicitly_conditional(catalog):  # noqa: F811
    fact = model(low=3)
    assert assess_hotel([fact], query(), catalog).matches[0].supported
    assert not assess_hotel([fact], query(), catalog, hotel=hotel()).matches[0].supported
    assert "Просите номер" in recommend_room([fact], query())
    assert FLOOR_DOMAIN_UNKNOWN in recommend_room([fact], query(), hotel=hotel())


def test_detail_propagates_hotel_domain_to_verdicts_and_room_recommendation():
    repo = ApiRepo()
    fact = model(low=3, hotel_id="test-hotel")
    repo.upsert("facts", fact.model_dump(mode="json"))
    result = detail("test-hotel", repo, parsed=query(), compact=True)
    assert FLOOR_DOMAIN_UNKNOWN in result.verdicts[0].caveat
    assert FLOOR_DOMAIN_UNKNOWN in result.room_recommendation
    actual = next(row for row in result.facts if row.id == fact.id)
    assert actual.value == fact.value and actual.confidence == fact.confidence and actual.scope == fact.scope
