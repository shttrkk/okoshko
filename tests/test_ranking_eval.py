"""Known-answer tests of weak-label policy and honest missing-label metrics."""
import hashlib
import json
from collections import Counter
from datetime import timedelta

import pytest

from backend.contracts import Claim, Evidence, Fact, Hotel
from backend.quality import ndcg, ranking_report
from scripts.ranking_eval import (
    AS_OF,
    GOLD,
    agreement_at_5,
    complex_ids,
    criterion,
    freeze,
    normalized_name,
    weak_judgment,
)


def input_fact(cid, value, *, side=None, status="computed", synthetic=False, confidence=.66):
    evidence = Evidence(id="ev-"+cid, kind="calculation", label="Synthetic known-answer policy test", source_ids=["test_fixture"],
                        artifact="/data/eval/README.md")
    claim = Claim(id="claim-"+cid, hotel_id="test", characteristic_id=cid, value=value,
                  source="test_fixture", method="known_answer_test", evidence=[evidence], confidence=confidence,
                  observed_at=AS_OF-timedelta(days=1), valid_until=AS_OF+timedelta(days=1), synthetic=synthetic)
    fact = Fact(id="fact-"+cid, hotel_id="test", characteristic_id=cid, scope={"facade": side}, value=value,
                status=status, confidence=confidence, evidence=[evidence], supporting_claim_ids=[claim.id],
                valid_until=claim.valid_until, synthetic=synthetic)
    return fact, claim


@pytest.mark.parametrize("minutes,grade", [(5, 3), (9, 2), (12, 0)])
def test_numeric_grades_are_predeclared_evidence_rules(minutes, grade):
    fact, claim = input_fact("beach_walk", {"minutes": minutes})
    result = weak_judgment([fact], {"month": 9, "must": [criterion("beach_walk", "minutes", "lte", 10)]}, {claim.id: claim})
    assert result["grade"] == grade
    assert result["fact_ids"] == [fact.id] and result["claim_ids"] == [claim.id]


@pytest.mark.parametrize("status,synthetic,confidence", [("disputed", False, .9), ("stale", False, .9), ("computed", True, .9), ("computed", False, .3)])
def test_unusable_evidence_stays_unjudged(status, synthetic, confidence):
    fact, claim = input_fact("sea_view", {"available": True}, status=status, synthetic=synthetic, confidence=confidence)
    assert weak_judgment([fact], {"month": 9, "must": [criterion("sea_view")]}, {claim.id: claim})["grade"] is None


def test_individually_matching_incompatible_rooms_are_not_fully_relevant():
    sea, sea_claim = input_fact("sea_view", {"available": True}, side="W")
    quiet, quiet_claim = input_fact("quiet_night", {"available": True}, side="E")
    result = weak_judgment([sea, quiet], {"month": 9, "must": [criterion("sea_view"), criterion("quiet_night")]},
                           {sea_claim.id: sea_claim, quiet_claim.id: quiet_claim})
    assert result["grade"] == 1


def test_unknown_is_not_replaced_by_a_negative_or_perfect_agreement():
    report = agreement_at_5(["unknown", "known"], {"unknown": {"grade": None}, "known": {"grade": 3}})
    assert report["ndcg_at_5"] is None and report["judged_top5"] == 1
    assert 0 <= report["lower_bound"] <= report["upper_bound"] <= 1
    assert ndcg(["a"]*5, {"a": 3}) == 1


def test_gold_is_frozen_weak_provenanced_and_has_disjoint_holdout():
    rows = [json.loads(line) for line in GOLD.read_text().splitlines()]
    assert len(rows) >= 30 and Counter(row["split"] for row in rows) == {"dev": 20, "holdout": 10}
    assert len({row["id"] for row in rows}) == len(rows)
    grades = set()
    for row in rows:
        assert row["label_type"] == "evidence_derived_expert_rule_weak" and row["human_labeled"] is False
        assert row["input_files_sha256"] and row["input_facts_sha256"]
        for judgment in row["judgments"].values():
            grades.add(judgment["grade"])
            if judgment["grade"] is not None:
                assert judgment["fact_ids"] and judgment["claim_ids"]
    assert grades == {None, 0, 1, 2, 3}


def test_freeze_never_silently_relabels_existing_gold(tmp_path):
    path = tmp_path / "gold.jsonl"
    path.write_text("frozen")
    with pytest.raises(ValueError, match="Gold already exists"):
        freeze(path)
    assert path.read_text() == "frozen"


def test_quality_hides_metrics_when_frozen_gold_changes(tmp_path):
    directory = tmp_path / "eval"
    directory.mkdir()
    gold = directory / "ranking_gold.jsonl"
    gold.write_text("frozen")
    report = {"gold_sha256": hashlib.sha256(gold.read_bytes()).hexdigest(), "ndcg_at_5": .8, "human_labeled": False}
    (directory / "ranking_results.json").write_text(json.dumps(report))
    assert ranking_report(tmp_path)["ndcg_at_5"] == .8
    gold.write_text("changed")
    assert ranking_report(tmp_path)["ndcg_at_5"] is None


def test_diversity_proxy_keeps_distant_namesakes_and_stops_transitive_chains():
    def hotel(id, name, north_m):
        return Hotel(id=id, name=name, lat=43.5+north_m/111_195, lon=39.7)
    hotels = [hotel("a", "Grand Way", 0), hotel("b", "GRAND-WAY", 150), hotel("c", "Grand Way", 300),
              hotel("d", "Grand Way", 2000), hotel("e", "Other Hotel", 0)]
    clusters = complex_ids(hotels)
    assert normalized_name(" Grand-Way ") == normalized_name("GRAND WAY")
    assert clusters["a"] == clusters["b"]
    assert clusters["c"] != clusters["a"] and clusters["d"] != clusters["a"]
    assert clusters["e"] != clusters["a"]
    assert complex_ids(list(reversed(hotels))) == clusters
