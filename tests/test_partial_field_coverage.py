"""A measured field survives an unknown sibling availability flag."""
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from backend.agents.ranker import rank_hotels
from backend.agents.resolver import resolve_claims
from backend.api.catalog import status
from backend.contracts import CharacteristicSpec, Claim, Criterion, Evidence, Fact, Hotel, ParsedQuery, now
from backend.coverage import region_coverage
from backend.quality import current_value
from backend.tests.test_api_integration import ApiRepo

ROOT = Path(__file__).resolve().parents[1]
HOTEL = Hotel(id="partial", name="Synthetic partial-field input", lat=43.58, lon=39.72)


@pytest.fixture(scope="module")
def specs():
    return [CharacteristicSpec.model_validate(yaml.safe_load(path.read_text()))
            for path in sorted((ROOT / "data/characteristics").glob("*.yaml"))]


def observation(cid, value):
    return Claim(id="partial-"+cid, hotel_id=HOTEL.id, characteristic_id=cid, value=value,
                 source="geo_engine", method="synthetic_partial_field_test", confidence=.8,
                 observed_at=now()-timedelta(days=1), valid_until=now()+timedelta(days=30), synthetic=True,
                 evidence=[Evidence(kind="simulation", label="Synthetic partial-field observation",
                                    source_ids=["geo_engine"], artifact="/data/eval/README.md")])


def resolve(cid, value, specs):
    claim = observation(cid, value)
    facts, _ = resolve_claims(HOTEL.id, [claim], specs, [])
    return next(fact for fact in facts if fact.characteristic_id == cid)


def test_known_park_distance_supports_the_requested_threshold_without_proving_availability(specs):
    fact = resolve("green_nearby", {"available": None, "distance_m": 900}, specs)
    assert fact.status == "computed" and fact.confidence == .8 and fact.value["available"] is None
    for limit, expected in [(1000, "yes"), (500, "no")]:
        parsed = ParsedQuery(text="", must=[Criterion(characteristic_id="green_nearby", label="Парк",
                                                      field="distance_m", op="lte", value=limit)])
        [hit] = rank_hotels([HOTEL], [fact], parsed, specs)
        assert hit.verdicts[0].status == expected
        assert (hit.group == "all") == (limit == 1000)
    parsed = ParsedQuery(text="", must=[Criterion(characteristic_id="green_nearby", label="Доступность парка", field="available")])
    [hit] = rank_hotels([HOTEL], [fact], parsed, specs)
    assert hit.verdicts[0].status == "unknown" and hit.group != "all"
    row = fact.model_dump(mode="json")
    assert region_coverage([row], specs, 1)["green_nearby"]["known_hotels"] == 1
    # Provenance metrics deliberately keep their existing whole-value policy.
    assert not current_value(row, now())


def test_categorical_primary_field_survives_unknown_availability(specs):
    fact = resolve("beach_surface", {"available": None, "surface": "sand"}, specs)
    assert fact.status == "computed" and fact.confidence == .8
    parsed = ParsedQuery(text="", must=[Criterion(characteristic_id="beach_surface", label="Песок", field="surface", value="sand")])
    [hit] = rank_hotels([HOTEL], [fact], parsed, specs)
    assert hit.group == "all" and hit.verdicts[0].status == "yes"


@pytest.mark.parametrize("cid,field", [("car_access", "parking_distance_m"),
                                      ("family_nearby", "playground_distance_m"), ("night_pharmacy", "distance_m")])
def test_existing_secondary_poi_distances_still_materialize(cid, field, specs):
    fact = resolve(cid, {"available": None, field: 350}, specs)
    assert fact.status == "computed" and fact.confidence == .8 and fact.value["available"] is None
    parsed = ParsedQuery(text="", must=[Criterion(characteristic_id=cid, label=cid, field=field, op="lte", value=500)])
    [hit] = rank_hotels([HOTEL], [fact], parsed, specs)
    assert hit.verdicts[0].status == "yes"
    # Coverage follows the declared default (parking meters for car_access,
    # availability for family/pharmacy), independently of the edited criterion.
    spec = next(spec for spec in specs if spec.id == cid)
    assert region_coverage([fact.model_dump(mode="json")], specs, 1)[cid]["known_hotels"] == int(spec.search.field == field)


@pytest.mark.parametrize("value", [None, True, "900", float("nan"), float("inf")])
def test_unknown_or_wrongly_typed_numeric_fields_do_not_become_observations(value, specs):
    fact = resolve("green_nearby", {"available": None, "distance_m": value, "model": "known model", "month": 9}, specs)
    assert fact.status == "unknown" and fact.confidence == 0
    assert region_coverage([fact.model_dump(mode="json")], specs, 1)["green_nearby"]["status"] == "no_data"


@pytest.mark.parametrize("value", [False, 23, "", "   "])
def test_categorical_fields_require_the_declared_string_type(value, specs):
    fact = resolve("beach_surface", {"available": None, "surface": value}, specs)
    assert fact.status == "unknown" and fact.confidence == 0


def test_declaring_metadata_as_numeric_does_not_turn_it_into_a_measured_field(specs):
    spec = next(spec for spec in specs if spec.id == "green_nearby").model_copy(deep=True)
    spec.search.field, spec.search.default_value = "month", 9
    fact = resolve("green_nearby", {"available": None, "month": 9, "model": "DSM"}, [spec])
    assert fact.status == "unknown" and fact.confidence == 0
    assert region_coverage([fact.model_dump(mode="json")], [spec], 1)[spec.id]["known_hotels"] == 0


@pytest.mark.parametrize("blackout", [True, False])
def test_declared_boolean_observation_is_known_without_implying_availability(specs, blackout):
    spec = next(spec for spec in specs if spec.id == "soundproofing").model_copy(deep=True)
    spec.search.field = "blackout"
    fact = resolve("soundproofing", {"available": None, "blackout": blackout}, [spec])
    assert fact.status == "computed" and fact.confidence == .8 and fact.value["available"] is None
    assert region_coverage([fact.model_dump(mode="json")], [spec], 1)[spec.id]["known_hotels"] == 1


@pytest.mark.parametrize("state", ["missing", "unknown", "stale", "disputed", "expired", "nan"])
def test_primary_coverage_excludes_unusable_facts_even_with_known_siblings(state, specs):
    value = {"available": True, "distance_m": 900}
    if state == "missing":
        value.pop("distance_m")
    elif state == "nan":
        value["distance_m"] = float("nan")
    fact = Fact(hotel_id=HOTEL.id, characteristic_id="green_nearby", value=value, confidence=.8,
                status=state if state in {"unknown", "stale", "disputed"} else "computed",
                valid_until=now()+timedelta(days=-1 if state == "expired" else 30))
    assert region_coverage([fact.model_dump(mode="json")], specs, 1)["green_nearby"]["known_hotels"] == 0


def test_reported_wifi_does_not_cover_download_speed_or_hide_it_from_status(specs):
    repo = ApiRepo()
    fact = Fact(hotel_id="test-hotel", characteristic_id="connectivity", status="computed", confidence=.65,
                value={"available": True, "wlan_reported": True, "download_mbps": None}, valid_until=now()+timedelta(days=30))
    repo.upsert("facts", fact.model_dump(mode="json"))
    report = status(repo)
    assert report["region_coverage"]["connectivity"]["known_hotels"] == 0
    assert "connectivity" in report["no_data_characteristics"]
    # A measured low speed is known coverage even when it fails the default threshold.
    fact.value["download_mbps"] = 10
    coverage = region_coverage([fact.model_dump(mode="json")], specs, 1)
    assert coverage["connectivity"]["status"] == "available"
