"""Read-only HTTP size/latency measurements for the prepared regional release."""
from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import median, quantiles
from urllib.parse import quote

import httpx


def distribution(values):
    return {"count": len(values), "p50": round(median(values), 3),
            "p95": round(quantiles(values, n=100, method="inclusive")[94], 3), "max": round(max(values), 3)}


def measure(url):
    report = {"base_url": url, "checked_at": datetime.now(UTC).isoformat(),
              "method": "sequential warm HTTP, no concurrent-load SLA; UTF-8 response bytes"}
    with httpx.Client(base_url=url, timeout=30) as client:
        response = client.get("/api/hotels")
        response.raise_for_status()
        hotels = response.json()
        sizes, milliseconds, exceeded = [], [], []
        for hotel in hotels:
            started = time.perf_counter()
            response = client.get("/api/hotels/" + quote(hotel["id"], safe="") + "?compact=true")
            response.raise_for_status()
            elapsed = 1000 * (time.perf_counter() - started)
            sizes.append(len(response.content))
            milliseconds.append(elapsed)
            if sizes[-1] > 400_000:
                exceeded.append({"id": hotel["id"], "bytes": sizes[-1]})
        report["compact_profile_bytes"] = distribution(sizes)
        report["compact_profile_ms"] = distribution(milliseconds)
        report["over_400000_bytes"] = exceeded
        queries = ["тихий номер с видом на закат недалеко от моря", "круглосуточный магазин рядом",
                   "вид на Морской вокзал", "из аэропорта без пересадок"]
        for text in queries:
            response = client.post("/api/search", json={"text": text, "limit": 40, "compact": True})
            response.raise_for_status()
        latency, search_sizes, returned_counts = [], [], []
        for index in range(40):
            started = time.perf_counter()
            response = client.post("/api/search", json={"text": queries[index % len(queries)], "limit": 40, "compact": True})
            response.raise_for_status()
            latency.append(1000 * (time.perf_counter() - started))
            search_sizes.append(len(response.content))
            returned_counts.append(len(response.json()["results"]))
        report["warm_search_ms"] = distribution(latency)
        report["search_response_bytes"] = distribution(search_sizes)
        report["requested_search_limit"] = 40
        report["compact_page_returned_counts"] = sorted(set(returned_counts))
        report["catalogue_hotels"] = len(hotels)
    report["passed"] = not exceeded and max(search_sizes) < 400_000
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8081")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = measure(args.url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
