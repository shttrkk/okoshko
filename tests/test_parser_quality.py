"""Freshness, portability and honest unknown metrics for authored intent reports."""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from backend import parser_quality as pq
from scripts import parser_eval

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def measured(tmp_path):
    source = tmp_path / "source"
    data = source / "data"
    for name in pq.PARSER_FILES:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (data / "eval").mkdir(parents=True)
    (data / "seed").mkdir()
    (data / "characteristics").mkdir()
    trait = ROOT / "data/characteristics/quiet_night.yaml"
    shutil.copyfile(trait, data / "characteristics/quiet_night.yaml")
    (data / "seed/hotels.json").write_text("[]\n")
    condition = {"characteristic_id": "quiet_night", "field": "available", "op": "eq", "value": True}
    gold = [
        {
            "id": split,
            "split": split,
            "text": "Нужен тихий номер",
            "expected": {"must": [condition], "nice": [], "no_data": [], "unmapped": None},
        }
        for split in ["development", "holdout"]
    ]
    negative = [
        {
            "id": "negative",
            "split": "negative",
            "text": "Подпись к картине «Вид на море»",
            "expected": {"must": [], "nice": [], "no_data": [], "unmapped": None},
        }
    ]
    for name, rows in [("gold", gold), ("negative", negative)]:
        (data / pq.CORPUS_FILES[name]).write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        )
    specs = parser_eval.catalogue(source)
    report = parser_eval.evaluate(gold, negative, specs, [])
    fingerprint = pq.parser_input_fingerprint(data, source_root=source)
    report.update(
        {
            "status": pq.MEASURED,
            "human_labeled": False,
            "synthetic": True,
            "input_fingerprint": fingerprint,
            "corpus_sha256": {name: v["sha256"] for name, v in fingerprint["corpora"].items()},
        }
    )
    path = data / "eval/parser_results.json"
    parser_eval.write_report(path, report)
    return source, data, path, report


def read(measured):
    source, data, _, _ = measured
    return pq.parser_intent_report(data, source_root=source)


def assert_unavailable(value, status):
    assert value["status"] == status and value["human_labeled"] is False
    assert not {"gold", "splits", "negative", "failures", "criterion_precision"} & value.keys()


def test_fresh_report_exposes_authored_summary_and_no_failure_texts(measured):
    value = read(measured)
    assert value["status"] == pq.MEASURED and value["human_labeled"] is False and value["synthetic"] is True
    assert value["gold"]["queries"] == 2 and value["gold"]["exact_matches"] == 2
    assert set(value["splits"]) == {"development", "holdout"}
    assert value["negative"]["false_criteria_rate"] == 0
    assert "without independent human validation" in value["methodology"]
    assert "failures" not in value


@pytest.mark.parametrize("relative", pq.PARSER_FILES)
def test_any_actual_parser_scorer_or_contract_change_hides_all_old_metrics(measured, relative):
    source, _, _, _ = measured
    with (source / relative).open("a") as file:
        file.write("\n# changed measured input\n")
    assert_unavailable(read(measured), "parser_changed_recompute_required")


def test_missing_source_and_implicit_year_change_require_recomputation(measured, monkeypatch):
    monkeypatch.setattr(pq, "date", type("NextYear", (), {"today": staticmethod(lambda: date(2099, 1, 1))}))
    assert_unavailable(read(measured), "parser_changed_recompute_required")
    monkeypatch.undo()
    (measured[0] / pq.PARSER_FILES[0]).unlink()
    assert_unavailable(read(measured), "parser_changed_recompute_required")


@pytest.mark.parametrize("change", ["edit", "add", "delete"])
def test_catalogue_membership_and_yaml_bytes_are_part_of_measurement(measured, change):
    _, data, _, _ = measured
    path = data / "characteristics/quiet_night.yaml"
    if change == "edit":
        path.write_text(path.read_text() + "\n# changed YAML\n")
    elif change == "add":
        shutil.copyfile(path, path.with_name("another.yaml"))
    else:
        path.unlink()
    assert_unavailable(read(measured), "catalogue_changed_recompute_required")


@pytest.mark.parametrize("relative", [*pq.CORPUS_FILES.values(), *pq.FIXTURE_FILES])
@pytest.mark.parametrize("change", ["edit", "delete"])
def test_corpora_and_hotel_name_fixture_change_require_recomputation(measured, relative, change):
    _, data, _, _ = measured
    path = data / relative
    if change == "edit":
        path.write_text(path.read_text() + "\n")
    else:
        path.unlink()
    assert_unavailable(read(measured), "corpus_changed_recompute_required")


@pytest.mark.parametrize("contents", ["{broken", "[]", "null", '{"gold":{"exact_match_rate":1}}'])
def test_missing_malformed_and_legacy_artifacts_remain_unmeasured(measured, contents):
    measured[2].write_text(contents)
    assert_unavailable(read(measured), "not_measured")
    measured[2].unlink()
    assert_unavailable(read(measured), "not_measured")


def test_report_cannot_select_a_path_outside_the_trusted_input_manifest(measured, monkeypatch):
    source, _, path, report = measured
    tampered = copy.deepcopy(report)
    tampered["input_fingerprint"]["corpora"]["gold"]["path"] = "../../private.txt"
    parser_eval.write_report(path, tampered)
    original = pq._sha256

    def confined(file):
        assert file.resolve().is_relative_to(source.resolve())
        return original(file)

    monkeypatch.setattr(pq, "_sha256", confined)
    assert_unavailable(read(measured), "corpus_changed_recompute_required")


@pytest.mark.parametrize(
    "field,value", [("human_labeled", True), ("synthetic", False), ("status", "measured")]
)
def test_report_does_not_upgrade_authored_examples_to_real_guest_accuracy(measured, field, value):
    report = copy.deepcopy(measured[3])
    report[field] = value
    parser_eval.write_report(measured[2], report)
    assert_unavailable(read(measured), "not_measured")


@pytest.mark.parametrize(
    "change", ["empty_parser", "unknown_file", "version", "split", "bad_count", "bad_rate", "nan"]
)
def test_incomplete_or_invalid_attestation_does_not_publish_metrics(measured, change):
    report = copy.deepcopy(measured[3])
    if change == "empty_parser":
        report["input_fingerprint"]["parser_sha256"] = {}
    if change == "unknown_file":
        report["input_fingerprint"]["parser_sha256"]["../../private.txt"] = "0" * 64
    if change == "version":
        report["input_fingerprint"]["version"] = 999
    if change == "split":
        report["input_fingerprint"]["selection"] = {"split": "holdout"}
    if change == "bad_count":
        report["gold"]["queries"] = True
    if change == "bad_rate":
        report["gold"]["criterion_precision"] = 1.01
    if change == "nan":
        report["gold"]["criterion_precision"] = float("nan")
    if change == "nan":
        measured[2].write_text(json.dumps(report))
    else:
        parser_eval.write_report(measured[2], report)
    assert_unavailable(read(measured), "not_measured")


def test_unmapped_with_null_labels_is_na_even_if_an_old_field_says_perfect(measured):
    report = copy.deepcopy(measured[3])
    assert report["gold"]["unmapped_evaluated_queries"] == 0
    report["gold"]["unmapped_expectation_accuracy"] = 1
    parser_eval.write_report(measured[2], report)
    value = read(measured)
    assert value["status"] == pq.MEASURED and value["gold"]["unmapped_expectation_accuracy"] is None
    assert json.loads(measured[2].read_text())["gold"]["unmapped_expectation_accuracy"] == 1


def test_finite_but_inconsistent_rates_do_not_publish_false_precision(measured):
    report = copy.deepcopy(measured[3])
    report["gold"]["criterion_precision"] = 0.5
    parser_eval.write_report(measured[2], report)
    assert_unavailable(read(measured), "not_measured")


def test_report_is_portable_to_another_worktree_without_absolute_paths(measured, tmp_path):
    source, _, _, _ = measured
    target = tmp_path / "relocated"
    shutil.copytree(source, target)
    result = pq.parser_intent_report(target / "data", source_root=target)
    assert result["status"] == pq.MEASURED
    assert str(source) not in json.dumps(result["input_fingerprint"])


def test_producer_is_deterministic_and_default_report_is_loadable_without_touching_eval_data(tmp_path):
    gold, negative = [ROOT / "data" / pq.CORPUS_FILES[key] for key in ["gold", "negative"]]
    first = parser_eval.produce_report(gold, negative)
    second = parser_eval.produce_report(gold, negative)
    first_file, second_file = tmp_path / "one.json", tmp_path / "two.json"
    parser_eval.write_report(first_file, first)
    parser_eval.write_report(second_file, second)
    assert first_file.read_bytes() == second_file.read_bytes()
    loaded = pq.parser_intent_report(ROOT / "data", report_path=first_file)
    assert loaded["status"] == pq.MEASURED and loaded["gold"] == first["gold"]


def test_report_bytes_are_reproducible_across_process_hash_seeds(tmp_path):
    paths = []
    for seed in ["11", "37"]:
        path = tmp_path / f"seed-{seed}.json"
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/parser_eval.py"), "--output", str(path)],
            cwd=ROOT,
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=True,
            stdout=subprocess.DEVNULL,
        )
        paths.append(path)
    assert hashlib.sha256(paths[0].read_bytes()).digest() == hashlib.sha256(paths[1].read_bytes()).digest()


def test_cli_preserves_custom_inputs_split_output_and_failures(measured, tmp_path):
    _, data, _, _ = measured
    output = tmp_path / "custom.json"
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/parser_eval.py"),
            "--gold",
            str(data / pq.CORPUS_FILES["gold"]),
            "--negative",
            str(data / pq.CORPUS_FILES["negative"]),
            "--split",
            "holdout",
            "--output",
            str(output),
            "--failures",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(process.stdout)
    assert report == json.loads(output.read_text())
    assert report["gold"]["queries"] == 1 and set(report["splits"]) == {"holdout"} and "failures" in report
    assert_unavailable(pq.parser_intent_report(data, report_path=output), "not_measured")


def test_inputs_changing_during_measurement_cannot_be_published(measured, monkeypatch):
    _, data, path, _ = measured
    original = parser_eval.evaluate

    def racing(*args, **kwargs):
        result = original(*args, **kwargs)
        negative = data / pq.CORPUS_FILES["negative"]
        negative.write_text(negative.read_text() + "\n")
        return result

    before = path.read_bytes()
    monkeypatch.setattr(parser_eval, "evaluate", racing)
    with pytest.raises(RuntimeError, match="inputs changed"):
        parser_eval.produce_report(data / pq.CORPUS_FILES["gold"], data / pq.CORPUS_FILES["negative"])
    assert path.read_bytes() == before


def test_atomic_publication_failure_keeps_old_artifact_and_removes_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    path.write_text('{"old":true}')

    def rejected(*args):
        raise OSError("test publication interrupted")

    monkeypatch.setattr(parser_eval.os, "replace", rejected)
    with pytest.raises(OSError):
        parser_eval.write_report(path, {"new": True})
    assert path.read_text() == '{"old":true}' and list(tmp_path.iterdir()) == [path]


def test_new_landmark_catalogue_invalidates_old_parser_measurement(measured):
    _, data, _, _ = measured
    directory = data / 'landmarks'
    directory.mkdir()
    (directory / 'sochi.yaml').write_text('landmarks: []\n')
    assert_unavailable(read(measured), 'catalogue_changed_recompute_required')
