"""A new geometry must not leave reusable aliases/composites on old inputs."""
from __future__ import annotations

import copy
from datetime import timedelta
from unittest.mock import patch

import pytest

from backend.agents.dependencies import NAMESPACE, metadata
from backend.agents.freshness import apply_change
from backend.agents.resolver import resolve_all, resolve_claims, resolve_hotel
from backend.contracts import (
    CharacteristicSpec,
    Claim,
    Evidence,
    ExpectationPassport,
    PassportItem,
    Scope,
    ToolContext,
    now,
)
from backend.contracts.validation import validate_value
from backend.tests.test_api_integration import ApiRepo
from backend.tests.test_freshness import observation
from backend.tools.registry import Registry
from backend.tracing import Trace
from geo.traits import METHODS


class Repo(ApiRepo):
    def replace_materialized_facts(self, hotel_ids, rows):
        self.tables["facts"] = {key: row for key, row in self.tables.get("facts", {}).items()
                                if row["hotel_id"] not in hotel_ids}
        self.upsert_many("facts", rows)


def spec(repo, cid, tool="viewshed_facade", params=None, schema=None):
    base = repo.get("characteristics", "sea_view")
    value = CharacteristicSpec.model_validate({**base, "id": cid, "name_ru": cid,
        "methods": [{"tool": tool, "params": params or {"target": "sea"}}],
        "value_schema": schema or base["value_schema"]})
    repo.insert("characteristics", value.model_dump(mode="json"))
    return value


def collect(repo, cid):
    current = CharacteristicSpec.model_validate(repo.get("characteristics", cid))
    result = Registry(repo).execute(ToolContext(hotel_id="test-hotel", characteristic_id=cid, run_id="test-collect"), current.methods[0])
    assert result.claims, result.warnings
    repo.insert_claims(result.claims)
    resolve_hotel("test-hotel", repo)
    return result.claims


def setup():
    repo = Repo()
    repo.add_claim(observation("sea-original", 1, 8, True))
    resolve_hotel("test-hotel", repo)
    spec(repo, "custom_sea")
    spec(repo, "custom_derived", "derive", {"expr": "sea_view.available"})
    spec(repo, "custom_chain", "derive", {"expr": "custom_derived.available"})
    return repo


def change(repo, identity="sea-changed", available=False):
    claim = observation(identity, 1, 8, available)
    artifact = "/data/geo/" + identity + ".json"
    claim.evidence[0].artifact = artifact
    fixture = {"type": "Feature", "properties": {"synthetic": True},
               "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 1], [0, 0]]]}}
    return apply_change(repo, "test-hotel", fixture,
                        {"geo": {"artifact": artifact}, "claims": [claim.model_dump(mode="json")]},
                        Trace(repo, "refresh", hotel_id="test-hotel"))


def facts(repo, cid):
    return repo.find("facts", hotel_id="test-hotel", characteristic_id=cid)


def assert_unknown(repo, *cids):
    for cid in cids:
        rows = facts(repo, cid)
        assert rows
        assert all(row["status"] == "unknown" and row["confidence"] == 0 and row["value"] is None for row in rows)
        assert all(row["evidence"] and row["synthetic"] for row in rows)
        assert all(row["evidence"][0]["inputs"][NAMESPACE]["reason"] == "geometry_changed" for row in rows)


def test_alias_and_derive_chain_retire_append_only_with_event_and_durable_reason():
    repo = setup()
    claims = [*collect(repo, "custom_sea"), *collect(repo, "custom_derived"), *collect(repo, "custom_chain")]
    ledger_before = copy.deepcopy(repo.all("claims"))
    old_sea_id = facts(repo, "sea_view")[0]["id"]
    event = change(repo)
    assert facts(repo, "sea_view")[0]["id"] == old_sea_id  # ID stability is not input stability.
    assert facts(repo, "sea_view")[0]["value"]["available"] is False
    assert set(event.payload["invalidated_claim_ids"]) == {claim.id for claim in claims}
    assert_unknown(repo, "custom_sea", "custom_derived", "custom_chain")
    assert {fact.characteristic_id for fact in event.after} >= {"custom_sea", "custom_derived", "custom_chain"}
    assert all(repo.get("claims", old["id"]) == old for old in ledger_before)
    assert not any(row["characteristic_id"].startswith("custom_") for row in repo.all("claims") if row["id"] not in {old["id"] for old in ledger_before})
    for _ in range(2):
        resolve_hotel("test-hotel", repo)
        resolve_all(repo)
        assert_unknown(repo, "custom_sea", "custom_derived", "custom_chain")
    # The canonical claim alone carries the reason even if the Event insert or
    # materialized snapshot was lost. No process-local retirement cache exists.
    restored = Repo()
    restored.tables = copy.deepcopy(repo.tables)
    restored.tables["change_events"] = {}
    restored.tables["facts"] = {}
    resolve_all(restored)
    assert_unknown(restored, "custom_sea", "custom_derived", "custom_chain")


@pytest.mark.parametrize("schema,value", [({"type": "boolean"}, True),
    ({"type": "object", "properties": {"score": {"type": "number"}}, "required": ["score"], "additionalProperties": False}, {"score": 1})])
def test_arbitrary_schema_does_not_receive_unknown_object_claim(schema, value):
    repo = setup()
    spec(repo, "custom_strict", schema=schema)
    original = observation("strict-alias", 1, 8, True).model_copy(update={"characteristic_id": "custom_strict", "value": value})
    validate_value(original.value, schema)
    repo.add_claim(original)
    resolve_hotel("test-hotel", repo)
    change(repo)
    assert_unknown(repo, "custom_strict")
    rows = repo.find("claims", characteristic_id="custom_strict")
    assert len(rows) == 1 and rows[0]["value"] == value
    validate_value(rows[0]["value"], schema)


def test_second_change_does_not_revive_old_superseded_alias_and_allows_new_tools():
    repo = setup()
    first = collect(repo, "custom_sea")[0]
    second = collect(repo, "custom_sea")[0]
    # Reproduce a historical chain: dropping the newer retired claim BEFORE
    # computing supersession would incorrectly revive its predecessor.
    stored = repo.tables["claims"][second.id]
    stored["supersedes_ids"] = [first.id]
    event1 = change(repo)
    assert_unknown(repo, "custom_sea")
    rerun = collect(repo, "custom_sea")
    assert facts(repo, "custom_sea")[0]["status"] == "computed"
    assert facts(repo, "custom_sea")[0]["value"]["available"] is False
    event2 = change(repo, "sea-changed-again", True)
    assert event2.id != event1.id
    assert set(event2.payload["invalidated_claim_ids"]) == {claim.id for claim in rerun}
    for _ in range(2):
        resolve_all(repo)
        assert_unknown(repo, "custom_sea")
    assert change(repo, "sea-changed-again", True).id == event2.id
    assert len(repo.all("change_events")) == 2
    collect(repo, "custom_sea")
    assert facts(repo, "custom_sea")[0]["value"]["available"] is True


def test_unchanged_route_climate_and_disjoint_view_scope_are_preserved():
    repo = setup()
    route = observation("route-original", 1, 8, True).model_copy(update={"characteristic_id": "beach_walk", "scope": Scope(), "synthetic": False,
        "value": {"available": True, "minutes": 7, "route_ref": "/data/geo/old.json#route"}})
    climate = route.model_copy(deep=True, update={"id": "climate-original", "characteristic_id": "climate", "value": {"water_c": 23, "month": 9}})
    east = observation("east-original", 1, 8, True).model_copy(update={"scope": Scope(type="floor_range", facade="E", floor_min=1, floor_max=8)})
    for claim in [route, climate, east]:
        repo.add_claim(Claim.model_validate(claim.model_dump()))
    spec(repo, "custom_beach", "walk_route", {})
    spec(repo, "custom_weather", "derive", {"expr": "climate.water_c >= 20"})
    resolve_hotel("test-hotel", repo)
    collect(repo, "custom_beach")
    collect(repo, "custom_weather")
    aliases = collect(repo, "custom_sea")
    # The east canonical input is kept unchanged in the replacement.
    replacement = observation("west-replacement", 1, 8, False)
    east_new = Claim.model_validate(east.model_dump()).model_copy(update={"id": "east-replacement"})
    route_new = Claim.model_validate(route.model_dump()).model_copy(update={"id": "route-replacement", "value": {**route.value, "route_ref": "/data/geo/new.json#route"}})
    artifact = "/data/geo/unchanged-inputs.json"
    rows = []
    for claim in [replacement, east_new, route_new]:
        claim.evidence[0].artifact = artifact
        rows.append(claim.model_dump(mode="json"))
    event = apply_change(repo, "test-hotel", {"geometry": {"type": "Polygon", "coordinates": []}},
                         {"geo": {"artifact": artifact}, "claims": rows}, Trace(repo, "refresh"))
    assert all(row["status"] == "computed" for cid in ["custom_beach", "custom_weather"] for row in facts(repo, cid))
    east_alias = next(claim for claim in aliases if claim.scope.facade == "E")
    assert east_alias.id not in event.payload["invalidated_claim_ids"]
    assert any(row["scope"]["facade"] == "E" and row["status"] == "computed" for row in facts(repo, "custom_sea"))


def test_legacy_geometry_trait_alias_and_its_derived_chain_need_actual_recalculation():
    repo = setup()
    spec(repo, "custom_sun", "geo_trait", {"kind": "morning_sun"})
    spec(repo, "custom_sun_chain", "derive", {"expr": "custom_sun.available"})
    original = observation("legacy-sun-alias", 1, 8, True).model_copy(update={"characteristic_id": "custom_sun"})
    repo.add_claim(original)
    resolve_hotel("test-hotel", repo)
    collect(repo, "custom_sun_chain")
    event = change(repo, available=True)  # Sea values unchanged, geometry inputs changed.
    assert original.id in event.payload["invalidated_claim_ids"]
    assert_unknown(repo, "custom_sun", "custom_sun_chain")


def test_same_canonical_values_leave_aliases_and_derived_observations_unchanged():
    repo = setup()
    collect(repo, "custom_sea")
    collect(repo, "custom_derived")
    event = change(repo, available=True)
    assert event.payload["invalidated_claim_ids"] == []
    assert all(row["status"] == "computed" for cid in ["custom_sea", "custom_derived"] for row in facts(repo, cid))


def test_geo_trait_alias_already_calculated_on_the_exact_new_variant_is_preserved():
    repo = setup()
    spec(repo, "custom_sun", "geo_trait", {"kind": "morning_sun"})
    original_sun = observation("sun-original", 1, 8, True).model_copy(update={"characteristic_id": "morning_sun"})
    alias = original_sun.model_copy(deep=True, update={"id": "sun-variant-alias", "characteristic_id": "custom_sun"})
    alias.evidence[0].artifact = "/data/geo/sea-changed.json"
    alias.evidence[0].inputs[NAMESPACE] = metadata(geometry_sensitive=True, geometry_trait_kind="morning_sun")
    repo.insert_claims([original_sun, alias])
    resolve_hotel("test-hotel", repo)
    event = change(repo)
    assert alias.id not in event.payload["invalidated_claim_ids"]
    assert facts(repo, "custom_sun")[0]["status"] == "computed"
    assert facts(repo, "morning_sun")[0]["status"] == "unknown"


def test_retired_geo_trait_alias_requires_new_current_geometry_tool_result(tmp_path):
    repo = setup()
    spec(repo, "custom_sun", "geo_trait", {"kind": "morning_sun"})
    original = observation("sun-alias-original", 1, 8, True).model_copy(update={
        "characteristic_id": "custom_sun", "method": METHODS["morning_sun"]})
    repo.add_claim(original)
    resolve_hotel("test-hotel", repo)
    event = change(repo)
    assert_unknown(repo, "custom_sun")
    context = ToolContext(hotel_id="test-hotel", characteristic_id="custom_sun", run_id="test-current")
    call = CharacteristicSpec.model_validate(repo.get("characteristics", "custom_sun")).methods[0]
    registry = Registry(repo, data_dir=tmp_path)
    with patch("geo.traits.compute_trait", return_value={"claims": [], "warnings": ["No input"], "artifacts": {}}) as compute:
        result = registry.execute(context, call)
    assert not result.claims  # Cannot clone the retired cache even if it is unexpired.
    assert compute.call_args.kwargs["geo_artifact"] == event.payload["geo_artifact"]
    replacement = original.model_copy(deep=True, update={"id": "sun-recomputed", "characteristic_id": "morning_sun", "value": {"available": False}})
    replacement.evidence[0].artifact = event.payload["geo_artifact"]
    with patch("geo.traits.compute_trait", return_value={"claims": [replacement.model_dump(mode="json")], "warnings": [], "artifacts": {}}):
        result = registry.execute(context, call)
    assert result.claims and result.claims[0].id != original.id
    assert result.claims[0].evidence[0].inputs[NAMESPACE]["geometry_sensitive"] is True
    repo.insert_claims(result.claims)
    resolve_all(repo)
    assert facts(repo, "custom_sun")[0]["status"] == "computed"
    assert facts(repo, "custom_sun")[0]["value"]["available"] is False


def test_recomputed_derive_chain_can_be_invalidated_by_the_second_change():
    repo = setup()
    collect(repo, "custom_derived")
    collect(repo, "custom_chain")
    change(repo)
    regenerated = [*collect(repo, "custom_derived"), *collect(repo, "custom_chain")]
    assert all(row["status"] == "computed" and row["value"]["available"] is False
               for cid in ["custom_derived", "custom_chain"] for row in facts(repo, cid))
    event = change(repo, "second-derived-change", True)
    assert set(event.payload["invalidated_claim_ids"]) == {claim.id for claim in regenerated}
    resolve_all(repo)
    assert_unknown(repo, "custom_derived", "custom_chain")


@pytest.mark.parametrize("source,anchor_cid,hotel", [("hotel_website", "sea_view", "test-hotel"),
    ("geo_engine", "custom_sea", "test-hotel"), ("geo_engine", "sea_view", "other-hotel")])
def test_foreign_or_untrusted_invalidation_metadata_cannot_retire(source, anchor_cid, hotel):
    repo = setup()
    [alias] = collect(repo, "custom_sea")
    anchor = observation("untrusted", 1, 8, False).model_copy(deep=True, update={"source": source, "characteristic_id": anchor_cid, "hotel_id": hotel, "method": "untrusted@1"})
    anchor.evidence[0].inputs[NAMESPACE] = metadata(hotel_id="test-hotel", reason="geometry_changed", invalidated_claim_ids=[alias.id])
    claims = [Claim.model_validate(row) for row in repo.all("claims")] + [anchor]
    resolved, _ = resolve_claims("test-hotel", claims, [CharacteristicSpec.model_validate(row) for row in repo.all("characteristics")], [])
    assert any(fact.characteristic_id == "custom_sea" and alias.id in fact.supporting_claim_ids + fact.conflicting_claim_ids for fact in resolved)


def test_independent_hoteliers_evidence_survives_model_invalidation():
    repo = setup()
    collect(repo, "custom_sea")
    real = observation("real-hoteliers-quote", 1, 8, True).model_copy(update={
        "characteristic_id": "custom_sea", "source": "hotelier_real", "method": "confirmed_answer@1", "synthetic": False,
        "observed_at": now() - timedelta(days=1),
        "evidence": [Evidence(kind="hotelier", label="Explicit independent scope", source_ids=["hotelier_real"], quote="На запад с первого по восьмой этаж море видно")],
    })
    repo.add_claim(real)
    resolve_hotel("test-hotel", repo)
    change(repo)
    assert any(row["status"] == "confirmed" and not row["synthetic"] and real.id in row["supporting_claim_ids"] for row in facts(repo, "custom_sea"))


def test_invalidated_custom_fact_marks_its_confirmed_passport_at_risk():
    repo = setup()
    collect(repo, "custom_sea")
    [previous] = facts(repo, "custom_sea")
    item = PassportItem(characteristic_id="custom_sea", text="Selected model view", scope=previous["scope"],
                        fact_ids=[previous["id"]], status="confirmed")
    passport = ExpectationPassport(hotel_id="test-hotel", status="confirmed", items=[item])
    repo.insert("expectation_passports", passport.model_dump(mode="json"))
    event = change(repo)
    updated = repo.get("expectation_passports", passport.id)
    assert updated["status"] == "at_risk" and updated["items"][0]["status"] == "at_risk"
    assert event.notifications and event.notifications[0]["synthetic"]
    assert event.notifications[0]["item_ids"] == [item.id]
