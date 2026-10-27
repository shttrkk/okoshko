"""A change invalidates target-specific LOS before passport notification."""
from datetime import timedelta
from unittest.mock import patch

import pytest

from backend.agents.freshness import simulate_change
from backend.agents.resolver import resolve_hotel
from backend.api.passports import _combine_scope, _comparison, _fact_candidates, scopes_compatible
from backend.contracts import Claim, Criterion, ExpectationPassport, Fact, PassportItem, Scope, now
from backend.tests.test_api_integration import ApiRepo
from backend.tests.test_freshness import observation, write_result


def landmark(target="sea_terminal", available=True):
    claim = observation("landmark-" + target, 1, 8, available)
    claim.characteristic_id = "landmark_view"
    claim.scope.landmark_id = target
    claim.value = {"available": available, "landmark_id": target}
    claim.evidence[0].label = "Synthetic known-answer target LOS"
    return Claim.model_validate(claim.model_dump(mode="json"))


def test_target_filter_is_distinct_from_physical_room_compatibility():
    repo = ApiRepo()
    for claim in (landmark(), landmark("bolshoy", False)):
        repo.add_claim(claim)
    facts = resolve_hotel("test-hotel", repo)
    wanted = Scope(facade="W", floor_min=5, floor_max=5, landmark_id="sea_terminal")
    item = PassportItem(characteristic_id="landmark_view", text="Вид на вокзал", scope=wanted)
    choices = _fact_candidates(item, facts, [Criterion(characteristic_id="landmark_view", label="Вокзал", scope=wanted)])
    assert choices and all(fact.scope.landmark_id == "sea_terminal" for _, fact in choices)
    assert scopes_compatible([wanted, wanted.model_copy(update={"landmark_id": "bolshoy"})])
    assert _combine_scope(wanted, Scope(facade="W")).landmark_id == "sea_terminal"
    assert _combine_scope(wanted, Scope(landmark_id="bolshoy")) is None


def test_new_geometry_stales_landmark_and_notifies_before_event_is_written(tmp_path):
    repo = ApiRepo()
    repo.add_claim(observation("old-sea", 1, 8, True))
    repo.add_claim(landmark())
    facts = resolve_hotel("test-hotel", repo)
    old = next(fact for fact in facts if fact.characteristic_id == "landmark_view")
    item = PassportItem(characteristic_id="landmark_view", text="Вид на вокзал", scope=old.scope,
                        fact_ids=[old.id], status="confirmed")
    passport = ExpectationPassport(hotel_id="test-hotel", status="confirmed", items=[item])
    repo.insert("expectation_passports", passport.model_dump(mode="json"))
    result = write_result(tmp_path / "data", [observation("new-sea", 1, 8, False)])
    with patch("geo.service.compute_hotel", return_value=result):
        event = simulate_change(repo, "test-hotel", data_dir=tmp_path / "data", runtime=tmp_path / "runtime")
    changed = [fact for fact in event.after if fact.characteristic_id == "landmark_view"]
    assert changed and all(fact.status == "stale" and fact.confidence == 0 for fact in changed)
    assert changed[0].value == old.value and changed[0].evidence == old.evidence
    assert repo.get("expectation_passports", passport.id)["status"] == "at_risk"
    assert any(item.id in message["item_ids"] for message in event.notifications)
    restored = resolve_hotel("test-hotel", repo)
    assert all(fact.status == "stale" for fact in restored if fact.characteristic_id == "landmark_view")
    assert old.valid_until > now() + timedelta(days=1)


@pytest.mark.parametrize('cid,field,value,op,threshold,expected', [
    ('airport_noise', 'runway_distance_m', 6500, 'gte', 3000, True),
    ('veterinary_nearby', 'minutes', 18, 'lte', 20, True),
    ('veterinary_nearby', 'minutes', 18, 'lte', 15, False),
    ('pet_shop_nearby', 'distance_m', 1500, 'lte', 2000, True),
])
def test_passport_uses_known_metrics_under_edited_thresholds(cid, field, value, op, threshold, expected):
    fact = Fact(id='f', hotel_id='h', characteristic_id=cid, value={'available': False, field: value},
                status='computed', confidence=.7, valid_until=now() + timedelta(days=30))
    criterion = Criterion(characteristic_id=cid, label=cid, field=field, op=op, value=threshold)
    assert _comparison(fact, criterion) is expected
