from datetime import UTC, datetime, timedelta

from backend.agents.resolver import resolve_claims
from backend.contracts import Claim, Evidence, Scope

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

