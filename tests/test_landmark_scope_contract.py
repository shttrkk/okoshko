"""Visibility targets must not change legacy scope bytes or accept arbitrary paths."""
import pytest
from pydantic import ValidationError

from backend.agents.resolver import scope_key
from backend.contracts import Claim, Scope, now


def test_legacy_scope_keeps_exact_wire_shape_even_when_nested_in_claim():
    expected = {"type": "hotel", "facade": None, "floor_min": None,
                "floor_max": None, "room_type": None, "month": None}
    assert Scope().model_dump(mode="json") == expected
    claim = Claim(hotel_id="legacy", characteristic_id="sea_view", value=True,
                  source="fixture", method="known_fixture", confidence=.7,
                  observed_at=now(), valid_until=now(), synthetic=True,
                  evidence=[{"kind": "simulation", "label": "Known synthetic fixture", "source_ids": ["fixture"], "artifact": "/tests/test_landmark_scope_contract.py"}])
    assert claim.model_dump(mode="json")["scope"] == expected
    assert scope_key(Scope()) == '{"type": "hotel"}'


def test_two_targets_share_a_room_but_keep_distinct_evidence_scope_keys():
    first = Scope(type="floor_range", facade="W", floor_min=5, floor_max=7,
                  month=9, landmark_id="marine_station")
    second = first.model_copy(update={"landmark_id": "bolshoy"})
    assert scope_key(first) != scope_key(second)
    assert Scope.model_validate_json(first.model_dump_json()) == first
    assert first.model_dump(mode="json")["landmark_id"] == "marine_station"


@pytest.mark.parametrize("target", ["../seed/hotels", "https://example.com", "Фишт", "x"*65])
def test_target_is_a_bounded_catalogue_identifier(target):
    with pytest.raises(ValidationError):
        Scope(landmark_id=target)
