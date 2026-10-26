"""Polling revisions follow durable visible writes, not search telemetry."""
from concurrent.futures import ThreadPoolExecutor

import pytest

import backend.db.repository as module
from backend.db.repository import Repository
from backend.tracing import Trace


class Copy:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def write_row(self, row):
        pass


class Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, *_):
        return []

    def executemany(self, *_):
        pass

    def copy(self, *_):
        return Copy()


class Connection(Cursor):
    fail_commit = False

    def __exit__(self, error, *_):
        if error is None and self.fail_commit:
            raise RuntimeError("Synthetic transaction failure")

    def cursor(self):
        return Cursor()


@pytest.fixture
def repo(monkeypatch):
    connection = Connection()
    monkeypatch.setattr(module.psycopg, "connect", lambda *_, **__: connection)
    value = Repository()
    value._search_snapshot = {"old-fact": {"id": "old-fact", "hotel_id": "hotel-a", "characteristic_id": "sea_view"}}
    value.search_cache_put("search", value.search_generation(), ["cached result"], None)
    return value, connection


def write(repo, method, table):
    rows = [{"id": f"changed-{index}", "hotel_id": "hotel-a"} for index in range(50 if method == "bulk-copy" else 1)]
    if method.startswith("bulk"):
        repo.upsert_many(table, rows)
    else:
        getattr(repo, method)(table, rows[0])


@pytest.mark.parametrize("table", ["hotels", "characteristics", "questions", "answers", "conflicts",
                                  "change_events", "expectation_passports", "passport_items"])
@pytest.mark.parametrize("method", ["insert", "upsert", "bulk-small", "bulk-copy"])
def test_visible_writes_advance_once_and_preserve_the_facts_snapshot(repo, table, method):
    value, _ = repo
    generation, snapshot = value.search_generation(), value._search_snapshot
    write(value, method, table)
    assert value.search_generation() == generation + 1
    assert value._search_snapshot is snapshot
    assert value.search_cache_get("search")[1] is None
    value.search_cache_put("racing", generation, ["obsolete calculation"], None)
    assert value.search_cache_get("racing")[1] is None


@pytest.mark.parametrize("table", ["search_queries", "agent_runs", "agent_steps", "llm_cache", "tool_cache"])
@pytest.mark.parametrize("method", ["insert", "upsert", "bulk-small", "bulk-copy"])
def test_search_and_trace_writes_do_not_create_a_refetch_loop(repo, table, method):
    value, _ = repo
    generation, snapshot = value.search_generation(), value._search_snapshot
    write(value, method, table)
    assert value.search_generation() == generation
    assert value._search_snapshot is snapshot
    assert value.search_cache_get("search")[1] == ["cached result"]


def test_real_search_trace_lifecycle_does_not_change_the_polling_revision(repo):
    value, _ = repo
    generation = value.search_generation()
    trace = Trace(value, "search", trigger="synthetic revision test")
    trace.step("search", "decision", "Synthetic result", output={"hotel_ids": []})
    trace.finish()
    value.insert("search_queries", {"id": "synthetic-query", "results": []})
    assert value.search_generation() == generation


@pytest.mark.parametrize("method", ["insert", "upsert", "bulk-small", "bulk-copy"])
def test_failed_transaction_does_not_publish_a_revision(repo, method):
    value, connection = repo
    generation = value.search_generation()
    connection.fail_commit = True
    with pytest.raises(RuntimeError, match="transaction failure"):
        write(value, method, "questions")
    assert value.search_generation() == generation
    assert value.search_cache_get("search")[1] == ["cached result"]


def test_empty_bulk_is_not_a_mutation_but_removing_hotel_facts_is(repo):
    value, _ = repo
    generation = value.search_generation()
    value.upsert_many("questions", [])
    assert value.search_generation() == generation
    value.replace_facts("hotel-a", [])
    assert value.search_generation() == generation + 1
    assert value._search_snapshot == {}


def test_concurrent_question_writes_do_not_lose_revisions(repo):
    value, _ = repo
    generation = value.search_generation()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: value.insert("questions", {"id": f"question-{index}"}), range(32)))
    assert value.search_generation() == generation + 32
