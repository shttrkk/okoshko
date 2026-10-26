"""Metrics over independent labels. Missing ground truth remains unmeasured."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from backend.contracts import Fact


def available(value):
    if isinstance(value, dict):
        return value.get("available")
    if isinstance(value, bool):
        return value
    return None


def known_value(value) -> bool:
    if isinstance(value, dict):
        if "available" in value:
            return isinstance(value["available"], bool)
        return any(known_value(v) for key, v in value.items()
                   if key not in {"month", "period", "model", "limitations"})
    if isinstance(value, list | tuple):
        return any(known_value(item) for item in value)
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, float):
        return math.isfinite(value)
    return value is not None


def current_value(fact: dict, at: datetime) -> bool:
    if fact.get("status") not in {"computed", "confirmed"} or not known_value(fact.get("value")):
        return False
    deadline = fact.get("valid_until")
    if deadline:
        try:
            parsed = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed <= at:
                return False
        except (ValueError, TypeError):
            return False
    return True


def provenance(facts: list[dict], claims: list[dict] | None = None, *, at: datetime | None = None) -> dict:
    """Count materialized values once. Mixed is a subset of synthetic, not real."""
    at = at or datetime.now(UTC)
    flags = {claim["id"]: bool(claim.get("synthetic")) for claim in claims or []}
    non_synthetic = synthetic = mixed = unknown = 0
    for fact in facts:
        if not current_value(fact, at):
            unknown += 1
            continue
        support = [flags[id] for id in fact.get("supporting_claim_ids", []) if id in flags]
        kinds = {evidence.get("kind") for evidence in fact.get("evidence", [])}
        has_synthetic = bool(fact.get("synthetic") or True in support or kinds & {"simulation", "mock"})
        has_non_synthetic = False in support
        if has_synthetic:
            synthetic += 1
            mixed += bool(has_non_synthetic)
        else:
            non_synthetic += 1
    known = non_synthetic + synthetic
    return {"unit": "materialized_facts", "known": known, "non_synthetic": non_synthetic,
            "synthetic": synthetic, "mixed": mixed, "unknown_stale_disputed": unknown,
            "synthetic_share": synthetic / known if known else None}


def scope_matches(expected: dict, actual: dict) -> bool:
    for key in ("facade","month","room_type"):
        if expected.get(key) is not None and actual.get(key)!=expected[key]:
            return False
    if expected.get("floor_min") is not None or expected.get("floor_max") is not None:
        if actual.get("floor_min") is None:
            return False
        low = expected.get("floor_min") or expected["floor_max"]
        high = expected.get("floor_max") or low
        if actual["floor_min"]>low or (actual.get("floor_max") or actual["floor_min"])<high:
            return False
    return True


def pick_fact(facts: list[dict], hotel_id: str, characteristic_id: str, scope: dict | None = None):
    candidates = [f for f in facts if f["hotel_id"] == hotel_id and f["characteristic_id"] == characteristic_id
                  and scope_matches(scope or {}, f.get("scope", {})) and f.get("status") not in {"unknown", "stale", "disputed"}]
    return max(candidates, key=lambda item: item.get("confidence", 0), default=None)


def ndcg(retrieved: list[str], relevance: dict[str, int], k: int = 5) -> float | None:
    ideal = sorted(relevance.values(), reverse=True)[:k]
    denominator = sum((2**grade - 1) / math.log2(index+2) for index, grade in enumerate(ideal))
    if denominator == 0:
        return None
    # A repeated ID cannot earn the same relevance gain more than once.
    unique = list(dict.fromkeys(retrieved))[:k]
    numerator = sum((2**relevance.get(id, 0)-1)/math.log2(index+2) for index, id in enumerate(unique))
    return numerator / denominator


def ranking_report(data_dir: Path) -> dict:
    """Expose frozen weak-policy agreement separately from independent accuracy."""
    path = data_dir / "eval" / "ranking_results.json"
    gold = data_dir / "eval" / "ranking_gold.jsonl"
    if not path.is_file() or not gold.is_file():
        return {"status": "not_measured", "ndcg_at_5": None, "human_labeled": False}
    report = json.loads(path.read_text())
    if report.get("gold_sha256") != hashlib.sha256(gold.read_bytes()).hexdigest():
        return {"status": "gold_changed_recompute_required", "ndcg_at_5": None, "human_labeled": False}
    for relative, expected in report.get("scorer_sha256", {}).items():
        scorer = data_dir.parent / relative
        if not scorer.is_file() or hashlib.sha256(scorer.read_bytes()).hexdigest() != expected:
            return {"status": "scorer_changed_recompute_required", "ndcg_at_5": None, "human_labeled": False}
    return {"status": "measured_against_frozen_weak_labels", **report}


def classification(rows: list[tuple[bool, bool, float]], abstentions: int = 0):
    if not rows:
        return {"evaluated": 0, "abstentions": abstentions, "accuracy": None, "precision": None, "recall": None,
                "f1": None, "brier": None, "ece": None}
    n = len(rows)
    tp = sum(pred and truth for pred, truth, _ in rows)
    fp = sum(pred and not truth for pred, truth, _ in rows)
    fn = sum(not pred and truth for pred, truth, _ in rows)
    precision = tp/(tp+fp) if tp+fp else None
    recall = tp/(tp+fn) if tp+fn else None
    accuracy = sum(pred == truth for pred, truth, _ in rows)/n
    brier = sum((prob-float(truth))**2 for _, truth, prob in rows)/n
    bins = []
    ece = 0
    for low in [0, .2, .4, .6, .8]:
        group = [(truth, prob) for _, truth, prob in rows if min(int(prob*5),4)==round(low*5)]
        if group:
            mean_prob = sum(p for _, p in group)/len(group)
            positive_rate = sum(t for t, _ in group)/len(group)
            ece += len(group)/n * abs(mean_prob-positive_rate)
            bins.append({"low": low, "high": round(low+.2, 1), "n": len(group), "mean_probability": mean_prob, "positive_rate": positive_rate})
    return {"evaluated": n, "abstentions": abstentions, "accuracy": accuracy, "precision": precision, "recall": recall,
            "f1": 2*precision*recall/(precision+recall) if precision is not None and recall is not None and precision+recall else None,
            "brier": brier, "ece": ece, "calibration": bins}


def evaluate(hotels: list[dict], facts: list[dict], characteristics: list[dict], labels: list[dict], questions: list[dict] | None = None, claims: list[dict] | None = None) -> dict:
    # Do not count synthetic hotelier answers as independently verified real facts.
    ids = {h["id"] for h in hotels}
    hotel_by_id = {hotel["id"]:hotel for hotel in hotels}
    active = {c["id"] for c in characteristics if c.get("status", "active") == "active"}
    denominator = len(ids)*len(active)
    at = datetime.now(UTC)
    covered = {(f["hotel_id"], f["characteristic_id"]) for f in facts if not f.get("synthetic") and f["hotel_id"] in ids and f["characteristic_id"] in active
               and current_value(f, at)}
    groups = {}
    for label_type in sorted({label.get("label_type", "manual") for label in labels}):
        selected = [label for label in labels if label.get("label_type", "manual") == label_type]
        rows = []
        by_characteristic = defaultdict(list)
        abstentions = 0
        for label in selected:
            value = label["value"]
            if isinstance(value, str):
                value = json.loads(value)
            truth = available(value)
            scope = label.get("scope", {})
            if isinstance(scope, str):
                scope = json.loads(scope or "{}")
            if truth is None:
                continue
            prediction_facts = facts
            if label_type=="weak_website":
                # Held-out website quotes must never evaluate themselves through Resolver.
                prediction_facts = [fact for fact in facts if not fact.get("synthetic") and
                                    any(evidence.get("kind")=="calculation" for evidence in fact.get("evidence",[])) and
                                    not any(evidence.get("kind")=="quote" for evidence in fact.get("evidence",[]))]
                # A calculation disputed only by the held-out quote is still the model's prediction;
                # excluding it would turn every disagreement with the label into an abstention.
                prediction_facts = [{**fact, "status": "computed"} if fact.get("status") == "disputed" else fact
                                    for fact in prediction_facts]
            fact = pick_fact(prediction_facts, label["hotel_id"], label["characteristic_id"], scope)
            if label_type=="weak_website" and not scope and label["characteristic_id"] in {"sea_view","sunset_view","mountain_view"}:
                candidates = [candidate for candidate in prediction_facts if candidate["hotel_id"]==label["hotel_id"] and
                              candidate["characteristic_id"]==label["characteristic_id"] and candidate.get("status") not in {"unknown","stale","disputed"}]
                positives = [candidate for candidate in candidates if available(candidate.get("value")) is True]
                if positives:
                    fact = max(positives,key=lambda candidate:candidate.get("confidence",0))
                elif not hotel_by_id.get(label["hotel_id"],{}).get("floors"):
                    # A single modeled ground level cannot refute existence on unknown upper floors.
                    fact = None
            pred = available(fact["value"]) if fact else None
            if pred is None:
                abstentions += 1
                continue
            confidence = fact.get("confidence", 0)
            # Confidence is the stated reliability of the chosen value, not P(positive).
            probability = confidence if pred else 1-confidence
            row = (bool(pred), bool(truth), probability)
            rows.append(row)
            by_characteristic[label["characteristic_id"]].append(row)
        metrics = classification(rows, abstentions)
        metrics.update({"label_count": len(selected), "characteristics": {key: classification(value) for key, value in by_characteristic.items()}})
        thresholds = []
        for threshold in [.5, .6, .7, .8, .9]:
            subset = [row for row in rows if (row[2] if row[0] else 1-row[2]) >= threshold]
            thresholds.append({"threshold": threshold, "coverage": len(subset)/len(selected) if selected else 0,
                               "accuracy": sum(pred == truth for pred, truth, _ in subset)/len(subset) if subset else None,
                               "evaluated": len(subset)})
        metrics["risk_coverage"] = thresholds
        groups[label_type] = metrics
    return {
        "generated_at": datetime.now(UTC).isoformat(), "hotel_count": len(hotels), "characteristic_count": len(active),
        "fact_count": len(facts), "label_count": len(labels), "manual_label_count": sum(label.get("label_type", "manual") == "manual" for label in labels),
        "coverage": {"known_pairs": len(covered), "total_pairs": denominator, "ratio": len(covered)/denominator if denominator else 0},
        "synthetic_fact_count": sum(bool(f.get("synthetic")) for f in facts),
        "question_share": len({(q["hotel_id"], cid) for q in questions or [] for cid in q.get("characteristic_ids", [])})/denominator if denominator else 0,
        "label_groups": groups, "parser_accuracy": None, "ndcg_at_5": None,
        "provenance": provenance(facts, claims, at=at),
        "status": "measured" if groups else "awaiting_independent_labels",
        "limitations": ["Слабые метки с сайтов могут описывать только часть номеров; scope проверяется отдельно.",
                         "Monte Carlo характеризует устойчивость модели, а не точность на независимой разметке.",
                         "Отсутствующие метрики показаны как не измеренные, а не как ноль.",
                         "Доли происхождения считают materialized facts, включая отдельные фасады/месяцы; это не доля отелей и не независимая точность. Mixed входит в synthetic; неизвестные, пустые, устаревшие и спорные значения исключены из знаменателя."],
        "methodology_url": "/data/eval/README.md",
    }


def read_labels(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def from_repository(repo, data_dir: Path) -> dict:
    labels = read_labels(data_dir / "eval" / "labels.csv")
    for row in repo.all("eval_labels"):
        labels.append({**row, "label_type": row.get("label_type", "manual")})
    # Metrics use values, scope, status, supporting IDs, confidence and evidence
    # kinds/quotes/limitations. Cached search snapshots omit only large tool
    # inputs/output, which none of these metric definitions reads.
    rows = repo.search_facts() if hasattr(repo, "search_facts") else repo.all("facts")
    facts = [Fact.model_validate(row).model_dump(mode="json") for row in rows]
    if hasattr(repo, "db_url"):
        # Read only provenance flags, never duplicate large evidence payloads.
        import psycopg
        with psycopg.connect(repo.db_url) as conn:
            claims = [{"id": row[0], "synthetic": row[1]} for row in conn.execute(
                "SELECT id, COALESCE((payload->>'synthetic')::boolean, false) FROM claims")]
    else:
        claims = repo.all("claims")
    result = evaluate(repo.all("hotels"), facts, repo.all("characteristics"), labels, repo.all("questions"), claims)
    result["ranking_weak"] = ranking_report(data_dir)
    return result

