from datetime import timedelta
from unittest.mock import patch

import pytest

from backend.contracts import Claim, Evidence, Fact, ToolCall, ToolContext, now
from backend.tests.test_api_integration import ApiRepo
from backend.tools.registry import Registry
from backend.tools.scoped_inputs import compatible_inputs


def fact(cid, facade, low=1, high=5):
    return Fact(hotel_id="test-1", characteristic_id=cid, scope={"type": "floor_range", "facade": facade, "floor_min": low, "floor_max": high}, value={"available": True}, confidence=.85, status="computed", supporting_claim_ids=["input"], valid_until=now()+timedelta(days=30), evidence=[Evidence(kind="simulation", label="Known synthetic input", source_ids=["geo_engine"], artifact="/data/eval/README.md")]).model_dump(mode="json")


def test_composite_cannot_mix_facades_or_disjoint_floors():
    assert not compatible_inputs([fact("sea_view", "W"), fact("quiet_night", "E")], ["sea_view", "quiet_night"], 9)
    assert not compatible_inputs([fact("sea_view", "W", 1, 2), fact("quiet_night", "W", 3, 5)], ["sea_view", "quiet_night"], 9)
    [(scope, inputs)] = compatible_inputs([fact("sea_view", "W", 2, 5), fact("quiet_night", "W", 3, 6)], ["sea_view", "quiet_night"], 9)
    assert (scope.facade, scope.floor_min, scope.floor_max) == ("W", 3, 5)
    assert len(inputs) == 2


def test_derive_preserves_shared_scope_and_synthetic_provenance():
    repo = ApiRepo()
    for row in [fact("sea_view", "W", 2, 5), fact("quiet_night", "W", 3, 6)]:
        row["synthetic"] = True
        repo.insert("facts", row)
    result = Registry(repo)._derive(ToolContext(hotel_id="test-1", characteristic_id="quiet_sea", run_id="run"), ToolCall(tool="derive", params={"expr": "sea_view.available and quiet_night.available"}))
    [claim] = result.claims
    assert claim.value["available"] is True and claim.synthetic
    assert (claim.scope.facade, claim.scope.floor_min, claim.scope.floor_max) == ("W", 3, 5)


def test_repeat_poi_collect_supersedes_without_reusing_primary_key(tmp_path):
    repo = ApiRepo()
    context = ToolContext(hotel_id="test-1", characteristic_id="night_pharmacy", run_id="run")
    class EmptyTree:
        def query(self, point):
            return []
    repo.insert("hotels", {"id": "test-1", "name": "Synthetic test", "lon": 39.72, "lat": 43.58})
    registry = Registry(repo, data_dir=tmp_path)
    with patch("backend.tools.registry.feature_index", return_value=([], [], EmptyTree())):
        [first] = registry._poi(context, ToolCall(tool="poi_nearby")).claims
        repo.add_claim(first)
        second_result = registry._poi(context, ToolCall(tool="poi_nearby"))
        [second] = second_result.claims
        assert first.id != second.id and first.id in second.supersedes_ids
        assert second.observed_at == first.observed_at and second.valid_until == first.valid_until
        assert second_result.artifacts["cache_hit"] is True
        Claim.model_validate(second)


def test_hotel_wide_view_existence_never_proves_composite_room():
    rows = [fact("sea_view", "W"), fact("quiet_night", "W")]
    for row in rows:
        row["scope"] = {"type": "hotel"}
    assert not compatible_inputs(rows, ["sea_view", "quiet_night"], 9)
    assert not compatible_inputs(rows, [], 9)
    result = Registry(ApiRepo())._derive(ToolContext(hotel_id="test-1", characteristic_id="quiet_sea", run_id="run"), ToolCall(tool="derive", params={"expr": "True"}))
    assert not result.claims and result.warnings


@pytest.mark.parametrize("status", ["disputed", "unknown"])
def test_composite_cannot_hide_narrow_uncertainty_behind_a_broad_default(status):
    wide = fact("sea_view", "W", 1, 10)
    narrow = fact("sea_view", "W", 5, 5)
    narrow.update(status=status, value={"available": None})
    other = fact("quiet_night", "W", 1, 10)
    assert not compatible_inputs([wide, narrow, other], ["sea_view", "quiet_night"], 9)
    repo = ApiRepo()
    for row in [wide, narrow, other]:
        repo.insert("facts", row)
    result = Registry(repo)._derive(ToolContext(hotel_id="test-1", characteristic_id="quiet_sea", run_id="run"),
                                    ToolCall(tool="derive", params={"expr": "sea_view.available and quiet_night.available"}))
    assert not result.claims and result.warnings


def test_composite_preserves_explicit_partition_outside_disputed_floor():
    wide = fact("sea_view", "W", 1, 10)
    dispute = fact("sea_view", "W", 5, 5)
    dispute.update(status="disputed", value={"available": None})
    other = fact("quiet_night", "W", 1, 4)
    [(scope, _)] = compatible_inputs([wide, dispute, other], ["sea_view", "quiet_night"], 9)
    assert (scope.floor_min, scope.floor_max) == (1, 4)
    other["scope"]["floor_min"], other["scope"]["floor_max"] = 6, 10
    [(scope, _)] = compatible_inputs([wide, dispute, other], ["sea_view", "quiet_night"], 9)
    assert (scope.floor_min, scope.floor_max) == (6, 10)


def test_composite_handles_room_type_wildcard_without_inventing_other_rooms():
    wide = fact("sea_view", "W", 1, 10)
    dispute = fact("sea_view", "W", 1, 10)
    dispute["scope"]["room_type"] = "suite"
    dispute.update(status="disputed", value={"available": None})
    other = fact("quiet_night", "W", 1, 10)
    assert not compatible_inputs([wide, dispute, other], ["sea_view", "quiet_night"], 9)
    other["scope"]["room_type"] = "standard"
    [(scope, _)] = compatible_inputs([wide, dispute, other], ["sea_view", "quiet_night"], 9)
    assert scope.room_type == "standard"


def test_composite_keeps_resolved_narrow_partition_but_not_its_broad_bypass():
    wide = fact("sea_view", "W", 1, 10)
    narrow = fact("sea_view", "W", 5, 5)
    narrow["scope"]["room_type"] = "suite"
    narrow["value"] = {"available": False}
    other = fact("quiet_night", "W", 5, 5)
    other["scope"]["room_type"] = "suite"
    [(scope, inputs)] = compatible_inputs([wide, narrow, other], ["sea_view", "quiet_night"], 9)
    assert scope.room_type == "suite" and next(f for f in inputs if f.characteristic_id == "sea_view").id == narrow["id"]


def test_composite_ignores_other_month_or_expired_dispute():
    wide = fact("sea_view", "W", 1, 10)
    dispute = fact("sea_view", "W", 5, 5)
    dispute["scope"]["month"] = 9
    dispute.update(status="disputed", value={"available": None})
    other = fact("quiet_night", "W", 1, 10)
    assert not compatible_inputs([wide, dispute, other], ["sea_view", "quiet_night"], 9)
    assert compatible_inputs([wide, dispute, other], ["sea_view", "quiet_night"], 8)
    dispute["valid_until"] = (now()-timedelta(seconds=1)).isoformat()
    assert compatible_inputs([wide, dispute, other], ["sea_view", "quiet_night"], 9)


def test_morning_swim_composite_still_uses_known_hotel_route_and_monthly_climate():
    repo = ApiRepo()
    for cid, value, month in [("beach_walk", {"available": True, "minutes": 7}, None), ("climate", {"month": 9, "water_c": 23}, 9)]:
        row = fact(cid, "W")
        row["scope"] = {"type": "hotel", "month": month}
        row["value"] = value
        row["synthetic"] = True
        repo.insert("facts", row)
    [claim] = Registry(repo)._derive(ToolContext(hotel_id="test-1", characteristic_id="morning_swim", run_id="run", month=9),
                                    ToolCall(tool="derive", params={"expr": "beach_walk.minutes <= 10 and climate.water_c >= 20"})).claims
    assert claim.scope.month == 9 and claim.scope.facade is None
    assert claim.value["available"] is True and claim.value["minutes"] == 7 and claim.value["water_c"] == 23
    assert claim.confidence == .85 and claim.synthetic is True and claim.evidence
