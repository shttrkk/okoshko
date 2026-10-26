"""Metrics over independent labels. Missing ground truth remains unmeasured."""
from __future__ import annotations

import csv
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


def scope_matches(expected: dict, actual: dict) -> bool:
    return all(actual.get(key) == value for key, value in expected.items() if value is not None and key != "type")


def pick_fact(facts: list[dict], hotel_id: str, characteristic_id: str, scope: dict | None = None):
    candidates = [f for f in facts if f["hotel_id"] == hotel_id and f["characteristic_id"] == characteristic_id
                  and scope_matches(scope or {}, f.get("scope", {})) and f.get("status") not in {"unknown", "stale", "disputed"}]
    return max(candidates, key=lambda item: item.get("confidence", 0), default=None)


def ndcg(retrieved: list[str], relevance: dict[str, int], k: int = 5) -> float | None:
    ideal = sorted(relevance.values(), reverse=True)[:k]
    denominator = sum((2**grade - 1) / math.log2(index+2) for index, grade in enumerate(ideal))
    if denominator == 0:
        return None
    numerator = sum((2**relevance.get(id, 0)-1)/math.log2(index+2) for index, id in enumerate(retrieved[:k]))
    return numerator / denominator


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
        group = [(truth, prob) for _, truth, prob in rows if low <= prob < low+.2 or low == .8 and prob == 1]
        if group:
            mean_prob = sum(p for _, p in group)/len(group)
            positive_rate = sum(t for t, _ in group)/len(group)
            ece += len(group)/n * abs(mean_prob-positive_rate)
            bins.append({"low": low, "high": round(low+.2, 1), "n": len(group), "mean_probability": mean_prob, "positive_rate": positive_rate})
    return {"evaluated": n, "abstentions": abstentions, "accuracy": accuracy, "precision": precision, "recall": recall,
            "f1": 2*precision*recall/(precision+recall) if precision is not None and recall is not None and precision+recall else None,
            "brier": brier, "ece": ece, "calibration": bins}


def evaluate(hotels: list[dict], facts: list[dict], characteristics: list[dict], labels: list[dict], questions: list[dict] | None = None) -> dict:
    # Do not count synthetic hotelier answers as independently verified real facts.
    ids = {h["id"] for h in hotels}
    active = {c["id"] for c in characteristics if c.get("status", "active") == "active"}
    denominator = len(ids)*len(active)
    covered = {(f["hotel_id"], f["characteristic_id"]) for f in facts if f["hotel_id"] in ids and f["characteristic_id"] in active
               and f.get("status") not in {"unknown", "stale", "disputed"} and available(f.get("value")) is not None}
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
            fact = pick_fact(facts, label["hotel_id"], label["characteristic_id"], scope)
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
            subset = [row for row in rows if max(row[2], 1-row[2]) >= threshold]
            thresholds.append({"threshold": threshold, "coverage": len(subset)/len(selected) if selected else 0,
                               "accuracy": sum(pred == truth for pred, truth, _ in subset)/len(subset) if subset else None,
                               "evaluated": len(subset)})
        metrics["risk_coverage"] = thresholds
        groups[label_type] = metrics
    return {
        "generated_at": datetime.now(UTC).isoformat(), "hotel_count": len(hotels), "characteristic_count": len(active),
        "fact_count": len(facts), "label_count": len(labels), "manual_label_count": sum(label.get("label_type", "manual") == "manual" for label in labels),
        "coverage": {"known_pairs": len(covered), "total_pairs": denominator, "ratio": len(covered)/denominator if denominator else 0},
        "question_share": len({(q["hotel_id"], cid) for q in questions or [] for cid in q.get("characteristic_ids", [])})/denominator if denominator else 0,
        "label_groups": groups, "parser_accuracy": None, "ndcg_at_5": None,
        "status": "measured" if groups else "awaiting_independent_labels",
        "limitations": ["Слабые метки с сайтов могут описывать только часть номеров; scope проверяется отдельно.",
                         "Monte Carlo характеризует устойчивость модели, а не точность на независимой разметке.",
                         "Отсутствующие метрики показаны как не измеренные, а не как ноль."],
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
    facts = [Fact.model_validate(row).model_dump(mode="json") for row in repo.all("facts")]
    return evaluate(repo.all("hotels"), facts, repo.all("characteristics"), labels, repo.all("questions"))

