"""The shipped synthetic new-building scenario shows было/стало and a passport alert on first start."""
import gzip
import json
from pathlib import Path

import yaml

from backend.agents.freshness import change_id_for, seed_change_event
from backend.agents.resolver import resolve_hotel
from backend.contracts import CharacteristicSpec, Claim
from backend.tests.test_api_integration import ApiRepo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DOCUMENT = json.loads((DATA / "seed/demo_change_event.json").read_text())
MASHA = {row["hotel_id"] for row in json.loads((DATA / "eval/persona_acceptance.json").read_text())["synthetic_hotels"]}


def stand(hotel_id):
    repo = ApiRepo()
    repo.insert("hotels", next(row for row in json.loads((DATA / "seed/hotels.json").read_text()) if row["id"] == hotel_id))
    for path in sorted((DATA / "characteristics").glob("*.yaml")):
        repo.upsert("characteristics", CharacteristicSpec.model_validate(yaml.safe_load(path.read_text())).model_dump(mode="json"))
    for row in json.loads((DATA / "seed/sources.json").read_text()):
        repo.upsert("sources", row)
    with gzip.open(DATA / "geo/geo_claims.jsonl.gz", "rt") as stream:
        for line in stream:
            if f'"hotel_id": "{hotel_id}"' in line:
                repo.add_claim(Claim.model_validate_json(line))
    resolve_hotel(hotel_id, repo)
    return repo


def test_shipped_scenario_is_synthetic_reproducible_and_outside_the_masha_set():
    assert DOCUMENT["synthetic"] is True and DOCUMENT["schema_version"] == 1
    assert DOCUMENT["hotel_id"] not in MASHA | {"osm-node-10058556917"}
    assert (DATA / "geo" / Path(DOCUMENT["geo"]["artifact"]).name).is_file()
    assert all(claim["synthetic"] and claim["evidence"][0]["kind"] == "simulation" for claim in DOCUMENT["claims"])
    assert DOCUMENT["min_floor_sea"]["before"] != DOCUMENT["min_floor_sea"]["after"]


def test_seed_change_event_records_before_after_and_notifies_the_demo_passport(tmp_path):
    hotel_id = DOCUMENT["hotel_id"]
    repo = stand(hotel_id)
    event = seed_change_event(repo, DATA, runtime=tmp_path)
    assert event.id == change_id_for(hotel_id) and event.synthetic and event.payload["seeded"] is True
    assert event.title.startswith("Демо: новый 16-этажный дом")
    sea_before = {f.scope.facade for f in event.before if f.characteristic_id == "sea_view" and f.value.get("available")}
    sea_after = {f.scope.facade for f in event.after if f.characteristic_id == "sea_view" and f.value.get("available")}
    assert "NW" in sea_before and "NW" not in sea_after
    assert any(f.synthetic for f in event.after if f.characteristic_id == "sea_view")
    [passport] = repo.find("expectation_passports", hotel_id=hotel_id)
    assert passport["guest_name"].endswith("(синтетический паспорт)") and passport["status"] == "at_risk"
    assert event.notifications and event.notifications[0]["passport_id"] == passport["id"]
    assert seed_change_event(repo, DATA, runtime=tmp_path) is None  # applied once
