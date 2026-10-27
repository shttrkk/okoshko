"""Freeze and evaluate evidence-derived expert-rule weak ranking judgments.

These are NOT human relevance labels or independent hotel accuracy. They test
agreement with an explicit evidence policy on non-synthetic versioned inputs.
Label generation never calls rank_hotels, parse_query, assess_hotel or their
scoring helpers. Missing/stale/disputed evidence remains unjudged (null).

  python -m scripts.ranking_eval --freeze  # refuses to overwrite existing gold
  python -m scripts.ranking_eval --split dev
  python -m scripts.ranking_eval --split holdout

The 20/10 split and grading policy are fixed before measuring scorer changes.
The evaluator passes only query criteria and frozen source-derived facts to the
ranker; judgments are read afterwards. No runtime DB or network is used.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import itertools
import json
import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import yaml

from backend.contracts import CharacteristicSpec, Claim, Fact, Hotel, ParsedQuery
from backend.quality import ndcg

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data/eval/ranking_gold.jsonl"
AS_OF = datetime(2026, 10, 26, 20, 0, tzinfo=UTC)
POLICY = "evidence_rules_v1"
POLICY_TEXT = ("3: all musts have compatible non-synthetic evidence with confidence >=0.6 and numeric margin >=20%; "
               "2: all musts have compatible evidence with confidence >=0.5; "
               "1: known partial fit or individually positive but incompatible room scopes; "
               "0: every must is observed but none matches; null: at least one must lacks current, undisputed, provenance-backed evidence. "
               "Boolean-only strong matches use >=0.6. Numeric margin is an expert preference for a robust fit, not human truth.")
LEDGERS = ["data/seed/open_claims.jsonl.gz", "data/geo/geo_claims.jsonl.gz", "data/eval/website_claims.jsonl"]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def criterion(cid, field="available", op="eq", value=True, **extra):
    return {"characteristic_id": cid, "label": cid, "field": field, "op": op, "value": value, **extra}


def query_cases():
    c = criterion
    # Deliberately explicit criteria isolate ranking from parser performance.
    definitions = [
        ("До пляжа 5 минут", [c("beach_walk", "minutes", "lte", 5)], {}),
        ("До пляжа 10 минут", [c("beach_walk", "minutes", "lte", 10)], {}),
        ("До пляжа 15 минут", [c("beach_walk", "minutes", "lte", 15)], {}),
        ("Обратно с пляжа до 20 минут", [c("beach_walk", "return_minutes", "lte", 20)], {}),
        ("Вид на море", [c("sea_view")], {}),
        ("Закат в сентябре", [c("sunset_view")], {"month": 9}),
        ("Закат в июне", [c("sunset_view")], {"month": 6}),
        ("Закат в декабре", [c("sunset_view")], {"month": 12}),
        ("Вид на горы", [c("mountain_view")], {}),
        ("Тихая сторона", [c("quiet_night")], {}),
        ("В июне вода от 20 градусов", [c("climate", "water_c", "gte", 20)], {"month": 6}),
        ("В октябре вода от 20 градусов", [c("climate", "water_c", "gte", 20)], {"month": 10}),
        ("В сентябре вода от 25 градусов", [c("climate", "water_c", "gte", 25)], {"month": 9}),
        ("Зимой вода от 16 градусов", [c("climate", "water_c", "gte", 16)], {"month": 1}),
        ("Качество воздуха AQI до 40", [c("air_quality", "european_aqi", "lte", 40)], {}),
        ("Качество воздуха AQI до 25", [c("air_quality", "european_aqi", "lte", 25)], {}),
        ("Море и пляж до 10 минут", [c("sea_view"), c("beach_walk", "minutes", "lte", 10)], {}),
        ("Сентябрьский закат и пляж до 10 минут", [c("sunset_view"), c("beach_walk", "minutes", "lte", 10)], {}),
        ("Горы и тихая сторона", [c("mountain_view"), c("quiet_night")], {}),
        ("Море, тишина и пляж до 5 минут", [c("sea_view"), c("quiet_night"), c("beach_walk", "minutes", "lte", 5)], {}),
        ("Пляж до 7 минут без лестниц", [c("beach_walk", "minutes", "lte", 7, no_stairs=True)], {}),
        ("Пляж до 8 минут без переходов", [c("beach_walk", "minutes", "lte", 8, no_crossings=True)], {}),
        ("Море на западе с 5 этажа", [c("sea_view")], {"facade": "W", "min_floor": 5}),
        ("Горы на востоке с 3 этажа", [c("mountain_view")], {"facade": "E", "min_floor": 3}),
        ("Закат в мае с запада", [c("sunset_view")], {"month": 5, "facade": "W"}),
        ("Море и закат в одном номере", [c("sea_view"), c("sunset_view")], {}),
        ("В октябре тихо, закат и пляж до 15 минут", [c("quiet_night"), c("sunset_view"), c("beach_walk", "minutes", "lte", 15)], {"month": 10}),
        ("В августе вода от 25 и пляж до 7 минут", [c("climate", "water_c", "gte", 25), c("beach_walk", "minutes", "lte", 7)], {"month": 8}),
        ("AQI до 20 и пляж до 10 минут", [c("air_quality", "european_aqi", "lte", 20), c("beach_walk", "minutes", "lte", 10)], {}),
        ("Горы и пляж до 15 минут", [c("mountain_view"), c("beach_walk", "minutes", "lte", 15)], {}),
    ]
    return [{"id": f"rank-{i+1:02d}", "split": "dev" if i < 20 else "holdout",
             "parsed": {"text": text, "must": conditions, "month": 9, **extra}}
            for i, (text, conditions, extra) in enumerate(definitions)]


def seed_rows(table):
    with gzip.open(ROOT / "data/seed" / (table+".csv.gz"), "rt", encoding="utf-8") as stream:
        return [json.loads(row["payload"]) for row in csv.DictReader(stream)]


def load_inputs():
    from backend.agents.resolver import resolve_claims
    hotels = [Hotel.model_validate(row) for row in seed_rows("hotels")]
    cids = {c["characteristic_id"] for row in query_cases() for c in row["parsed"]["must"]}
    paths = [*LEDGERS, "data/seed/hotels.csv.gz", "data/seed/sources.csv.gz",
             *("data/characteristics/"+cid+".yaml" for cid in sorted(cids))]
    hashes = {path: hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in paths}
    specs = [CharacteristicSpec.model_validate(yaml.safe_load((ROOT / "data/characteristics" / (cid+".yaml")).read_text())) for cid in sorted(cids)]
    by_hotel = defaultdict(list)
    claims_by_id = {}
    for path in LEDGERS:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(ROOT/path, "rt", encoding="utf-8") as stream:
            for line in stream:
                claim = Claim.model_validate_json(line)
                if claim.synthetic or any(e.kind in {"simulation", "mock"} for e in claim.evidence):
                    continue
                by_hotel[claim.hotel_id].append(claim)
                claims_by_id[claim.id] = claim
    sources = [*seed_rows("sources"), {"id": "hotel_website", "alpha": 5.5, "beta": 4.5}]
    facts = [fact for hotel in hotels for fact in resolve_claims(hotel.id, by_hotel[hotel.id], specs, sources, at=AS_OF, known_floors=hotel.floors)[0]]
    normalized = [{key: value for key, value in f.model_dump(mode="json").items() if key != "updated_at"} for f in facts]
    return hotels, specs, facts, claims_by_id, hashes, digest(sorted(normalized, key=lambda row: row["id"]))


def _field(value, name):
    for part in name.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _scope(fact):
    scope = fact.scope.model_dump(exclude_none=True)
    value = fact.value if isinstance(fact.value, dict) else {}
    if not scope.get("facade") and value.get("facade"):
        scope["facade"] = value["facade"]
    if not scope.get("month") and value.get("month"):
        scope["month"] = value["month"]
    if value.get("available") is True:
        scope["floor_min"] = scope.get("floor_min") or value.get("min_floor")
    return scope


def _usable(fact, claims):
    return (not fact.synthetic and fact.status in {"computed", "confirmed"} and fact.confidence >= .5
            and (fact.valid_until is None or fact.valid_until > AS_OF)
            and fact.evidence and fact.supporting_claim_ids
            and all(cid in claims and not claims[cid].synthetic for cid in fact.supporting_claim_ids)
            and not any(e.kind in {"simulation", "mock"} for e in fact.evidence))


def _compatible(facts, query):
    scopes = [_scope(f) for f in facts]
    sides = {s.get("facade") for s in scopes if s.get("facade")}
    rooms = {s.get("room_type") for s in scopes if s.get("room_type")}
    if query.get("facade"):
        sides.add(query["facade"])
    return (len(sides) <= 1 and len(rooms) <= 1
            and max([s.get("floor_min") or 1 for s in scopes] + [query.get("min_floor") or 1])
            <= min([s.get("floor_max") or 100 for s in scopes]))


def _test(fact, condition):
    value = _field(fact.value, condition["field"])
    target = condition["value"]
    if value is None:
        return None, False
    if condition["op"] in {"lte", "gte"}:
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            return None, False
        passed = value <= target if condition["op"] == "lte" else value >= target
        robust = value <= .8*target if condition["op"] == "lte" else value >= 1.2*target
    else:
        passed, robust = value == target, True
    if condition.get("no_stairs"):
        stairs = _field(fact.value, "has_steps")
        if not isinstance(stairs, bool):
            return None, False
        passed = passed and not stairs
    if condition.get("no_crossings"):
        crossings = _field(fact.value, "crossings")
        if crossings is None or isinstance(crossings, bool):
            return None, False
        passed = passed and not crossings
    return passed, robust


def weak_judgment(facts: list[Fact], query: dict, claims: dict):
    """Independent, deliberately simple label policy; never reads a rank score."""
    observed, positives = [], []
    for condition in query["must"]:
        candidates = []
        passing = []
        for fact in facts:
            if fact.characteristic_id != condition["characteristic_id"] or not _usable(fact, claims):
                continue
            scope = _scope(fact)
            if scope.get("month", query["month"]) != query["month"] or not _compatible([fact], query):
                continue
            passed, robust = _test(fact, condition)
            if passed is None:
                continue
            candidates.append(fact)
            if passed:
                passing.append((fact, robust))
        if not candidates:
            return {"grade": None, "reason": "unjudged_missing_stale_disputed_or_low_confidence", "fact_ids": [], "claim_ids": []}
        observed.extend(candidates)
        positives.append(passing)
    grade, reason, selected = 0, "observed_conditions_do_not_match", observed
    if any(positives):
        grade, reason = 1, "known_partial_fit_or_incompatible_scopes"
    if all(positives):
        for combination in itertools.product(*positives):
            chosen = [item[0] for item in combination]
            if _compatible(chosen, query):
                strong = all(f.confidence >= .6 and robust for f, robust in combination)
                candidate_grade = 3 if strong else 2
                if candidate_grade > grade:
                    grade, selected = candidate_grade, chosen
                    reason = "compatible_robust_fit" if strong else "compatible_preliminary_or_narrow_margin_fit"
    return {"grade": grade, "reason": reason, "fact_ids": sorted({f.id for f in selected}),
            "claim_ids": sorted({cid for f in selected for cid in f.supporting_claim_ids})}


def freeze(destination):
    if destination.exists():
        raise ValueError("Gold already exists; review and version a new policy instead of overwriting held-out labels")
    hotels, _, facts, claims, hashes, facts_hash = load_inputs()
    by_hotel = defaultdict(list)
    for fact in facts:
        by_hotel[fact.hotel_id].append(fact)
    rows = []
    for case in query_cases():
        judgments = {hotel.id: weak_judgment(by_hotel[hotel.id], case["parsed"], claims) for hotel in hotels}
        rows.append({**case, "label_type": "evidence_derived_expert_rule_weak", "human_labeled": False,
                     "policy": POLICY, "policy_text": POLICY_TEXT, "as_of": AS_OF.isoformat(),
                     "input_files_sha256": hashes, "input_facts_sha256": facts_hash,
                     "provenance": "Non-synthetic resolved facts and immutable supporting claims; artifact/URL and observed_at/valid_until remain in the hashed versioned ledgers.",
                     "judgments": judgments})
    destination.write_text("".join(canonical(row)+"\n" for row in rows))
    return {"frozen_queries": len(rows), "dev_queries": 20, "holdout_queries": 10,
            "gold_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "bytes": destination.stat().st_size,
            "label_type": "evidence_derived_expert_rule_weak", "human_labeled": False}


def agreement_at_5(retrieved: list[str], judgments: dict):
    """Do not silently map unknown evidence to a negative relevance label."""
    ids = list(dict.fromkeys(retrieved))[:5]
    grades = {key: value["grade"] for key, value in judgments.items() if value["grade"] is not None}
    unknown = [key for key in ids if key not in grades]
    agreement = ndcg(ids, grades) if not unknown else None
    def gain(grade):
        return 2**grade-1
    known_dcg = sum(gain(grades.get(key, 0))/math.log2(i+2) for i, key in enumerate(ids))
    max_dcg = known_dcg + sum(gain(3)/math.log2(i+2) for i, key in enumerate(ids) if key not in grades)
    ideal_known = sum(gain(g)/math.log2(i+2) for i, g in enumerate(sorted(grades.values(), reverse=True)[:5]))
    ideal_max = sum(gain(g)/math.log2(i+2) for i, g in enumerate(sorted([*grades.values(), *([3]*(len(judgments)-len(grades)))], reverse=True)[:5]))
    return {"ndcg_at_5": agreement, "judged_top5": len(ids)-len(unknown), "top5_count": len(ids),
            "unjudged_top5": unknown, "lower_bound": known_dcg/ideal_max if ideal_max else None,
            "upper_bound": min(1, max_dcg/ideal_known) if ideal_known else 1 if ideal_max else None,
            "query_has_positive_judgments": ideal_known > 0}


def aggregate_cases(cases):
    measured = [row["ndcg_at_5"] for row in cases if row["ndcg_at_5"] is not None]
    bounds = [row for row in cases if row["lower_bound"] is not None]
    return {"queries": len(cases), "queries_with_measured_ndcg": len(measured),
            "queries_without_positive_judgments": sum(not row["query_has_positive_judgments"] for row in cases),
            "ndcg_at_5": sum(measured)/len(measured) if measured else None,
            "top5_judged_share": sum(row["judged_top5"] for row in cases)/sum(row["top5_count"] for row in cases) if cases else None,
            "top10_unique_normalized_names_mean": sum(row["top10_unique_normalized_names"] for row in cases)/len(cases) if cases else None,
            "top10_unique_complexes_mean": sum(row["top10_unique_complexes"] for row in cases)/len(cases) if cases else None,
            "top10_close_geo_pairs_200m_mean": sum(row["top10_close_geo_pairs_200m"] for row in cases)/len(cases) if cases else None,
            "ndcg_at_5_bounds": {"lower": sum(row["lower_bound"] for row in bounds)/len(bounds) if bounds else None,
                                  "upper": sum(row["upper_bound"] for row in bounds)/len(bounds) if bounds else None}}


def normalized_name(name: str) -> str:
    return re.sub(r"[\W_]+", "", name.casefold().replace("ё", "е"))


def distance_m(left, right):
    lat1, lat2 = math.radians(left.lat), math.radians(right.lat)
    dlat, dlon = lat2-lat1, math.radians(right.lon-left.lon)
    angle = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 6_371_000 * 2*math.asin(math.sqrt(min(1, angle)))


def complex_ids(hotels):
    """A conservative name+200m proxy, not verified real property boundaries.

    Complete-link membership prevents a chain of adjacent same-name hotels
    from merging geographically distant properties. Clusters are built once
    in stable ID order, independently of retrieved rankings and relevance.
    """
    groups = defaultdict(list)
    result = {}
    for hotel in sorted(hotels, key=lambda row: row.id):
        name = normalized_name(hotel.name)
        cluster = next((items for items in groups[name] if all(distance_m(hotel, other) <= 200 for other in items)), None)
        if cluster is None:
            cluster = []
            groups[name].append(cluster)
        cluster.append(hotel)
        result[hotel.id] = cluster[0].id
    return result


def evaluate_gold(path, split="all"):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    hotels, specs, facts, _, hashes, facts_hash = load_inputs()
    from backend.agents.ranker import rank_hotels
    if any(row["input_files_sha256"] != hashes for row in rows):
        raise ValueError("Frozen input files changed; do not regenerate labels to fit new rankings. Review a new dataset version.")

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return AS_OF if tz else AS_OF.replace(tzinfo=None)

    cases = []
    complexes = complex_ids(hotels)
    with patch("backend.agents.query_match.datetime", Frozen):
        for row in rows:
            if split != "all" and row["split"] != split:
                continue
            hits = rank_hotels(hotels, facts, ParsedQuery.model_validate(row["parsed"]), specs, explain_limit=5)
            retrieved = [hit.hotel.id for hit in hits[:5]]
            measurement = agreement_at_5(retrieved, row["judgments"])
            cases.append({"id": row["id"], "split": row["split"], "text": row["parsed"]["text"], **measurement,
                          "top5_ids": retrieved, "top5_grades": [row["judgments"][key]["grade"] for key in retrieved],
                          "top5_unique_names": len({hit.hotel.name.casefold() for hit in hits[:5]}),
                          "top10_ids": [hit.hotel.id for hit in hits[:10]],
                          "top10_names": [hit.hotel.name for hit in hits[:10]],
                          "top10_unique_normalized_names": len({normalized_name(hit.hotel.name) for hit in hits[:10]}),
                          "top10_unique_complexes": len({complexes[hit.hotel.id] for hit in hits[:10]}),
                          "top10_close_geo_pairs_200m": sum(distance_m(a.hotel, b.hotel) <= 200
                                                           for a, b in itertools.combinations(hits[:10], 2))})
    return {"label_type": "evidence_derived_expert_rule_weak", "human_labeled": False,
            "metric_meaning": "nDCG@5 agreement with frozen expert-rule evidence labels, NOT human relevance or independent hotel accuracy",
            "split": split, **aggregate_cases(cases),
            "by_split": {name: aggregate_cases([row for row in cases if row["split"] == name]) for name in sorted({row["split"] for row in cases})},
            "input_materialization_matches_frozen": all(row["input_facts_sha256"] == facts_hash for row in rows),
            "scorer_sha256": {path: hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
                              for path in ["backend/agents/ranker.py", "backend/agents/query_match.py",
                                           "backend/catalogue.py", "data/seed/catalogue_reviews.json"]},
            "gold_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "cases": cases,
            "diversity_definition": "Top10 unique normalized names (casefold, ё→е, punctuation/space removed) and complete-link same-name geographic clusters of diameter <=200m; distant namesakes remain separate. This is a complex proxy, not verified property identity.",
            "same_name_nearby_pairs_in_corpus": sum(normalized_name(a.name) == normalized_name(b.name) and distance_m(a, b) <= 200
                                                     for a, b in itertools.combinations(hotels, 2)),
            "diversity_limitation": "Differently named adjacent hotels can share a building or complex. Close-geo pair counts are reported separately; proximity alone is not verified common property identity.",
            "limitations": ["No human labels; source evidence and predictions share a model, so this measures policy agreement only.",
                            "Unknown/stale/disputed/low-confidence pairs are unjudged. Main nDCG excludes queries with any unjudged top5 item; judged share and loose bounds are reported separately.",
                            "The graded ideal is over judged candidates; unknown candidates could change the ideal. Bounds allow their grades to vary from 0 to 3.",
                            "Explicit criteria isolate the ranker and do not evaluate natural-language parsing. Synthetic hotelier/demo facts are excluded."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=GOLD)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--split", choices=["all", "dev", "holdout"], default="all")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = freeze(args.gold) if args.freeze else evaluate_gold(args.gold, args.split)
    text = json.dumps(report, ensure_ascii=False, indent=2)+"\n"
    if args.output:
        args.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
