"""Run only supplemental Sochi TZ stages in the pinned pipeline environment.

No baseline acquisition, geo rebuild, seed loading or database operation is
available here. Selected stages may update TZ artifacts and fetch their licensed
source inputs. Partial runs require existing prerequisite artifacts; use
--stage all for the complete supplemental sequence. --dry-run only prints a plan.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGION = ROOT / "pipeline/regions/sochi.yaml"
STAGES = (
    "fetch_osm_extra",
    "fetch_tz_areas",
    "fetch_tz_osm_metadata",
    "fetch_tz_fisht",
    "fetch_tz_places",
    "fetch_tz_climate",
    "fetch_tz_osm_facts",
    "fetch_tz_construction",
    "fetch_tz_routes",
    "fetch_tz_history",
    "fetch_tz_landmarks",
    "fetch_tz_transfer_diagnostics",
    "fetch_tz_location",
    "fetch_tz_metadata",
    "fetch_tz_supplements",
    "fetch_tz_catalog",
    "fetch_tz_manifest",
)


def select_stages(requested):
    """Only fixed module names; repeats run once, in the declared sequence."""
    requested = set(requested)
    if not requested or requested - {*STAGES, "all"}:
        raise ValueError("Select only explicit TZ stages or all")
    if "all" in requested:
        if len(requested) != 1:
            raise ValueError("--stage all cannot be combined with individual stages")
        return STAGES
    return tuple(stage for stage in STAGES if stage in requested)


def stage_plan(stages):
    # Validate again for callers that do not use argparse.
    return [{"stage": stage, "module": "pipeline." + stage} for stage in select_stages(stages)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", action="append", required=True, choices=("all", *STAGES),
                        help="Select all or repeat for individual stages; fixed order, no automatic prerequisites")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without imports, subprocesses, downloads or writes")
    args = parser.parse_args(argv)
    try:
        plan = stage_plan(args.stage)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({"mode": "dry-run" if args.dry_run else "tz-supplemental",
                      "region": str(REGION.relative_to(ROOT)), "baseline_rebuild": False,
                      "prerequisites": "Existing baseline seed/geo/specs; partial runs do not add prerequisite stages",
                      "stages": plan}, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return
    environment = {**os.environ, "REGION": str(REGION)}
    for item in plan:
        print("Starting " + item["stage"], flush=True)
        try:
            subprocess.run([sys.executable, "-m", item["module"]], cwd=ROOT, env=environment, check=True)
        except subprocess.CalledProcessError as error:
            parser.exit(error.returncode, f"TZ stage failed: {item['stage']}\n")


if __name__ == "__main__":
    main()
