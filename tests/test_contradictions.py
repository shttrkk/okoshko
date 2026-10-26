"""Known-answer contradictions between an open hotel statement and the geo model."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from backend.agents.answer_parser import parse_answer
from backend.agents.interviewer import ask_contradictions
from backend.agents.query_parser import parse_query
from backend.agents.ranker import rank_hotels
from backend.agents.resolver import CONTRADICTION_PREFIX, resolve_claims
from backend.contracts import CharacteristicSpec, Claim, Evidence, Hotel, Question, Scope, SearchRequest
from backend.quality import evaluate
from backend.tests.test_api_integration import ApiRepo

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(UTC)
SOURCES = [{"id": "geo_engine", "alpha": 7, "beta": 3}, {"id": "hotel_website", "alpha": 5.5, "beta": 4.5}]


@pytest.fixture
def catalog():
    return [CharacteristicSpec.model_validate(yaml.safe_load(path.read_text()))
            for path in sorted((ROOT / "data/characteristics").glob("*.yaml"))]


def website(cid="sea_view", value=None, quote="Из окон открываются виды на море"):
    return Claim(id=f"website-{cid}", hotel_id="h1", characteristic_id=cid, value=value or {"available": True},
                 source="hotel_website", method="exact_quote_v1", confidence=.55, observed_at=NOW, valid_until=NOW + timedelta(days=180),
                 evidence=[Evidence(kind="quote", label="Цитата с сайта", source_ids=["hotel_website"], quote=quote, url="https://example.test")])


def geo(facade, available=False, confidence=.8, cid="sea_view", value=None, scope=None):
    return Claim(id=f"geo-{cid}-{facade}", hotel_id="h1", characteristic_id=cid,
                 scope=scope or Scope(type="floor_range", facade=facade, floor_min=1, floor_max=5),
                 value=value or {"available": available, "facade": facade}, source="geo_engine", method="two_scale_dsm_los_mc_v1",
                 confidence=confidence, observed_at=NOW, valid_until=NOW + timedelta(days=90),
                 evidence=[Evidence(kind="calculation", label="Георасчёт", source_ids=["geo_engine"], artifact="/data/geo/h1.json")])


def test_hotel_wide_yes_against_all_negative_facades_is_disputed_and_explained(catalog):
    claims = [website(), *(geo(side) for side in ("N", "E", "S", "W"))]
    facts, conflicts = resolve_claims("h1", claims, catalog, SOURCES)
    sea = [fact for fact in facts if fact.characteristic_id == "sea_view"]
    assert sea and all(fact.status == "disputed" for fact in sea)
    [conflict] = [c for c in conflicts if c.id.startswith(CONTRADICTION_PREFIX)]
    assert conflict.kind == "value" and conflict.status == "open" and len(conflict.claim_ids) == 5
    assert "Сайт отеля: «Из окон открываются виды на море»" in conflict.resolution_note
    assert "ни с одного из 4 фасадов на этажах 1–5" in conflict.resolution_note
    assert not any(c.kind == "scope" and c.characteristic_id == "sea_view" for c in conflicts)


@pytest.mark.parametrize("claims", [
    [website(), geo("N"), geo("E"), geo("S"), geo("W", confidence=.55)],
    [website(), geo("N"), geo("E")],
    [website(), geo("N"), geo("E"), geo("S", available=True), geo("W")],
], ids=["weak-facade", "too-few-facades", "one-facade-sees-the-sea"])
def test_scope_qualification_is_not_a_contradiction(claims, catalog):
    facts, conflicts = resolve_claims("h1", claims, catalog, SOURCES)
    assert not any(c.id.startswith(CONTRADICTION_PREFIX) for c in conflicts)
    assert not any(f.status == "disputed" for f in facts)


def test_hotelier_answer_supersedes_the_contradiction(catalog):
    answer = geo("SW", available=True, confidence=.85).model_copy(update={"id": "hotelier-sea", "source": "hotelier_simulator", "synthetic": True})
    claims = [website(), *(geo(side) for side in ("N", "E", "S", "W")), answer]
    _, conflicts = resolve_claims("h1", claims, catalog, SOURCES)
    assert not any(c.id.startswith(CONTRADICTION_PREFIX) for c in conflicts)


def test_quoted_beach_distance_far_below_the_route_is_a_contradiction(catalog):
    quote = {"available": True, "quoted_distance": {"meters": 300, "relation": "stated", "target": "beach"}}
    route = {"available": True, "minutes": 12.5, "distance_m": 1042.4, "has_steps": False}
    claims = [website("beach_walk", quote, "в 300 метрах от пляжа"), geo("route", cid="beach_walk", value=route, confidence=.66, scope=Scope())]
    facts, conflicts = resolve_claims("h1", claims, catalog, SOURCES)
    [conflict] = [c for c in conflicts if c.id.startswith(CONTRADICTION_PREFIX)]
    assert "1042 м, ≈ 13 мин" in conflict.resolution_note and "в 300 метрах от пляжа" in conflict.resolution_note
    assert all(f.status == "disputed" for f in facts if f.characteristic_id == "beach_walk")
    close = {**route, "distance_m": 392.5, "minutes": 5}
    _, conflicts = resolve_claims("h1", [claims[0], geo("route", cid="beach_walk", value=close, confidence=.66, scope=Scope())], catalog, SOURCES)
    assert not any(c.id.startswith(CONTRADICTION_PREFIX) for c in conflicts)


def test_contradiction_creates_one_conflict_question_and_search_never_confirms(catalog):
    repo = ApiRepo()
    claims = [website(), *(geo(side) for side in ("N", "E", "S", "W"))]
    for claim in claims:
        repo.add_claim(claim)
    facts, conflicts = resolve_claims("h1", claims, catalog, SOURCES)
    asked = ask_contradictions(repo, conflicts)
    again = ask_contradictions(repo, conflicts)
    [conflict] = [c for c in asked if c.id.startswith(CONTRADICTION_PREFIX)]
    assert conflict.status == "asked" and conflict.question_id == next(c.question_id for c in again if c.id == conflict.id)
    [question] = repo.find("questions", hotel_id="h1")
    assert question["reason"] == "conflict" and question["characteristic_ids"] == ["sea_view"]
    assert question["hypothesis"] == conflict.resolution_note and question["evidence_refs"]
    parsed = parse_query(SearchRequest(text="номер с видом на море"), catalog)
    [hit] = rank_hotels([Hotel(id="h1", name="Synthetic", lat=43.58, lon=39.72)], facts, parsed, catalog)
    assert hit.group != "all" and hit.verdicts[0].status == "unknown" and hit.verdicts[0].disputed


def test_disputing_by_the_held_out_quote_does_not_change_label_accuracy(catalog):
    claims = [website(), *(geo(side) for side in ("N", "E", "S", "W"))]
    disputed, _ = resolve_claims("h1", claims, catalog, SOURCES)
    plain, _ = resolve_claims("h1", claims[1:], catalog, SOURCES)
    label = {"hotel_id": "h1", "characteristic_id": "sea_view", "scope": {}, "value": {"available": True}, "label_type": "weak_website"}
    hotels = [{"id": "h1", "floors": 5}]
    specs = [spec.model_dump() for spec in catalog]
    def metrics(facts):
        return evaluate(hotels, [f.model_dump(mode="json") for f in facts], specs, [label])["label_groups"]["weak_website"]
    assert metrics(disputed)["evaluated"] == metrics(plain)["evaluated"] == 1
    assert metrics(disputed)["accuracy"] == metrics(plain)["accuracy"] == 0


def test_beach_conflict_answer_is_parsed_with_a_quote():
    question = Question(hotel_id="h1", characteristic_ids=["beach_walk"], text="?", reason="conflict")
    answer = parse_answer(question, "До пляжа 6 минут пешком через подземный переход, лестниц нет.", synthetic=True)
    [update] = answer.updates
    assert update.value == {"available": True, "minutes": 6.0, "has_steps": False} and update.quote in answer.raw_text
    assert parse_answer(question, "Не знаю точно, может минут 10 до пляжа.", synthetic=True).unknown_fields == ["beach_walk"]
