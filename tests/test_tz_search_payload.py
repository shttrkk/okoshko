"""Small search cards keep decisions while source details remain in lazy proof."""
import pytest
from fastapi import HTTPException

from backend.api.catalog import bounded_search_page, search_explanations
from backend.contracts import (
    CriterionVerdict,
    Hotel,
    ParsedQuery,
    RoomClause,
    RoomProfile,
    SearchHit,
    SearchResult,
)
from backend.db.repository import search_fact


def test_source_limits_deferred_without_changing_ranking_decision_or_provenance():
    limitation = "Copernicus DSM has approximate heights; graph connectors are unverified"
    verdict = CriterionVerdict(characteristic_id="quiet_night", label="Тишина", status="partial", symbol="≈",
                               text="Окна на запад", confidence=.55, fact_ids=["fact"], evidence_refs=["evidence"],
                               caveat="Лучше уточнить окна у отеля; " + limitation)
    profile = RoomProfile(id="p", scope={"facade": "W", "floor_min": 5}, title="Запад, 5-й этаж",
                          description="По расчёту здесь тише.", clauses=[RoomClause(text="Тише по расчёту",
                          characteristic_id="quiet_night", status="partial", fact_ids=["fact"], evidence_refs=["evidence"])],
                          caveats=[limitation, "Наличие свободного номера уточнит отель"])
    hit = SearchHit(hotel=Hotel(id="h", name="Known-answer hotel", lat=43.5, lon=39.7), score=75.5,
                    group="all", verdicts=[verdict], room_profile=profile)
    facts = [{"hotel_id": "h", "evidence": [{"id": "evidence", "limitations": [limitation]}]}]
    [result] = search_explanations([hit], facts)
    assert (result.score, result.group, result.verdicts[0].status, result.verdicts[0].confidence) == (75.5, "all", "partial", .55)
    assert result.verdicts[0].fact_ids == ["fact"] and result.verdicts[0].evidence_refs == ["evidence"]
    assert result.verdicts[0].caveat == "Лучше уточнить окна у отеля"
    assert result.room_profile.clauses == profile.clauses
    assert result.room_profile.caveats == ["Наличие свободного номера уточнит отель"]
    assert hit.verdicts[0].caveat.endswith(limitation) and profile.caveats[0] == limitation
    assert facts[0]["evidence"][0]["limitations"] == [limitation]


def test_unrecognized_warning_and_empty_search_remain_intact():
    assert search_explanations([], []) == []
    verdict = CriterionVerdict(characteristic_id="airport_noise", label="Аэропорт", status="unknown", symbol="?",
                               text="Слышимость самолётов не измерена", confidence=0,
                               caveat="Частота и ночные рейсы неизвестны")
    hit = SearchHit(hotel=Hotel(id="h", name="Hotel", lat=43.5, lon=39.7), score=0, group="unknown", verdicts=[verdict])
    [result] = search_explanations([hit], [])
    assert result.verdicts == [verdict] and result.room_profile is None


def test_search_projection_defers_source_limitations_without_changing_stored_proof():
    original = {"id": "fact", "hotel_id": "h", "value": {"available": None, "unknown_reason": "no route"},
                "evidence": [{"id": "e", "kind": "calculation", "source_ids": ["source"],
                              "artifact": "/data/seed/test.json", "quote": None,
                              "inputs": {"facade_azimuth_deg": 270, "large": [1, 2]}, "output": {"raw": [1, 2]},
                              "limitations": ["Full source-method detail"]}]}
    compact = search_fact(original)
    assert compact['value'] == original['value']
    assert compact['evidence'][0]['id'] == 'e' and compact['evidence'][0]['source_ids'] == ['source']
    assert compact['evidence'][0]['inputs'] == {'facade_azimuth_deg': 270}
    assert 'limitations' not in compact['evidence'][0]
    assert original['evidence'][0]['limitations'] == ['Full source-method detail']


def test_search_fetches_airport_risk_for_quiet_requests_only():
    from fastapi.testclient import TestClient

    from backend.db.repository import get_repo
    from backend.main import app
    from backend.tests.test_api_integration import ApiRepo

    repo = ApiRepo()
    requested = []

    def snapshot(characteristic_ids=None, month=None):
        requested.append(characteristic_ids)
        return []

    repo.search_facts = snapshot
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        with TestClient(app) as client:
            assert client.post('/api/search', json={'text': 'тихий номер'}).status_code == 200
            assert 'airport_noise' in requested[-1]
            assert client.post('/api/search', json={'text': 'бары рядом'}).status_code == 200
            assert 'airport_noise' not in requested[-1]
    finally:
        app.dependency_overrides.clear()


def test_page_budget_keeps_complete_ranked_prefix_and_true_total():
    hits = [SearchHit(hotel=Hotel(id=str(i), name='Hotel', lat=43.5, lon=39.7), score=100-i,
                      group='all', summary='Known-answer payload ' * 100) for i in range(5)]
    result = SearchResult(parsed=ParsedQuery(text='test'), results=hits, total=247, latency_ms=3)
    envelope = len(result.model_copy(update={'results': []}).model_dump_json().encode())
    budget = envelope + sum(len(hit.model_dump_json().encode()) for hit in hits[:3]) + 2
    limited = bounded_search_page(result, budget)
    assert [hit.hotel.id for hit in limited.results] == ['0', '1', '2']
    assert limited.total == 247 and limited.results == result.results[:3]
    assert len(limited.model_dump_json().encode()) <= budget
    assert len(result.results) == 5
    assert bounded_search_page(result, 100_000).results == hits


def test_oversized_single_card_fails_explicitly_instead_of_losing_its_evidence():
    result = SearchResult(parsed=ParsedQuery(text='test'), results=[SearchHit(
        hotel=Hotel(id='h', name='Hotel', lat=43.5, lon=39.7), score=10, group='all', summary='x' * 1000)],
        total=1, latency_ms=1)
    with pytest.raises(HTTPException) as error:
        bounded_search_page(result, 500)
    assert error.value.status_code == 413
