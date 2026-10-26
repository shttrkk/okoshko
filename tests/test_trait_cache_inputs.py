"""Synthetic cache generations must not outlive their saved model inputs."""
from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import pytest

from backend.agents.orchestrator import collect_hotel
from backend.agents.resolver import resolve_hotel
from backend.contracts import Claim, ToolCall, ToolContext, now
from backend.tests.test_api_integration import ApiRepo
from backend.tools.registry import Registry
from geo.runtime_cache import input_digest
from geo.tests.test_traits import solar_artifacts  # noqa: F401 (fixture)
from geo.traits import compute_trait


@pytest.fixture
def cache(solar_artifacts):  # noqa: F811
    hotel, directory, baseline = solar_artifacts
    (directory / "seed").mkdir()
    regional = directory / "seed/buildings.geojson"
    regional.write_text('{"features":[]}')
    baseline["inputs"] = {"/data/seed/buildings.geojson": input_digest(regional)}
    (directory / "geo/test.json").write_text(json.dumps(baseline))
    repo = ApiRepo()
    repo.insert("hotels", hotel)
    originals = [Claim.model_validate(row) for row in compute_trait(hotel, directory, "morning_sun", month=9)["claims"]]
    assert len(originals) == 2
    for claim in originals:
        repo.add_claim(claim)
    resolve_hotel(hotel["id"], repo)
    return repo, hotel, directory, baseline, regional, originals


def execute(repo, directory, month=9):
    return Registry(repo, directory).execute(
        ToolContext(hotel_id="test", characteristic_id="morning_sun", run_id="synthetic-cache-test", month=month),
        ToolCall(tool="geo_trait", params={"kind": "morning_sun"}))


def test_unchanged_direct_and_nested_inputs_reuse_without_redating(cache):
    repo, _, directory, _, _, originals = cache
    with patch("geo.traits.compute_trait", side_effect=AssertionError("unchanged cache should be reused")):
        result = execute(repo, directory)
    assert result.artifacts["cache_hit"] and len(result.claims) == 2
    before = {claim.id: claim for claim in originals}
    for claim in result.claims:
        old = before[claim.supersedes]
        assert claim.id != old.id and claim.value == old.value
        assert (claim.observed_at, claim.valid_until) == (old.observed_at, old.valid_until)


@pytest.mark.parametrize("change", ["regional-change", "regional-missing", "json-unsupported", "npz-missing"])
def test_invalid_input_and_unknown_recalculation_supersede_values_instead_of_reusing_them(cache, change):
    repo, _, directory, baseline, regional, originals = cache
    if change == "regional-change":
        regional.write_text('{"features":["changed snapshot"]}')
    elif change == "regional-missing":
        regional.unlink()
    elif change == "json-unsupported":
        baseline["floors"]["count"] = None
        (directory / "geo/test.json").write_text(json.dumps(baseline))
    else:
        (directory / "geo/test.npz").unlink()
    # Use the real adapter: it returns unknown on these incompatible inputs.
    result = execute(repo, directory)
    assert not result.artifacts.get("cache_hit") and result.warnings
    assert len(result.claims) == len(originals)
    assert {item for claim in result.claims for item in claim.supersedes_ids} == {claim.id for claim in originals}
    for claim in result.claims:
        assert claim.value == {"available": None} and claim.confidence == 0
        assert claim.method == "geo_trait_input_invalidation_v1"
        assert claim.evidence[0].inputs["input_checks"]
        assert claim.evidence[0].inputs["previous_observed_at"]
        repo.add_claim(claim)
    resolve_hotel("test", repo)
    facts = repo.find("facts", hotel_id="test", characteristic_id="morning_sun")
    assert facts and all(row["status"] == "unknown" and row["value"] == {"available": None} for row in facts)
    for old in originals:
        assert repo.get("claims", old.id) == old.model_dump(mode="json")
    # Repeated unknown checks cannot resurrect values or create more tombstones.
    assert execute(repo, directory).claims == []


def test_calculation_error_after_proven_input_change_also_retires_values(cache):
    repo, _, directory, _, regional, _ = cache
    regional.unlink()
    with patch("geo.traits.compute_trait", side_effect=ValueError("unsupported local profile")):
        result = execute(repo, directory)
    assert result.claims and all(claim.value == {"available": None} for claim in result.claims)
    assert result.warnings


def test_collect_persists_schema_valid_unknown_and_preserves_other_months(cache):
    repo, hotel, directory, _, regional, originals = cache
    august = [Claim.model_validate(row) for row in compute_trait(hotel, directory, "morning_sun", month=8)["claims"]]
    for claim in august:
        repo.add_claim(claim)
    regional.write_text('{"features":["changed snapshot"]}')
    run = collect_hotel("test", repo, ["morning_sun"], data_dir=directory)
    assert run.status == "completed"
    facts = repo.find("facts", hotel_id="test", characteristic_id="morning_sun")
    september = [fact for fact in facts if fact["scope"]["month"] == 9]
    assert september and all(fact["status"] == "unknown" for fact in september)
    assert all(fact["status"] == "computed" for fact in facts if fact["scope"]["month"] == 8)
    tombstones = [row for row in repo.find("claims", hotel_id="test") if row["method"] == "geo_trait_input_invalidation_v1"]
    assert {item for row in tombstones for item in row["supersedes_ids"]} == {claim.id for claim in originals}


def test_changed_inputs_with_a_rebuilt_horizon_use_the_new_calculation(cache):
    repo, _, directory, baseline, regional, originals = cache
    regional.write_text('{"features":["new source snapshot"]}')
    baseline["inputs"]["/data/seed/buildings.geojson"] = input_digest(regional)
    (directory / "geo/test.json").write_text(json.dumps(baseline))
    result = execute(repo, directory)
    assert result.claims and all(claim.method == originals[0].method for claim in result.claims)
    assert {item for claim in result.claims for item in claim.supersedes_ids} == {claim.id for claim in originals}
    assert all(claim.evidence[0].inputs["baseline_inputs"] == baseline["inputs"] for claim in result.claims)
    assert all(claim.observed_at == originals[0].observed_at for claim in result.claims)


@pytest.mark.parametrize("changed", [False, True], ids=["unchanged-stays-stale", "changed-becomes-unknown"])
@pytest.mark.parametrize("old_method", [False, True], ids=["current-method", "previous-method"])
def test_expired_archive_is_never_redated_as_a_fresh_value(cache, changed, old_method):
    _, hotel, directory, _, regional, originals = cache
    repo = ApiRepo()
    repo.insert("hotels", hotel)
    expired = [claim.model_copy(update={"observed_at": now()-timedelta(days=100), "valid_until": now()-timedelta(days=10),
                                       "method": "astral_morning_horizon_proxy_v1" if old_method else claim.method})
               for claim in originals]
    for claim in expired:
        repo.add_claim(claim)
    if changed:
        regional.unlink()
        result = execute(repo, directory)
        assert result.claims and all(claim.value == {"available": None} for claim in result.claims)
        for claim in result.claims:
            repo.add_claim(claim)
    else:
        with patch("geo.traits.compute_trait", side_effect=AssertionError("same expired archive is not new data")):
            result = execute(repo, directory)
        assert not result.claims and result.warnings
    resolve_hotel("test", repo)
    facts = repo.find("facts", hotel_id="test", characteristic_id="morning_sun")
    assert facts and {fact["status"] for fact in facts} == ({"unknown"} if changed else {"stale"})
    for claim in expired:
        assert repo.get("claims", claim.id) == claim.model_dump(mode="json")
