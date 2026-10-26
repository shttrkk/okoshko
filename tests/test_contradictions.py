"""Known-answer contradictions between an open hotel statement and the geo model."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from backend.agents.answer_parser import parse_answer
from backend.agents.interviewer import ask_contradictions
from backend.agents.query_parser import parse_query
from backend.agents.ranker import rank_hotels
from backend.agents.resolver import CONTRADICTION_PREFIX, resolve_all, resolve_claims, resolve_hotel
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
    facts, conflicts = resolve_claims("h1", claims, catalog, SOURCES, known_floors=5)
    sea = [fact for fact in facts if fact.characteristic_id == "sea_view"]
    assert sea and all(fact.status == "disputed" for fact in sea)
    [conflict] = [c for c in conflicts if c.id.startswith(CONTRADICTION_PREFIX)]
    assert conflict.kind == "value" and conflict.status == "open" and len(conflict.claim_ids) == 5
    assert "Сайт отеля: «Из окон открываются виды на море»" in conflict.resolution_note
    assert "ни с одного из 4 фасадов на этажах 1–5" in conflict.resolution_note
    assert not any(c.kind == "scope" and c.characteristic_id == "sea_view" for c in conflicts)


def facade_model(cid="sea_view", *, intervals=((1, 5),), months=(None,)):
    return [geo(side, cid=cid, scope=Scope(type="floor_range", facade=side, floor_min=low, floor_max=high, month=month))
            .model_copy(update={"id": f"model-{cid}-{side}-{low}-{high}-{month}"})
            for side in ("N", "E", "S", "W") for low, high in intervals for month in months]


@pytest.mark.parametrize("cid", ["sea_view", "mountain_view"])
@pytest.mark.parametrize("known_floors,intervals", [(None, ((1, 1),)), (5, ((1, 1),)), (5, ((1, 2), (4, 5)))],
                         ids=["unknown-upper-floors", "unmodeled-upper-floors", "interior-gap"])
def test_incomplete_floor_domain_qualifies_the_statement_without_disputing_it(catalog, cid, known_floors, intervals):
    claims = [website(cid), *facade_model(cid, intervals=intervals)]
    facts, conflicts = resolve_claims("h1", claims, catalog, SOURCES, known_floors=known_floors)
    assert not any(c.id.startswith(CONTRADICTION_PREFIX) for c in conflicts)
    assert not any(f.status == "disputed" for f in facts)
    assert any(c.kind == "scope" and c.status == "auto_resolved" for c in conflicts)


@pytest.mark.parametrize("cid", ["sea_view", "mountain_view"])
def test_adjacent_floor_intervals_cover_the_known_domain(catalog, cid):
    claims = [website(cid), *facade_model(cid, intervals=((1, 2), (3, 5)))]
    facts, conflicts = resolve_claims("h1", claims, catalog, SOURCES, known_floors=5)
    assert len([c for c in conflicts if c.id.startswith(CONTRADICTION_PREFIX)]) == 1
    assert all(f.status == "disputed" for f in facts if f.characteristic_id == cid)


@pytest.mark.parametrize("incomplete", ["one-facade", "room-type", "implicit-floors"])
def test_every_modeled_facade_needs_explicit_coverage_of_the_whole_floor_domain(catalog, incomplete):
    model = facade_model()
    last = model[-1]
    updates = {"floor_max": 4} if incomplete == "one-facade" else {"room_type": "suite"} if incomplete == "room-type" else {"floor_max": None}
    model[-1] = last.model_copy(update={"scope": last.scope.model_copy(update=updates)})
    facts, conflicts = resolve_claims("h1", [website(), *model], catalog, SOURCES, known_floors=5)
    assert not any(c.id.startswith(CONTRADICTION_PREFIX) for c in conflicts)
    assert not any(f.status == "disputed" for f in facts)


@pytest.mark.parametrize("months,expected", [((None,), False), (tuple(range(1, 12)), False), (tuple(range(1, 13)), True)],
                         ids=["month-unspecified", "missing-december", "full-year"])
def test_general_sunset_claim_requires_all_months(catalog, months, expected):
    claims = [website("sunset_view"), *facade_model("sunset_view", months=months)]
    facts, conflicts = resolve_claims("h1", claims, catalog, SOURCES, known_floors=5)
    assert any(c.id.startswith(CONTRADICTION_PREFIX) for c in conflicts) is expected
    assert any(f.status == "disputed" for f in facts) is expected


def test_sunset_coverage_cannot_be_completed_by_other_facades_or_other_months(catalog):
    model = facade_model("sunset_view", months=tuple(range(1, 13)))
    last = model[-1]
    model[-1] = last.model_copy(update={"scope": last.scope.model_copy(update={"floor_max": 4})})
    _, conflicts = resolve_claims("h1", [website("sunset_view"), *model], catalog, SOURCES, known_floors=5)
    assert not any(c.id.startswith(CONTRADICTION_PREFIX) for c in conflicts)


def test_unqualified_positive_model_cannot_be_ignored_when_refuting_a_general_sunset(catalog):
    claims = [website("sunset_view"), *facade_model("sunset_view", months=tuple(range(1, 13))),
              geo("W", cid="sunset_view", available=True)]
    _, conflicts = resolve_claims("h1", claims, catalog, SOURCES, known_floors=5)
    assert not any(c.id.startswith(CONTRADICTION_PREFIX) for c in conflicts)


def test_month_specific_sunset_statement_uses_its_own_month(catalog):
    statement = website("sunset_view").model_copy(update={"scope": Scope(month=9)})
    model = facade_model("sunset_view", months=(9,))
    august = geo("W", cid="sunset_view", available=True, scope=Scope(type="floor_range", facade="W", floor_min=1, floor_max=5, month=8))
    facts, conflicts = resolve_claims("h1", [statement, *model, august], catalog, SOURCES, known_floors=5)
    [conflict] = [c for c in conflicts if c.id.startswith(CONTRADICTION_PREFIX)]
    assert august.id not in conflict.claim_ids
    assert next(f for f in facts if august.id in f.supporting_claim_ids).status == "computed"
    _, conflicts = resolve_claims("h1", [statement, august], catalog, SOURCES, known_floors=5)
    assert not any(c.id.startswith(CONTRADICTION_PREFIX) for c in conflicts)


@pytest.mark.parametrize("resolve", [resolve_hotel, resolve_all], ids=["single-hotel", "all-hotels"])
@pytest.mark.parametrize("known_floors", [None, 5], ids=["unknown-floors", "known-floors"])
def test_repository_resolution_passes_hotel_floor_metadata(resolve, known_floors):
    repo = ApiRepo()
    repo.insert("hotels", {"id": "h1", "name": "Synthetic", "lat": 43.58, "lon": 39.72, "floors": known_floors})
    for claim in [website(), *facade_model()]:
        repo.add_claim(claim)
    if resolve is resolve_hotel:
        resolve("h1", repo)
    else:
        resolve(repo)
    disputes = [c for c in repo.find("conflicts", hotel_id="h1") if c["id"].startswith(CONTRADICTION_PREFIX)]
    assert bool(disputes) == (known_floors is not None)


@pytest.mark.parametrize("claims", [
    [website(), geo("N"), geo("E"), geo("S"), geo("W", confidence=.55)],
    [website(), geo("N"), geo("E")],
    [website(), geo("N"), geo("E"), geo("S", available=True), geo("W")],
], ids=["weak-facade", "too-few-facades", "one-facade-sees-the-sea"])
def test_scope_qualification_is_not_a_contradiction(claims, catalog):
    facts, conflicts = resolve_claims("h1", claims, catalog, SOURCES, known_floors=5)
    assert not any(c.id.startswith(CONTRADICTION_PREFIX) for c in conflicts)
    assert not any(f.status == "disputed" for f in facts)


def test_hotelier_answer_supersedes_the_contradiction(catalog):
    answer = geo("SW", available=True, confidence=.85).model_copy(update={"id": "hotelier-sea", "source": "hotelier_simulator", "synthetic": True})
    claims = [website(), *(geo(side) for side in ("N", "E", "S", "W")), answer]
    _, conflicts = resolve_claims("h1", claims, catalog, SOURCES, known_floors=5)
    assert not any(c.id.startswith(CONTRADICTION_PREFIX) for c in conflicts)


def test_quoted_beach_distance_far_below_the_route_is_a_contradiction(catalog):
    quote = {"available": True, "quoted_distance": {"meters": 300, "relation": "stated", "target": "beach"}}
    route = {"available": True, "minutes": 12.5, "distance_m": 1042.4, "has_steps": False}
    claims = [website("beach_walk", quote, "в 300 метрах от пляжа"), geo("route", cid="beach_walk", value=route, confidence=.66, scope=Scope())]
    facts, conflicts = resolve_claims("h1", claims, catalog, SOURCES, known_floors=5)
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
    facts, conflicts = resolve_claims("h1", claims, catalog, SOURCES, known_floors=5)
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
    disputed, _ = resolve_claims("h1", claims, catalog, SOURCES, known_floors=5)
    plain, _ = resolve_claims("h1", claims[1:], catalog, SOURCES, known_floors=5)
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
