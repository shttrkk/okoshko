"""Bound bulk reads and cache invalidation protect the public read contract."""
import pytest

import backend.db.repository as module
from backend.db.repository import Repository
from backend.tests.dbapi_fakes import FakeDatabase


@pytest.mark.parametrize("compact,expected", [(True, 4), (False, 6)])
def test_detail_query_count_is_constant_and_ids_are_bound(monkeypatch, compact, expected):
    connection = FakeDatabase()
    monkeypatch.setattr(module.psycopg, "connect", connection.connect)
    ids = ["hotel-a", "hotel-b", "hotel-c", "x' OR true --"]
    Repository().catalog_details(ids, compact=compact)
    assert len(connection.calls) == expected
    assert all(ids == params[0] and "x' OR true --" not in query for query, params in connection.calls)
    facts_sql = next(query for query, _ in connection.calls if 'FROM "facts"' in query)
    assert ("evidence-'inputs'-'output'" in facts_sql) is compact
    assert any('FROM "claims"' in query for query, _ in connection.calls) is not compact


@pytest.mark.parametrize("table", ["facts", "hotels", "characteristics"])
def test_writes_invalidate_rankings_and_reject_racing_computation(monkeypatch, table):
    monkeypatch.setattr(module.psycopg, "connect", FakeDatabase().connect)
    repo = Repository()
    generation, _ = repo.search_cache_get("query")
    repo.search_cache_put("query", generation, ["old"], None)
    assert repo.search_cache_get("query")[1] == ["old"]
    repo.upsert(table, {"id": "changed", "hotel_id": "hotel-a"})
    assert repo.search_cache_get("query")[1] is None
    repo.search_cache_put("racing", generation, ["old"], None)
    assert repo.search_cache_get("racing")[1] is None


def test_expired_fact_deadline_expires_cached_ranking(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    repo = Repository()
    generation, _ = repo.search_cache_get("query")
    repo.search_cache_put("query", generation, ["result"], 101.0)
    assert repo.search_cache_get("query")[1] == ["result"]
    clock[0] = 101.1
    assert repo.search_cache_get("query")[1] is None
