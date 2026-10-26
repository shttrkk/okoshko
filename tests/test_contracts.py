from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from backend.contracts import Answer, Claim, Evidence, FieldUpdate, Scope, SearchRequest


def test_claim_requires_provenance():
    with pytest.raises(ValidationError):
        Claim(hotel_id="h", characteristic_id="sea_view", value=True)


def test_claim_rejects_empty_evidence_and_expired_interval():
    observed = datetime.now(UTC)
    evidence = Evidence(kind="calculation", label="Синтетика", source_ids=["test"], artifact="/data/test.json")
    common = dict(hotel_id="h", characteristic_id="sea_view", value=True, source="test",
                  method="test@1", confidence=.5, observed_at=observed, valid_until=observed+timedelta(days=1))
    with pytest.raises(ValidationError):
        Claim(**common, evidence=[])
    common["valid_until"] = observed - timedelta(days=1)
    with pytest.raises(ValidationError):
        Claim(**common, evidence=[evidence])


def test_parser_cannot_invent_quote():
    with pytest.raises(ValidationError):
        Answer(question_id="q", raw_text="Не знаю", updates=[FieldUpdate(characteristic_id="pets", value=True, quote="Да")])


def test_date_and_scope_bounds():
    with pytest.raises(ValidationError):
        SearchRequest(check_in="2027-09-21", check_out="2027-09-14")
    with pytest.raises(ValidationError):
        Scope(type="floor_range", facade="W", floor_min=7, floor_max=2)

