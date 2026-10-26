"""Ranking invariants during deferred explanation and profile scoring."""
from backend.agents.ranker import rank_hotels
from backend.agents.similarity import only_reference_phrase, similar_hotels
from backend.contracts import Criterion, ParsedQuery
from backend.tests.test_search import catalog, fact, hotel  # noqa: F401


def test_deferred_explanations_preserve_scores_order_and_top_page(catalog):  # noqa: F811
    hotels = [hotel(str(i)) for i in range(12)]
    facts = [fact(h.id, "beach_walk", {"available": True, "minutes": 2 + i}, confidence=.9) for i, h in enumerate(hotels)]
    query = ParsedQuery(text="пляж", must=[Criterion(characteristic_id="beach_walk", label="Пляж", field="minutes", op="lte", value=10)])
    full = rank_hotels(hotels, facts, query, catalog)
    page = rank_hotels(hotels, facts, query, catalog, explain_limit=5)
    assert [(h.hotel.id, h.score, h.group) for h in page] == [(h.hotel.id, h.score, h.group) for h in full]
    assert [h.model_dump() for h in page[:5]] == [h.model_dump() for h in full[:5]]
    assert all(not h.verdicts for h in page[5:])
    assert 2 <= len(page[4].why_not_higher) <= 3


def test_similarity_enforces_must_and_uses_nice_without_overriding_it(catalog):  # noqa: F811
    hotels = [hotel(name) for name in ["reference", "eligible", "closer-but-fails", "nice"]]
    facts = []
    for name, minutes, quiet in [("reference", 5, True), ("eligible", 8, True), ("closer-but-fails", 5, False), ("nice", 7, True)]:
        facts.extend([fact(name, "beach_walk", {"minutes": minutes, "available": True}),
                      fact(name, "quiet_night", {"available": quiet}, side="W", low=1, high=5)])
    parsed = ParsedQuery(text="похожий, но тихо", must=[Criterion(characteristic_id="quiet_night", label="Тихо")],
                         nice=[Criterion(characteristic_id="beach_walk", label="Пляж", field="minutes", op="lte", value=7)])
    hits = similar_hotels("reference", hotels, facts, catalog, parsed=parsed)
    assert {h.hotel.id for h in hits} == {"eligible", "nice"}
    assert hits[0].hotel.id == "nice"
    assert all(h.must_satisfied == h.must_total == 1 and h.group == "all" for h in hits)
    assert all(h.similarity_reasons and h.verdicts[0].fact_ids for h in hits)


def test_reference_removal_does_not_hide_unknown_preferences():
    assert only_reference_phrase("похожий на Brevis apartments", "Brevis apartments")
    assert not only_reference_phrase("похожий на Brevis apartments с бассейном", "Brevis apartments")
    assert not only_reference_phrase("бассейн", "Brevis apartments")


def test_numeric_margin_prefers_robust_fit_without_promoting_disputed_data(catalog):  # noqa: F811
    hotels = [hotel(name) for name in ["a-narrow", "z-comfortable", "disputed"]]
    facts = [fact("a-narrow", "beach_walk", {"available": True, "minutes": 9}, confidence=.66),
             fact("z-comfortable", "beach_walk", {"available": True, "minutes": 4}, confidence=.66),
             fact("disputed", "beach_walk", {"available": True, "minutes": 1}, confidence=.99, status="disputed")]
    parsed = ParsedQuery(text="пляж", must=[Criterion(characteristic_id="beach_walk", label="Пляж", field="minutes", op="lte", value=10)])
    hits = rank_hotels(hotels, facts, parsed, catalog)
    assert [hit.hotel.id for hit in hits] == ["z-comfortable", "a-narrow", "disputed"]
    assert [hit.group for hit in hits] == ["all", "all", "unknown"]
    assert hits[-1].verdicts[0].disputed and hits[-1].verdicts[0].status == "unknown"
    assert all(0 <= hit.score <= 100 for hit in hits)
