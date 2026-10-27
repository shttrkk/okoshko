"""Candidate pruning must preserve the complete overlap graph and its facts."""
from __future__ import annotations

import copy
import random
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from backend.agents import resolver
from backend.contracts import CharacteristicSpec, Claim, Evidence, Scope, ToolCall

AT = datetime(2026, 10, 27, tzinfo=UTC)
SPEC = CharacteristicSpec(id="scope_fixture", name_ru="Synthetic scope fixture", description="Synthetic overlap invariants",
                          value_schema={"type": "object", "properties": {"available": {"type": "boolean"}}},
                          methods=[ToolCall(tool="viewshed_facade")])


def reference_partition_claims(claims, *, at):
    """The pre-index BFS: retained as the differential oracle, not a new policy."""
    grouped = defaultdict(list)
    candidates = defaultdict(lambda: defaultdict(list))
    for claim in claims:
        key = claim.characteristic_id, resolver.scope_key(claim.scope)
        grouped[key].append(claim)
        if claim.valid_until > at and not resolver.existential_view(claim):
            candidates[claim.characteristic_id][key].append(claim)
    for characteristic_id, by_scope in candidates.items():
        groups = list(by_scope.values())
        remaining = set(range(len(groups)))
        while remaining:
            component = {remaining.pop()}
            pending = list(component)
            while pending:
                current = pending.pop()
                neighbours = {index for index in remaining
                              if resolver.scopes_overlap(groups[current][0].scope, groups[index][0].scope)}
                remaining.difference_update(neighbours)
                component.update(neighbours)
                pending.extend(neighbours)
            if len(component) == 1:
                continue
            affected = [groups[index] for index in sorted(component)]
            for group in affected:
                key = characteristic_id, resolver.scope_key(group[0].scope)
                ids = {claim.id for claim in group}
                grouped[key] = [claim for claim in grouped[key] if claim.id not in ids]
            for scope, active in resolver._partition_component(affected):
                grouped[(characteristic_id, resolver.scope_key(scope))].extend(
                    claim.model_copy(update={"scope": scope}) for claim in active)
    return {key: group for key, group in grouped.items() if group}


def scope(facade=None, first=None, last=None, month=None, room=None):
    return Scope(type="room_type" if room else "floor_range" if facade and (first or last) else "facade" if facade else "hotel",
                 facade=facade, floor_min=first, floor_max=last, month=month, room_type=room)


def claim(identity, scoped, available=True, *, cid="scope_fixture", expired=False):
    return Claim(id=identity, hotel_id="scope-hotel", characteristic_id=cid, scope=scoped,
                 value={"available": available}, source="geo_engine", method="synthetic_scope@1", confidence=.7,
                 evidence=[Evidence(kind="simulation", label="Synthetic scope invariant", source_ids=["geo_engine"],
                                    artifact="/data/geo/synthetic-scope.json")],
                 observed_at=AT-timedelta(days=5), valid_until=AT+timedelta(days=-1 if expired else 5), synthetic=True)


def normalized(result):
    facts, conflicts = result
    return (sorted([fact.model_dump(mode="json", exclude={"updated_at"}) for fact in facts], key=lambda row: row["id"]),
            sorted([conflict.model_dump(mode="json") for conflict in conflicts], key=lambda row: row["id"]))


def differential(claims, specs=None):
    original = copy.deepcopy([item.model_dump(mode="json") for item in claims])
    result = resolver.resolve_claims("scope-hotel", claims, specs or [SPEC], [], at=AT, known_floors=8)
    with patch.object(resolver, "partition_claims", reference_partition_claims):
        expected = resolver.resolve_claims("scope-hotel", claims, specs or [SPEC], [], at=AT, known_floors=8)
    assert normalized(result) == normalized(expected)
    assert [item.model_dump(mode="json") for item in claims] == original
    return result


def test_disjoint_facades_months_and_adjacent_floors_keep_exact_fact_ids():
    claims = [claim("west-june-low", scope("W", 1, 2, 6)), claim("west-june-high", scope("W", 3, 4, 6), False),
              claim("east-june", scope("E", 1, 2, 6)), claim("west-july", scope("W", 1, 2, 7), False)]
    facts, conflicts = differential(claims)
    assert not conflicts and len(facts) == 4
    assert all(fact.status == "computed" and fact.confidence == .7 for fact in facts)
    assert {fact.id for fact in facts} == {resolver.stable_id("fact", "scope-hotel", SPEC.id, resolver.scope_key(item.scope)) for item in claims}


def test_inclusive_floor_boundary_creates_only_the_shared_floor_dispute():
    facts, conflicts = differential([claim("low", scope("W", 1, 3, 6)), claim("high", scope("W", 3, 5, 6), False)])
    assert {(fact.scope.floor_min, fact.scope.floor_max, fact.status) for fact in facts} == {
        (1, 2, "computed"), (3, 3, "disputed"), (4, 5, "computed")}
    assert len(conflicts) == 1 and set(conflicts[0].claim_ids) == {"low", "high"}


def test_wildcard_bridge_connects_disjoint_facade_month_room_domains_transitively():
    facts, _ = differential([claim("west-june-suite", scope("W", 1, 3, 6, "suite")),
                             claim("wildcard", scope(first=3, last=5)),
                             claim("east-july-king", scope("E", 5, 7, 7, "king"), False)])
    assert any(fact.scope.facade == "W" and fact.scope.month == 6 and fact.scope.room_type == "suite"
               and fact.scope.floor_min == fact.scope.floor_max == 3
               and set(fact.supporting_claim_ids) == {"west-june-suite", "wildcard"} for fact in facts)
    assert any(fact.scope.facade == "E" and fact.scope.month == 7 and fact.scope.room_type == "king"
               and fact.scope.floor_min == fact.scope.floor_max == 5
               and set(fact.supporting_claim_ids + fact.conflicting_claim_ids) == {"east-july-king", "wildcard"}
               and fact.status == "disputed" for fact in facts)


@pytest.mark.parametrize("wildcard", ["facade", "month", "room_type", "floor_min", "floor_max"])
def test_each_wildcard_axis_keeps_its_narrow_overlay(wildcard):
    fields = {"facade": "W", "first": 2, "last": 6, "month": 6, "room": "suite"}
    fields[{"room_type": "room", "floor_min": "first", "floor_max": "last"}.get(wildcard, wildcard)] = None
    broad = scope(**fields)
    differential([claim("broad", broad), claim("narrow", scope("W", 3, 4, 6, "suite"), False),
                  claim("other", scope("E", 3, 4, 7, "king"))])


def test_expired_and_existential_scopes_keep_their_existing_partition_policy():
    sea = SPEC.model_copy(update={"id": "sea_view"})
    claims = [claim("somewhere", Scope(), cid="sea_view"), claim("west", scope("W", 2, 4, 6), False, cid="sea_view"),
              claim("expired", scope("W", 3, 8, 6), cid="sea_view", expired=True)]
    facts, _ = differential(claims, [sea])
    general = next(fact for fact in facts if fact.scope == Scope())
    assert general.supporting_claim_ids == ["somewhere"]
    assert any(fact.status == "stale" and fact.supporting_claim_ids == ["expired"] for fact in facts)


def test_seeded_mixed_wildcard_graphs_match_the_original_full_output():
    randomizer = random.Random(1087)
    for case in range(40):
        claims = []
        for index in range(10):
            first = randomizer.choice([None, 1, 2, 3, 5])
            last = randomizer.choice([None, 5, 6, 8])
            claims.append(claim(f"case-{case}-{index}", scope(randomizer.choice([None, "W", "E", "SW"]), first, last,
                randomizer.choice([None, 6, 7]), randomizer.choice([None, "suite", "king"])), bool(randomizer.randrange(2)),
                expired=randomizer.randrange(10) == 0))
        differential(claims)


def test_disjoint_solar_domains_avoid_quadratic_overlap_checks_without_timing_thresholds():
    claims = [claim(f"{facade}-{month}-{first}", scope(facade, first, first+1, month))
              for facade in ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
              for month in range(1, 13) for first in (1, 3, 5)]
    with patch.object(resolver, "scopes_overlap", wraps=resolver.scopes_overlap) as indexed:
        actual = resolver.partition_claims(claims, at=AT)
    with patch.object(resolver, "scopes_overlap", wraps=resolver.scopes_overlap) as baseline:
        expected = reference_partition_claims(claims, at=AT)
    assert actual == expected
    assert baseline.call_count == len(claims) * (len(claims)-1) // 2
    assert indexed.call_count == 288  # Three floor ranges within each actual facade/month domain.
    assert indexed.call_count * 100 < baseline.call_count
