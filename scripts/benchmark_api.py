"""Serial HTTP benchmark of public APIs; search writes normal search traces only.

Run against a ready stand, with no rebuild or concurrent load:
  python scripts/benchmark_api.py --base-url http://localhost:8080 --samples 10
Reports wall-clock HTTP latency, response bytes, cold samples and warm p50/p95.
Warm search deliberately includes the repeated-query cache. Full and compact
profiles are measured separately; no passport/answer/collect/change is created.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

QUERIES = ["тихий номер с видом на закат недалеко от моря", "вид на море с западной стороны не ниже 7 этажа",
           "рядом с пляжем и парковкой", "в гуще событий, бары и ночная жизнь", "похожий на Brevis apartments"]
DEFAULT_IDS = ["osm-way-201474330", "overture-037ee676-5776-46bd-a4ac-38c828f2a99d", "osm-node-11866202895"]


def summary(samples):
    times = sorted(row["ms"] for row in samples)
    return {"samples": len(times), "p50_ms": round(statistics.median(times), 2),
            "p95_ms": round(times[math.ceil(.95*len(times))-1], 2),
            "min_ms": round(times[0], 2), "max_ms": round(times[-1], 2),
            "bytes_max": max(row["bytes"] for row in samples)}


def request(base, path, body=None):
    encoded = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(base+path, data=encoded, headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
    return {"ms": (time.perf_counter()-started)*1000, "bytes": len(raw)}, json.loads(raw)


def benchmark(base, *, samples=10, warmups=1, ids=DEFAULT_IDS):
    _, status = request(base, "/api/status")
    report = {"base_url": base, "status": status, "method": "serial urllib HTTP; no gzip requested; nearest-rank p95",
              "search_cache": "Warm samples repeat each query after warmups; normal per-request trace/log remains enabled.",
              "samples_per_case": samples, "warmups_per_case": warmups, "search": [], "profiles": {}}
    search_samples = []
    for text in QUERIES:
        body = {"text": text, "limit": 20}
        cold, result = request(base, "/api/search", body)
        for _ in range(warmups):
            request(base, "/api/search", body)
        values = [request(base, "/api/search", body)[0] for _ in range(samples)]
        search_samples.extend(values)
        report["search"].append({"text": text, "first_sample": cold, "warm": summary(values),
                                 "all_in_top20": sum(hit["group"] == "all" for hit in result["results"])})
    report["search_warm"] = summary(search_samples)
    for compact in [False, True]:
        suffix = "compact=true" if compact else "compact=false"
        for name, path in [("detail", "/api/hotels/"+urllib.parse.quote(ids[0])+"?"+suffix),
                           ("compare", "/api/compare?ids="+urllib.parse.quote(",".join(ids))+"&"+suffix)]:
            cold, _ = request(base, path)
            for _ in range(warmups):
                request(base, path)
            values = [request(base, path)[0] for _ in range(samples)]
            report["profiles"][name + ("_compact" if compact else "_full")] = {"first_sample": cold, "warm": summary(values)}
    report["targets"] = {"warm_search_p95_under_800ms": report["search_warm"]["p95_ms"] < 800,
                         "compact_detail_under_400KB": report["profiles"]["detail_compact"]["warm"]["bytes_max"] < 400_000,
                         "compact_compare_p95_under_1s": report["profiles"]["compare_compact"]["warm"]["p95_ms"] < 1000}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--ids", default=",".join(DEFAULT_IDS))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--assert-targets", action="store_true")
    args = parser.parse_args()
    if args.samples < 1 or args.warmups < 0:
        parser.error("samples >= 1 and warmups >= 0 required")
    ids = args.ids.split(",")
    if not 2 <= len(set(ids)) <= 4 or len(ids) != len(set(ids)):
        parser.error("Choose 2–4 distinct hotel IDs")
    report = benchmark(args.base_url.rstrip("/"), samples=args.samples, warmups=args.warmups, ids=ids)
    text = json.dumps(report, ensure_ascii=False, indent=2)+"\n"
    if args.output:
        args.output.write_text(text)
    print(text, end="")
    return int(args.assert_targets and not all(report["targets"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
