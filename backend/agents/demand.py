"""Guest demand counted from the local search log; no synthetic estimates.

A count is the number of logged searches that asked for a characteristic and
showed this hotel in the returned results. Zero is a real zero for this stand,
not an estimate; the log is local and starts empty on a fresh install.
"""
from __future__ import annotations

from collections import Counter


def search_demand(repo, hotel_id: str | None = None, *, recent: int = 10) -> dict:
    requested, shown_with, queries = Counter(), Counter(), []
    rows = repo.all("search_queries") if hasattr(repo, "all") else []
    for row in rows:
        parsed = row.get("parsed") or {}
        wanted = sorted({criterion.get("characteristic_id") for criterion in [*parsed.get("must", []), *parsed.get("nice", [])]
                         if criterion.get("characteristic_id")})
        requested.update(wanted)
        results = row.get("results") or []
        if hotel_id and hotel_id in results:
            shown_with.update(wanted)
            queries.append({"text": row.get("text", ""), "characteristic_ids": wanted, "position": results.index(hotel_id) + 1,
                            "created_at": row.get("created_at")})
    queries.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"total_searches": len(rows), "requested": dict(requested), "shown_with": dict(shown_with),
            "queries": queries[:recent], "synthetic": False,
            "method": "Локальный журнал поиска: запросы с этим условием, где отель был в выдаче"}
