"""Relative wording describes verdicts without changing ranking semantics."""
from datetime import timedelta

from backend.agents.ranker import rank_hotels, relative_reason
from backend.contracts import (
    Criterion,
    CriterionVerdict,
    Evidence,
    Fact,
    Hotel,
    ParsedQuery,
    RoomPlanItem,
    SearchHit,
    now,
)


def verdict(state, *, required=True, cid="pets"):
    return CriterionVerdict(characteristic_id=cid, label=cid, required=required,
                            status=state, symbol={"yes": "✓", "partial": "≈", "unknown": "?", "no": "✗"}[state],
                            confidence=.65 if state == "partial" else .9 if state == "yes" else 0,
                            text="Known-answer fixture")


def hit(identifier, *, satisfied=0, score=50, verdicts=(), plan=()):
    return SearchHit(hotel=Hotel(id=identifier, name=identifier, lat=43.58, lon=39.72),
                     score=score, group="almost", must_satisfied=satisfied, must_total=5,
                     verdicts=list(verdicts), room_plan=list(plan))


def test_confirmed_counts_required_yes_only_in_shared_and_room_plan_verdicts():
    lower = hit("lower", satisfied=3, verdicts=[verdict("partial"), verdict("yes", required=False)],
                plan=[RoomPlanItem(label="first", verdicts=[verdict("yes"), verdict("unknown")])])
    higher = hit("higher", satisfied=5, verdicts=[verdict("yes"), verdict("yes", required=False)],
                 plan=[RoomPlanItem(label="first", verdicts=[verdict("yes"), verdict("partial")]),
                       RoomPlanItem(label="second", verdicts=[verdict("unknown")])])
    assert relative_reason(lower, higher) == "«higher»: подтверждено 2 из 5 обязательных условий, здесь — 1."


def test_supported_partial_is_preliminary_and_never_a_confirmed_count():
    lower = hit("lower", verdicts=[verdict("unknown")])
    higher = hit("higher", satisfied=5, score=70, verdicts=[verdict("partial")])
    reason = relative_reason(lower, higher)
    assert "есть предварительные данные" in reason
    assert "подтвержден" not in reason


def test_unknown_and_optional_yes_do_not_count_as_confirmed_and_score_wording_is_plain():
    lower = hit("lower", verdicts=[verdict("unknown"), verdict("yes", required=False)])
    higher = hit("higher", satisfied=5, score=70, verdicts=[verdict("unknown"), verdict("yes", required=False)])
    reason = relative_reason(lower, higher)
    assert reason == "«higher»: по условиям у него данные надёжнее; оценка 70 против 50. Проверьте оговорки по условиям."
    assert "подтвержден" not in reason


def test_room_plan_comparison_pairs_verdicts_by_room_instead_of_crossing_guests():
    lower = hit("lower", plan=[RoomPlanItem(label="first", verdicts=[verdict("partial")]),
                               RoomPlanItem(label="second", verdicts=[verdict("unknown")])])
    higher = hit("higher", score=70, plan=[RoomPlanItem(label="first", verdicts=[verdict("unknown")]),
                                          RoomPlanItem(label="second", verdicts=[verdict("partial")])])
    assert "есть предварительные данные; здесь ?" in relative_reason(lower, higher)


def test_rank_order_scores_groups_and_supported_counts_stay_unchanged():
    hotels = [Hotel(id=identifier, name=identifier, lat=43.58, lon=39.72, floors=5)
              for identifier in ("unknown", "partial", "yes", "weak")]
    facts = [Fact(id=h.id + "-fact", hotel_id=h.id, characteristic_id="pets",
                  value={"available": True}, confidence=confidence, status="computed",
                  valid_until=now() + timedelta(days=1), supporting_claim_ids=[h.id + "-claim"],
                  evidence=[Evidence(id=h.id + "-evidence", kind="calculation", label="Fixture",
                                     source_ids=["fixture"], artifact="/tests/rank.json")])
             for h, confidence in zip(hotels[1:], (.65, .9, .4), strict=True)]
    query = ParsedQuery(text="fixture", must=[Criterion(characteristic_id="pets", label="pets", field="available")])
    results = rank_hotels(hotels, facts, query, [])
    def summary(rows):
        return [(r.hotel.id, r.score, r.group, r.must_satisfied, r.must_total) for r in rows]
    assert summary(results) == [("yes", 96, "all", 1, 1), ("partial", 61, "all", 1, 1),
                                ("weak", 51, "almost", 0, 1), ("unknown", 20, "unknown", 0, 1)]
    assert summary(rank_hotels(list(reversed(hotels)), list(reversed(facts)), query, [], explain_limit=1)) == summary(results)
