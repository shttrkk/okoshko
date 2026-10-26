from datetime import timedelta
from unittest.mock import patch

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
