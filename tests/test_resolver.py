from datetime import UTC, datetime, timedelta

import pytest

from backend.agents.resolver import (
    resolve_all,
    resolve_claims,
    resolve_hotel,
    scope_contains,
    scope_key,
    stable_id,
)
from backend.contracts import CharacteristicSpec, Claim, Evidence, Scope, ToolCall

OBSERVED = datetime(2026, 10, 26, tzinfo=UTC)


def claim(id, value, source="geo", confidence=.7, **kwargs):
    return Claim(id=id, hotel_id="h", characteristic_id="sea_view", value={"available": value},
                 source=source, method="test@1", confidence=confidence, observed_at=OBSERVED,
                 valid_until=OBSERVED+timedelta(days=180),
                 evidence=[Evidence(kind="simulation",label="test",source_ids=[source],artifact="/data/fixtures/development.json")], **kwargs)


def test_single_claim_keeps_confidence_not_one():
    facts, conflicts = resolve_claims("h", [claim("1",True)], [], [], at=OBSERVED)
    assert facts[0].confidence == .7
    assert not conflicts


def test_unknown_is_not_a_vote_for_no_and_expired_is_stale():
    facts, _ = resolve_claims("h", [claim("1",None)], [], [], at=OBSERVED)
    assert facts[0].status == "unknown"
    facts, _ = resolve_claims("h", [claim("2",True)], [], [], at=OBSERVED+timedelta(days=181))
    assert facts[0].status == "stale" and facts[0].confidence == 0


def test_hotelier_cannot_override_external_geometry_by_title():
    facts, conflicts = resolve_claims("h", [claim("1",True,source="hotelier:1",confidence=.9),claim("2",False,confidence=.85)], [], [], at=OBSERVED)
    assert conflicts[0].status == "open"
    assert facts[0].status == "disputed"


def test_scope_split_preserves_specific_facade():
    facts, conflicts = resolve_claims("h", [claim("1",True),claim("2",False,scope=Scope(type="facade",facade="E"))], [], [], at=OBSERVED)
    assert len(facts)==2 and conflicts[0].kind == "scope"
    assert all(f.resolution=="scope_split" for f in facts)


def test_duplicate_source_does_not_manufacture_votes():
    original = claim("1",True)
    duplicate = original.model_copy(update={"id":"2"})
    facts, _ = resolve_claims("h", [original, duplicate], [], [], at=OBSERVED)
    assert facts[0].confidence == .7
    assert len(facts[0].supporting_claim_ids) == 1


def test_new_spec_version_cannot_reuse_values_with_old_semantics():
    spec=CharacteristicSpec(id="sea_view",version=2,name_ru="Вид",description="Новая семантика",value_schema={"type":"object"},methods=[ToolCall(tool="viewshed_facade")])
    facts,_=resolve_claims("h",[claim("v1",True)],[spec],[],at=OBSERVED)
    assert len(facts)==1 and facts[0].status=="unknown"



def test_known_parking_distance_survives_unknown_vehicle_entry():
    from backend.agents.query_match import evaluate_fact
    from backend.contracts import Criterion, ParsedQuery
    observed = claim("parking", None, confidence=.65).model_copy(update={"characteristic_id": "car_access", "value": {"available": None, "parking_distance_m": 350}, "synthetic": True})
    [fact], _ = resolve_claims("h", [observed], [], [], at=OBSERVED)
    assert fact.status == "computed" and fact.value["available"] is None
    parking = Criterion(characteristic_id="car_access", label="Парковка рядом", field="parking_distance_m", op="lte", value=500)
    entry = Criterion(characteristic_id="car_access", label="Въезд", field="available")
    assert evaluate_fact(fact, parking, ParsedQuery(text=""), "parking_distance_m").supported
    assert evaluate_fact(fact, entry, ParsedQuery(text=""), "available").status == "unknown"


def scoped_claim(id, value, *, source=None, **scope):
    return claim(id, value, source=source or id, confidence=.85, scope=Scope(**scope))


def assert_provenance_is_narrowed_only(facts, claims):
    originals = {c.id: c for c in claims}
    for fact in facts:
        for id in [*fact.supporting_claim_ids, *fact.conflicting_claim_ids]:
            assert scope_contains(originals[id].scope, fact.scope), (id, fact.scope, originals[id].scope)
        assert {e.id for e in fact.evidence} == {e.id for id in fact.supporting_claim_ids for e in originals[id].evidence}


def test_overlap_partitions_floors_and_keeps_immutable_claims():
    claims = [scoped_claim("a", True, facade="W", floor_min=1, floor_max=5),
              scoped_claim("b", False, facade="W", floor_min=5)]
    before = [c.model_dump() for c in claims]
    facts, conflicts = resolve_claims("h", claims, [], [], at=OBSERVED)
    assert {(f.scope.floor_min, f.scope.floor_max, f.status) for f in facts} == {
        (1, 4, "computed"), (5, 5, "disputed"), (6, None, "computed")}
    dispute = next(f for f in facts if f.status == "disputed")
    assert set(dispute.supporting_claim_ids + dispute.conflicting_claim_ids) == {"a", "b"}
    assert len(conflicts) == 1 and conflicts[0].status == "open" and set(conflicts[0].claim_ids) == {"a", "b"}
    assert [c.model_dump() for c in claims] == before
    assert_provenance_is_narrowed_only(facts, claims)


@pytest.mark.parametrize("left,right", [
    ({"facade": "W", "floor_min": 1, "floor_max": 4}, {"facade": "W", "floor_min": 5}),
    ({"facade": "W", "floor_min": 1, "floor_max": 5}, {"facade": "E", "floor_min": 5}),
    ({"facade": "W", "month": 8}, {"facade": "W", "month": 9}),
    ({"facade": "W", "room_type": "suite"}, {"facade": "W", "room_type": "standard"}),
])
def test_disjoint_scopes_keep_ids_and_never_conflict(left, right):
    claims = [scoped_claim("a", True, **left), scoped_claim("b", False, **right)]
    facts, conflicts = resolve_claims("h", claims, [], [], at=OBSERVED)
    assert not conflicts and len(facts) == 2
    assert {f.id for f in facts} == {stable_id("fact", "h", "sea_view", scope_key(c.scope)) for c in claims}
    assert {scope_key(f.scope) for f in facts} == {scope_key(c.scope) for c in claims}


@pytest.mark.parametrize("category,specific", [("month", 9), ("room_type", "suite")])
def test_wildcard_keeps_default_and_resolves_only_specific_overlap(category, specific):
    claims = [scoped_claim("a", True, facade="W", floor_min=5),
              scoped_claim("b", False, facade="W", floor_min=1, floor_max=5, **{category: specific})]
    facts, conflicts = resolve_claims("h", claims, [], [], at=OBSERVED)
    disputes = [f for f in facts if f.status == "disputed"]
    assert len(disputes) == 1 and disputes[0].scope.floor_min == disputes[0].scope.floor_max == 5
    assert getattr(disputes[0].scope, category) == specific
    assert any(getattr(f.scope, category) is None and f.value["available"] is True for f in facts)
    assert len(facts) == 3 and len(conflicts) == 1  # No twelve-month expansion.
    assert_provenance_is_narrowed_only(facts, claims)


def test_unbounded_floor_edges_and_nested_intersections():
    claims = [scoped_claim("a", True, facade="W"),
              scoped_claim("b", False, facade="W", floor_min=5, floor_max=7)]
    facts, _ = resolve_claims("h", claims, [], [], at=OBSERVED)
    assert {(f.scope.floor_min, f.scope.floor_max, f.status) for f in facts} == {
        (None, 4, "computed"), (5, 7, "disputed"), (8, None, "computed")}
    assert_provenance_is_narrowed_only(facts, claims)


def test_same_source_overlap_does_not_manufacture_independent_votes():
    claims = [scoped_claim("a", True, source="same", facade="W", floor_min=1, floor_max=5),
              scoped_claim("b", False, source="same", facade="W", floor_min=5)]
    facts, conflicts = resolve_claims("h", claims, [], [], at=OBSERVED)
    assert next(f for f in facts if f.scope.floor_min == f.scope.floor_max == 5).status == "disputed"
    assert conflicts[0].status == "open"


def test_equal_values_in_overlapping_ranges_are_not_a_conflict():
    claims = [scoped_claim("a", True, facade="W", floor_min=1, floor_max=5),
              scoped_claim("b", True, facade="W", floor_min=5)]
    facts, conflicts = resolve_claims("h", claims, [], [], at=OBSERVED)
    assert not conflicts and len(facts) == 3
    assert all(f.status == "computed" and f.value["available"] is True and f.confidence == pytest.approx(.85) for f in facts)
    assert_provenance_is_narrowed_only(facts, claims)


def test_overlapping_votes_keep_source_priors_and_auto_resolution():
    claims = [scoped_claim("a", True, facade="W", floor_min=1, floor_max=5),
              scoped_claim("b", False, facade="W", floor_min=5)]
    sources = [{"id": "a", "alpha": 9, "beta": 1}, {"id": "b", "alpha": 1, "beta": 9}]
    facts, conflicts = resolve_claims("h", claims, [], sources, at=OBSERVED)
    overlap = next(f for f in facts if f.scope.floor_min == f.scope.floor_max == 5)
    assert overlap.status == "computed" and overlap.value["available"] is True
    assert overlap.confidence == pytest.approx(.765)
    assert conflicts[0].status == "auto_resolved"


def test_month_and_room_type_intersections_form_one_real_three_claim_region():
    claims = [scoped_claim("a", True, source="shared", facade="W", floor_min=1, floor_max=10),
              scoped_claim("b", False, facade="W", floor_min=5, floor_max=8, month=9),
              scoped_claim("c", True, source="shared", facade="W", floor_min=6, floor_max=7, room_type="suite")]
    facts, conflicts = resolve_claims("h", claims, [], [], at=OBSERVED)
    [overlap] = [f for f in facts if f.scope.month == 9 and f.scope.room_type == "suite"]
    assert overlap.scope.floor_min == 6 and overlap.scope.floor_max == 7 and overlap.status == "disputed"
    assert set(overlap.supporting_claim_ids + overlap.conflicting_claim_ids) == {"a", "b", "c"}
    assert any(set(c.claim_ids) == {"a", "b", "c"} and c.status == "open" for c in conflicts)
    assert_provenance_is_narrowed_only(facts, claims)


def test_general_view_is_qualification_but_explicit_universal_can_conflict():
    broad = scoped_claim("broad", True)
    narrow = scoped_claim("narrow", False, type="floor_range", facade="W", floor_min=5, floor_max=5)
    facts, conflicts = resolve_claims("h", [broad, narrow], [], [], at=OBSERVED)
    assert len(facts) == 2 and all(f.status == "computed" for f in facts)
    assert conflicts[0].kind == "scope" and conflicts[0].status == "auto_resolved"
    broad.evidence[0].inputs["universal_scope"] = True
    facts, conflicts = resolve_claims("h", [broad, narrow], [], [], at=OBSERVED)
    assert any(f.status == "disputed" and f.scope.facade == "W" and f.scope.floor_min == 5 for f in facts)
    assert any(c.kind == "value" and c.status == "open" for c in conflicts)
    assert {f.scope.facade for f in facts} <= {None, "W"}  # No invented north-facing windows.
    assert_provenance_is_narrowed_only(facts, [broad, narrow])


def test_superseded_or_expired_ranges_do_not_enter_new_partitions():
    old = scoped_claim("old", True, facade="W", floor_min=1, floor_max=5)
    replacement = scoped_claim("replacement", False, facade="W", floor_min=5).model_copy(update={"supersedes_ids": [old.id]})
    facts, conflicts = resolve_claims("h", [old, replacement], [], [], at=OBSERVED)
    assert len(facts) == 1 and not conflicts and facts[0].supporting_claim_ids == [replacement.id]
    expired = old.model_copy(update={"valid_until": OBSERVED - timedelta(seconds=1)})
    facts, conflicts = resolve_claims("h", [expired, replacement.model_copy(update={"supersedes_ids": []})], [], [], at=OBSERVED)
    assert len(facts) == 2 and not conflicts and {f.status for f in facts} == {"computed", "stale"}


@pytest.mark.parametrize("bulk", [False, True])
@pytest.mark.parametrize("historical_status", ["open", "asked"])
def test_supersession_closes_old_conflict_history_but_retains_active_disputes(bulk, historical_status):
    from backend.tests.test_api_integration import ApiRepo
    repo = ApiRepo()
    repo.tables["hotels"] = {"h": {"id": "h", "name": "Synthetic overlap", "lat": 43.58, "lon": 39.72}}
    first = scoped_claim("a", True, facade="W", floor_min=5, floor_max=5)
    second = scoped_claim("b", False, facade="W", floor_min=5, floor_max=5)
    for observation in (first, second):
        repo.add_claim(observation)
    run = (lambda: resolve_all(repo)) if bulk else (lambda: resolve_hotel("h", repo))
    run()
    [historical] = repo.all("conflicts")
    assert historical["status"] == "open"
    run()
    assert repo.get("conflicts", historical["id"])["status"] == "open"
    repo.upsert("conflicts", {**historical, "status": historical_status, "question_id": "existing-question"})
    run()
    assert repo.get("conflicts", historical["id"])["status"] == historical_status
    assert repo.get("conflicts", historical["id"])["question_id"] == "existing-question"
    replacement = scoped_claim("c", True, facade="W", floor_min=5, floor_max=5).model_copy(update={"supersedes_ids": ["a", "b"]})
    repo.add_claim(replacement)
    run()
    closed = repo.get("conflicts", historical["id"])
    assert closed["status"] == "resolved" and closed["claim_ids"] == historical["claim_ids"]
    assert closed["question_id"] == "existing-question"
    assert "прежние наблюдения" in closed["resolution_note"]
    assert repo.get("claims", "a") == first.model_dump(mode="json")
    opposing = scoped_claim("d", False, facade="W", floor_min=5)
    repo.add_claim(opposing)
    run()
    active = [c for c in repo.all("conflicts") if c["status"] == "open"]
    assert len(active) == 1 and set(active[0]["claim_ids"]) == {"c", "d"}
    assert repo.get("conflicts", historical["id"])["status"] == "resolved"
