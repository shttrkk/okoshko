"""Nearby playgrounds never establish an empty set of route crossings."""
import json
from unittest.mock import patch

import pytest

from backend.agents.resolver import resolve_claims
from backend.contracts import CharacteristicSpec, ToolCall, ToolContext
from backend.contracts.validation import validate_value
from backend.seed import bootstrap_poi
from backend.tests.test_api_integration import ApiRepo
from backend.tools.registry import POI_METHOD, Registry, poi_method

HOTEL = {"id": "test-hotel", "name": "Synthetic family hotel", "lon": 39.72, "lat": 43.58}


def write(directory, crossing=False):
    features = []
    for identity, delta, tags in [("playground", .001, {"leisure": "playground"}),
                                  ("hospital", .002, {"amenity": "hospital"}),
                                  ("pharmacy", .001, {"amenity": "pharmacy", "opening_hours": "24/7"}),
                                  ("parking", .001, {"amenity": "parking"})]:
        features.append({"type": "Feature", "id": identity,
                         "geometry": {"type": "Point", "coordinates": [39.72, 43.58+delta]},
                         "properties": {"observed_at": "2026-10-25T21:21:21Z", **tags}})
    if crossing:
        features.append({"type": "Feature", "id": "unconnected-crossing",
                         "geometry": {"type": "Point", "coordinates": [39.72, 43.5805]},
                         "properties": {"highway": "crossing", "crossing": "marked", "observed_at": "2026-10-25T21:21:21Z"}})
    seed = directory / "seed"
    seed.mkdir()
    (seed / "pois.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))


def collect(repo, directory, kind, cid):
    [claim] = Registry(repo, data_dir=directory).execute(
        ToolContext(hotel_id=HOTEL["id"], characteristic_id=cid, run_id="family-test"),
        ToolCall(tool="poi_nearby", params={"kind": kind})).claims
    return claim


@pytest.mark.parametrize("mapped_crossing", [False, True])
def test_family_crossings_are_unknown_without_a_calculated_route(tmp_path, mapped_crossing):
    write(tmp_path, mapped_crossing)
    repo = ApiRepo()
    repo.upsert("hotels", HOTEL)
    claim = collect(repo, tmp_path, "family", "family_nearby")
    assert claim.value["available"] is True and claim.value["playground_distance_m"] > 0
    assert claim.value["crossings"] is None
    assert claim.method == "local_poi_family@1" and claim.confidence == .65
    assert claim.evidence[0].inputs["artifacts"] == ["/data/seed/pois.geojson"]
    assert set(claim.evidence[0].inputs["matched_osm_ids"]) == {"playground", "hospital"}
    assert any("Переходы и пересечения дорог не рассчитывались" in text for text in claim.evidence[0].limitations)
    validate_value(claim.value, repo.get("characteristics", "family_nearby")["value_schema"])


def test_new_family_method_supersedes_the_old_empty_crossings_claim_append_only(tmp_path):
    write(tmp_path)
    repo = ApiRepo()
    repo.upsert("hotels", HOTEL)
    template = collect(repo, tmp_path, "family", "family_nearby")
    legacy = template.model_copy(update={"id": "family-legacy", "method": POI_METHOD,
                                         "value": {**template.value, "crossings": []}})
    stored = legacy.model_dump(mode="json")
    repo.add_claim(legacy)
    current = collect(repo, tmp_path, "family", "family_nearby")
    assert current.id != legacy.id and current.supersedes_ids == [legacy.id]
    assert current.observed_at == legacy.observed_at and current.valid_until == legacy.valid_until
    assert current.confidence == legacy.confidence
    assert repo.get("claims", legacy.id) == stored
    specs = [CharacteristicSpec.model_validate(repo.get("characteristics", "family_nearby"))]
    [fact], conflicts = resolve_claims(HOTEL["id"], [legacy, current], specs, [])
    assert not conflicts and fact.supporting_claim_ids == [current.id]
    assert fact.value["crossings"] is None and fact.status == "computed"


def test_family_version_does_not_change_pharmacy_car_or_nightlife_methods():
    assert poi_method("family") == "local_poi_family@1"
    assert poi_method("pharmacy") == POI_METHOD == "local_poi_lookup@2"
    assert poi_method("car") == "local_poi_lookup@3"
    assert poi_method("nightlife") == "local_poi_nightlife@1"


def test_backfill_rechecks_legacy_family_without_recollecting_current_sibling_methods(tmp_path):
    write(tmp_path)
    repo = ApiRepo()
    repo.upsert("hotels", HOTEL)
    repo.tables["characteristics"] = {cid: repo.get("characteristics", cid)
                                     for cid in ("family_nearby", "night_pharmacy", "car_access")}
    template = collect(repo, tmp_path, "family", "family_nearby")
    legacy = template.model_copy(update={"id": "family-legacy", "method": POI_METHOD,
                                         "value": {**template.value, "crossings": []}})
    repo.add_claim(legacy)
    pharmacy = collect(repo, tmp_path, "pharmacy", "night_pharmacy")
    car = collect(repo, tmp_path, "car", "car_access")
    repo.insert_claims([pharmacy, car])
    called = []
    execute = Registry.execute
    def observed(self, context, call):
        called.append(context.characteristic_id)
        return execute(self, context, call)
    with patch.object(Registry, "execute", observed):
        bootstrap_poi(repo, tmp_path)
    assert called == ["family_nearby"]
    assert repo.get("claims", pharmacy.id) == pharmacy.model_dump(mode="json")
    assert repo.get("claims", car.id) == car.model_dump(mode="json")
    new = [row for row in repo.find("claims", characteristic_id="family_nearby") if row["id"] != legacy.id]
    assert len(new) == 1 and new[0]["method"] == "local_poi_family@1"
    assert new[0]["value"]["crossings"] is None and new[0]["supersedes_ids"] == [legacy.id]
