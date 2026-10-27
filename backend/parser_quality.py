"""Portable provenance and fail-closed loading of authored parser measurements.

No parsing, DB access, LLM calls or measurement happens on the read path.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = 1
MEASURED = "measured_against_authored_intent_cases"
METHODOLOGY = (
    "Authored synthetic intent cases, without independent human validation; "
    "not guest logs, hotel factual accuracy or ranking relevance. "
    "The original development and holdout splits are reported separately. "
    "no_data uses explicit fixture coverage; similarity uses the hotel-name fixture."
)
PARSER_FILES = (
    "backend/agents/query_parser.py",
    "backend/agents/group_query.py",
    "backend/agents/query_text.py",
    "backend/agents/query_rules.py",
    "backend/agents/similarity.py",
    "backend/contracts/__init__.py",
    "backend/contracts/models.py",
    "backend/tools/registry.py",
    "backend/parser_quality.py",
    "scripts/parser_eval.py",
)
CORPUS_FILES = {"gold": "eval/queries_gold.jsonl", "negative": "eval/queries_negative.jsonl"}
FIXTURE_FILES = ("seed/hotels.json",)
SUMMARY_FIELDS = (
    "queries",
    "exact_matches",
    "exact_match_rate",
    "criterion_precision",
    "criterion_recall",
    "criterion_true_positive",
    "criterion_expected",
    "criterion_actual",
    "criteria_exact_match_rate",
    "unmapped_expectation_accuracy",
    "unmapped_evaluated_queries",
    "queries_with_unmapped",
)
NEGATIVE_FIELDS = ("queries", "queries_with_false_criteria", "false_criteria_rate", "criteria_emitted")


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _corpus_path(path: Path, data_dir: Path) -> str | None:
    try:
        return path.resolve().relative_to(data_dir.resolve()).as_posix()
    except ValueError:
        # External CLI inputs remain evaluable but cannot attest the public corpus.
        return None


def parser_input_fingerprint(
    data_dir: Path,
    *,
    source_root: Path | None = None,
    gold_path: Path | None = None,
    negative_path: Path | None = None,
    split: str | None = None,
) -> dict:
    """Hash physical input bytes, not Git HEAD, mtimes or a stored path to execute."""
    source_root = source_root or ROOT
    corpora = {
        "gold": gold_path or data_dir / CORPUS_FILES["gold"],
        "negative": negative_path or data_dir / CORPUS_FILES["negative"],
    }
    return {
        "version": REPORT_VERSION,
        "parser_sha256": {name: _sha256(source_root / name) for name in PARSER_FILES},
        "catalogue_sha256": {
            path.relative_to(data_dir).as_posix(): _sha256(path)
            for path in sorted([*(data_dir / "characteristics").glob("*.yaml"),
                                *(data_dir / "landmarks").glob("*.yaml")])
        },
        "corpora": {
            name: {"path": _corpus_path(path, data_dir), "sha256": _sha256(path)}
            for name, path in corpora.items()
        },
        "fixture_sha256": {name: _sha256(data_dir / name) for name in FIXTURE_FILES},
        "selection": {"split": split},
        # The parser's implicit year is an input even if all source bytes match.
        "evaluation_year": date.today().year,
    }


def _unavailable(status: str) -> dict:
    return {"status": status, "human_labeled": False, "synthetic": True, "methodology": METHODOLOGY}


def _count(value) -> bool:
    return type(value) is int and value >= 0


def _rate(value) -> bool:
    return value is None or (type(value) in {int, float} and math.isfinite(value) and 0 <= value <= 1)


def _ratio_matches(value, numerator: int, denominator: int) -> bool:
    if not denominator:
        return value is None
    return value is not None and math.isclose(value, numerator / denominator, rel_tol=0, abs_tol=0.0000005)


def _summary(value) -> dict | None:
    if not isinstance(value, dict) or not set(SUMMARY_FIELDS) <= value.keys():
        return None
    view = {key: value[key] for key in SUMMARY_FIELDS}
    for key in (
        "queries",
        "exact_matches",
        "criterion_true_positive",
        "criterion_expected",
        "criterion_actual",
        "unmapped_evaluated_queries",
        "queries_with_unmapped",
    ):
        if not _count(view[key]):
            return None
    if any(
        not _rate(view[key])
        for key in (
            "exact_match_rate",
            "criterion_precision",
            "criterion_recall",
            "criteria_exact_match_rate",
            "unmapped_expectation_accuracy",
        )
    ):
        return None
    if any(
        view[key] > view["queries"]
        for key in ("exact_matches", "unmapped_evaluated_queries", "queries_with_unmapped")
    ):
        return None
    if view["criterion_true_positive"] > min(view["criterion_expected"], view["criterion_actual"]):
        return None
    for field, numerator, denominator in (
        ("exact_match_rate", "exact_matches", "queries"),
        ("criterion_precision", "criterion_true_positive", "criterion_actual"),
        ("criterion_recall", "criterion_true_positive", "criterion_expected"),
    ):
        if not _ratio_matches(view[field], view[numerator], view[denominator]):
            return None
    if not view["unmapped_evaluated_queries"]:
        view["unmapped_expectation_accuracy"] = None
    return view


def _negative(value) -> dict | None:
    if not isinstance(value, dict) or not set(NEGATIVE_FIELDS) <= value.keys():
        return None
    view = {key: value[key] for key in NEGATIVE_FIELDS}
    if any(not _count(view[key]) for key in NEGATIVE_FIELDS if key != "false_criteria_rate"):
        return None
    if not _rate(view["false_criteria_rate"]) or view["queries_with_false_criteria"] > view["queries"]:
        return None
    if not _ratio_matches(view["false_criteria_rate"], view["queries_with_false_criteria"], view["queries"]):
        return None
    if view["criteria_emitted"] < view["queries_with_false_criteria"]:
        return None
    return view


def parser_intent_report(
    data_dir: Path, *, source_root: Path | None = None, report_path: Path | None = None
) -> dict:
    """Return current summary metrics, or a status without stale numerical data.

    The report cannot choose which files the server hashes. All read paths are
    derived from trusted arguments and fixed manifests, never its JSON paths.
    """
    unavailable = _unavailable("not_measured")
    path = report_path or data_dir / "eval/parser_results.json"
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            return unavailable
        report = json.loads(
            path.read_text(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value))
        )
    except (OSError, UnicodeError, ValueError, RecursionError):
        return unavailable
    if (
        not isinstance(report, dict)
        or report.get("status") != MEASURED
        or report.get("human_labeled") is not False
        or report.get("synthetic") is not True
    ):
        return unavailable
    provenance = report.get("input_fingerprint")
    if (
        not isinstance(provenance, dict)
        or type(provenance.get("version")) is not int
        or provenance["version"] != REPORT_VERSION
    ):
        return unavailable
    if provenance.get("selection") != {"split": None}:
        # A CLI --split diagnostic is not the complete public measurement.
        return unavailable
    if not isinstance(provenance.get("parser_sha256"), dict) or set(provenance["parser_sha256"]) != set(
        PARSER_FILES
    ):
        return unavailable
    if not isinstance(provenance.get("catalogue_sha256"), dict) or not provenance["catalogue_sha256"]:
        return unavailable
    if not isinstance(provenance.get("corpora"), dict) or set(provenance["corpora"]) != set(CORPUS_FILES):
        return unavailable
    current = parser_input_fingerprint(data_dir, source_root=source_root)
    if (
        provenance["parser_sha256"] != current["parser_sha256"]
        or any(v is None for v in current["parser_sha256"].values())
        or provenance.get("evaluation_year") != current["evaluation_year"]
    ):
        return _unavailable("parser_changed_recompute_required")
    if provenance["catalogue_sha256"] != current["catalogue_sha256"]:
        return _unavailable("catalogue_changed_recompute_required")
    if (
        provenance["corpora"] != current["corpora"]
        or provenance.get("fixture_sha256") != current["fixture_sha256"]
        or any(value["sha256"] is None for value in current["corpora"].values())
        or any(value is None for value in current["fixture_sha256"].values())
        or report.get("corpus_sha256")
        != {name: value["sha256"] for name, value in current["corpora"].items()}
    ):
        return _unavailable("corpus_changed_recompute_required")
    gold, negative = _summary(report.get("gold")), _negative(report.get("negative"))
    splits = report.get("splits")
    if (
        gold is None
        or negative is None
        or not gold["queries"]
        or not negative["queries"]
        or not isinstance(splits, dict)
        or set(splits) != {"development", "holdout"}
    ):
        return unavailable
    checked_splits = {name: _summary(value) for name, value in splits.items()}
    if any(value is None for value in checked_splits.values()):
        return unavailable
    if any(
        sum(value[key] for value in checked_splits.values()) != gold[key]
        for key in (
            "queries",
            "exact_matches",
            "criterion_true_positive",
            "criterion_expected",
            "criterion_actual",
        )
    ):
        return unavailable
    return {
        "status": MEASURED,
        "human_labeled": False,
        "synthetic": True,
        "methodology": METHODOLOGY,
        "gold": gold,
        "splits": checked_splits,
        "negative": negative,
        "corpus_sha256": report["corpus_sha256"],
        "input_fingerprint": provenance,
    }
