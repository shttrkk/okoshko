"""Time the existing rebuild DAG without changing source formats or stages.

Run inside the pipeline image with only an isolated experimental data bind:
python -m scripts.region_probe --timings runtime/timings.json -- --region ...
The remaining arguments are passed to pipeline.rebuild unchanged. Each stage
record is persisted even if a later stage fails. RSS is a process lifetime high
water mark, not additional memory allocated by an individual stage.
"""
from __future__ import annotations

import argparse
import importlib
import json
import platform
import resource
import time
from datetime import UTC, datetime
from pathlib import Path

from pipeline import rebuild


def now():
    return datetime.now(UTC).isoformat()


def usage():
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    rss_scale = 1 if platform.system() == "Darwin" else 1024
    return {"cpu_user_s": own.ru_utime + children.ru_utime,
            "cpu_system_s": own.ru_stime + children.ru_stime,
            "process_peak_rss_bytes": int(own.ru_maxrss * rss_scale),
            "completed_child_peak_rss_bytes": int(children.ru_maxrss * rss_scale)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timings", type=Path, required=True)
    parser.add_argument("rebuild_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.rebuild_args
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    report = {"started_at": now(), "argv": arguments, "platform": platform.platform(),
              "rss_scope": "lifetime high water mark of this process and completed children; not per-stage allocation", "stages": []}
    args.timings.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.timings.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    original = rebuild.execute_stage

    def timed(name, stage_args, config):
        before, started = usage(), time.perf_counter()
        record = {"stage": name, "started_at": now(), "status": "running"}
        report["stages"].append(record)
        save()
        compute_module = None
        if name == "compute_views" and stage_args.workers == 1:
            compute_module = importlib.import_module("pipeline.compute_views")
            original_one, original_region = compute_module._compute_one, compute_module.Region
            record["hotels"] = []

            def timed_region(*positional, **keywords):
                init_start = time.perf_counter()
                region = original_region(*positional, **keywords)
                record["region_load_s"] = round(time.perf_counter() - init_start, 6)
                return region

            def timed_one(hotel):
                hotel_start = time.perf_counter()
                result = original_one(hotel)
                record["hotels"].append({"hotel_id": hotel["id"],
                                         "elapsed_s": round(time.perf_counter() - hotel_start, 6),
                                         "claims": len(result[1]["claims"]),
                                         "facades": len(result[1]["geo"]["facades"])})
                save()
                return result

            compute_module.Region, compute_module._compute_one = timed_region, timed_one
        try:
            original(name, stage_args, config)
            record["status"] = "ok"
        except BaseException as error:
            record.update(status="failed", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            if compute_module is not None:
                compute_module._compute_one, compute_module.Region = original_one, original_region
            after = usage()
            record.update(ended_at=now(), elapsed_s=round(time.perf_counter() - started, 6),
                          cpu_user_s=round(after["cpu_user_s"] - before["cpu_user_s"], 6),
                          cpu_system_s=round(after["cpu_system_s"] - before["cpu_system_s"], 6),
                          process_peak_rss_bytes=after["process_peak_rss_bytes"],
                          completed_child_peak_rss_bytes=after["completed_child_peak_rss_bytes"])
            save()

    rebuild.execute_stage = timed
    try:
        rebuild.main(arguments)
    finally:
        rebuild.execute_stage = original
        report["ended_at"] = now()
        save()


if __name__ == "__main__":
    main()
