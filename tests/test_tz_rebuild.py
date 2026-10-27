"""The supplemental CLI cannot dispatch baseline or arbitrary modules."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from pipeline import tz_rebuild


def test_full_tz_plan_is_explicit_and_excludes_baseline_rebuild():
    plan = tz_rebuild.stage_plan(["all"])
    assert len(plan) == 17
    assert [item["stage"] for item in plan] == list(tz_rebuild.STAGES)
    assert plan[0]["module"] == "pipeline.fetch_osm_extra"
    assert plan[-1]["module"] == "pipeline.fetch_tz_manifest"
    assert not {"fetch_osm", "fetch_dem", "export_seed", "compute_views", "rebuild", "run"}.intersection(tz_rebuild.STAGES)


@pytest.mark.parametrize("stage", ["fetch_osm", "rebuild", "backend.seed", "pipeline.fetch_tz_routes", "../fetch_tz_routes", "fetch_tz_routes;echo injected"])
def test_unlisted_stage_is_rejected_before_execution(stage, monkeypatch):
    monkeypatch.setattr(tz_rebuild.subprocess, "run", lambda *args, **kwargs: pytest.fail("Stage executed"))
    with pytest.raises(SystemExit) as error:
        tz_rebuild.main(["--stage", stage])
    assert error.value.code == 2
    with pytest.raises(ValueError):
        tz_rebuild.stage_plan([stage])


def test_dry_run_works_without_data_or_pipeline_dependencies(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REGION", "unrelated-region.yaml")
    monkeypatch.setattr(tz_rebuild.subprocess, "run", lambda *args, **kwargs: pytest.fail("Subprocess in dry-run"))
    tz_rebuild.main(["--stage", "all", "--dry-run"])
    plan = json.loads(capsys.readouterr().out)
    assert plan["mode"] == "dry-run" and plan["baseline_rebuild"] is False
    assert plan["region"] == "pipeline/regions/sochi.yaml"
    assert os.environ["REGION"] == "unrelated-region.yaml"
    assert list(tmp_path.iterdir()) == []


def test_selected_stages_run_once_in_fixed_order_with_fixed_region(monkeypatch, capsys):
    calls = []
    monkeypatch.setenv("REGION", "unrelated-region.yaml")
    monkeypatch.setattr(tz_rebuild.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    tz_rebuild.main(["--stage", "fetch_tz_manifest", "--stage", "fetch_tz_routes", "--stage", "fetch_tz_routes"])
    capsys.readouterr()
    assert [call[0] for call in calls] == [[sys.executable, "-m", "pipeline.fetch_tz_routes"],
                                         [sys.executable, "-m", "pipeline.fetch_tz_manifest"]]
    assert all(options["cwd"] == tz_rebuild.ROOT and options["check"] is True for _, options in calls)
    assert all(options["env"]["REGION"] == str(tz_rebuild.REGION) for _, options in calls)
    assert os.environ["REGION"] == "unrelated-region.yaml"


def test_failed_stage_stops_later_dispatch(monkeypatch, capsys):
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(7, command)

    monkeypatch.setattr(tz_rebuild.subprocess, "run", fail)
    with pytest.raises(SystemExit) as error:
        tz_rebuild.main(["--stage", "fetch_tz_catalog", "--stage", "fetch_tz_manifest"])
    assert error.value.code == 7 and len(calls) == 1
    capsys.readouterr()


def test_all_cannot_be_mixed_with_individual_stages():
    with pytest.raises(ValueError):
        tz_rebuild.select_stages(["all", "fetch_tz_manifest"])
    with pytest.raises(ValueError):
        tz_rebuild.select_stages([])
