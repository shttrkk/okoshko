"""Shipped evidence must still be valid when the stand is reviewed.

Time-travel checks run the real resolver and ranker on the versioned seed
ledgers with an injected "now"; no database or network is used.
"""
from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

import backend.agents.query_match as query_match
from backend.agents.query_parser import parse_query
from backend.agents.ranker import rank_hotels
from backend.agents.resolver import resolve_claims
from backend.contracts import CharacteristicSpec, Claim, Hotel, SearchRequest
from geo.service import geo_ttl_days
from pipeline.export_seed import ttl_days
from pipeline.retime_claims import retime

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REVIEW_HORIZON = datetime(2026, 12, 1, tzinfo=UTC)
LEDGERS = [DATA / "seed/open_claims.jsonl.gz", DATA / "geo/geo_claims.jsonl.gz",
           DATA / "eval/website_claims.jsonl", DATA / "seed/demo_hotelier_claims.jsonl"]
ACCEPTANCE = json.loads((DATA / "eval/persona_acceptance.json").read_text())


def _rows(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        yield from (json.loads(line) for line in stream if line.strip())


@pytest.fixture(scope="module")
def shipped_claims() -> list[dict]:
    rows = [row for path in LEDGERS for row in _rows(path)]
    rows.extend(json.loads((DATA / "seed/demo_hotelier_dialogues.json").read_text())["claims"])
    return rows


@pytest.fixture(scope="module")
def catalog():
    return [CharacteristicSpec.model_validate(yaml.safe_load(path.read_text()))
            for path in sorted((DATA / "characteristics").glob("*.yaml"))]


def test_no_shipped_claim_expires_before_the_review_window(shipped_claims):
    early = sorted({(row["characteristic_id"], row["valid_until"][:10]) for row in shipped_claims
                    if datetime.fromisoformat(row["valid_until"].replace("Z", "+00:00")) < REVIEW_HORIZON})
    assert not early, early


def test_shipped_ledgers_follow_the_ttl_policy():
    assert retime(DATA, apply=False) == {"open": {}, "geo": {}, "artifacts": 0}
    for characteristic_id in ("sea_view", "sunset_view", "mountain_view", "quiet_night", "beach_walk"):
        assert 60 <= geo_ttl_days(characteristic_id) <= ttl_days(characteristic_id)
    assert ttl_days("air_quality") == 90


def _sources():
    rows = json.loads((DATA / "seed/sources.json").read_text())
    # Priors the seed loader registers for the two text sources.
    return [*rows, {"id": "hotelier_simulator", "alpha": 6, "beta": 4}, {"id": "hotel_website", "alpha": 5.5, "beta": 4.5}]


@pytest.mark.parametrize("now", [datetime(2026, 10, 30, 12, tzinfo=UTC), datetime(2026, 11, 30, 12, tzinfo=UTC)], ids=["30.10", "30.11"])
def test_masha_and_air_quality_survive_until(now, shipped_claims, catalog, monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(query_match, "datetime", Frozen)
    hotel_ids = [row["hotel_id"] for row in ACCEPTANCE["synthetic_hotels"]]
    hotels = {row["id"]: row for row in json.loads((DATA / "seed/hotels.json").read_text())}
    facts = []
    for hotel_id in hotel_ids:
        claims = [Claim.model_validate(row) for row in shipped_claims if row["hotel_id"] == hotel_id]
        facts.extend(resolve_claims(hotel_id, claims, catalog, _sources(), at=now)[0])
    parsed = parse_query(SearchRequest(text=ACCEPTANCE["masha"]["text"]), catalog)
    hits = rank_hotels([Hotel.model_validate(hotels[hotel_id]) for hotel_id in hotel_ids], facts, parsed, catalog)
    assert [hit.group for hit in hits] == ["all"] * len(hotel_ids), [(hit.hotel.id, hit.group, [v.text for v in hit.verdicts]) for hit in hits]

    air = [Claim.model_validate(row) for row in shipped_claims if row["characteristic_id"] == "air_quality"][:5]
    for claim in air:
        [fact] = [f for f in resolve_claims(claim.hotel_id, [claim], catalog, _sources(), at=now)[0] if f.characteristic_id == "air_quality"]
        assert fact.status == "computed", (claim.hotel_id, fact.status)
