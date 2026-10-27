"""Real QueuePool leases over strict DBAPI fakes, without pooling bypasses."""
import gc
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import psycopg
import pytest
from psycopg.pq import TransactionStatus
from sqlalchemy.exc import TimeoutError as PoolTimeout

from backend.db.repository import Repository
from backend.tests.dbapi_fakes import FakeDatabase


@pytest.fixture
def pooled():
    database = FakeDatabase()
    repo = Repository("unused", pool_size=2, pool_timeout=.05, connection_factory=database.connect)
    yield repo, database
    repo.close()


def test_lazy_pool_reuses_a_physical_connection_and_keeps_raw_context_out_of_pooling(pooled):
    repo, database = pooled
    assert not database.connections
    assert repo.healthy() and repo.healthy()
    [connection] = database.connections
    assert connection.commits == 2 and connection.context_entries == 0
    assert not connection.closed and not connection.autocommit
    assert connection.info.transaction_status == TransactionStatus.IDLE
    # The installed PG psycopg dialect uses an empty command for its ping.
    assert database.calls == [("SELECT 1", None), (";", None), ("SELECT 1", None)]
    assert repo._pool.checkedout() == 0


@pytest.mark.parametrize("error", [RuntimeError("cancel"), KeyboardInterrupt(), SystemExit()])
def test_any_body_exception_rolls_back_without_transaction_leakage(pooled, error):
    repo, database = pooled
    with pytest.raises(type(error)):
        with repo._connection() as connection:
            connection.execute("INSERT INTO pool_probe VALUES (%s,%s)", ("uncommitted", "value"))
            raise error
    [connection] = database.connections
    assert not database.values and not connection.pending
    assert connection.info.transaction_status == TransactionStatus.IDLE and not connection.closed
    with repo._connection() as returned:
        assert returned is connection
        assert returned.execute("SELECT value FROM pool_probe WHERE key=%s", ("uncommitted",)).fetchone() == (None,)


def test_sql_error_rolls_back_failed_transaction_and_next_lease_can_commit(pooled):
    repo, database = pooled
    snapshot = {"old": {"id": "old", "hotel_id": "hotel"}}
    repo._search_snapshot = snapshot
    repo.search_cache_put("old", repo.search_generation(), ["valid result"], None)
    with pytest.raises(psycopg.errors.DivisionByZero):
        with repo._connection() as connection:
            connection.execute("INSERT INTO pool_probe VALUES (%s,%s)", ("failed", "value"))
            connection.execute("SELECT 1/0")
    assert repo._search_snapshot is snapshot and repo.search_generation() == 0
    assert repo.search_cache_get("old")[1] == ["valid result"]
    with repo._connection() as connection:
        connection.execute("INSERT INTO pool_probe VALUES (%s,%s)", ("committed", "value"))
    assert database.values == {"committed": "value"}
    assert len(database.connections) == 1


def test_failed_commit_is_not_replayed_published_or_reused(pooled):
    repo, database = pooled
    database.fail_commit = True
    with pytest.raises(RuntimeError, match="transaction failure"):
        repo.upsert("questions", {"id": "must-not-publish"})
    [failed] = database.connections
    assert failed.closed and not database.values and repo.search_generation() == 1
    assert repo._search_snapshot is None
    assert sum(query.startswith("INSERT INTO") for query, _ in database.calls) == 1
    database.fail_commit = False
    assert repo.healthy() and len(database.connections) == 2


def test_lost_commit_acknowledgment_invalidates_stale_snapshots_without_replaying_values(pooled):
    repo, database = pooled
    repo._search_snapshot = {"before": {"id": "before", "hotel_id": "hotel"}}
    generation = repo.search_generation()
    repo.search_cache_put("old", generation, ["old ranking"], None)
    database.lose_commit_response = True
    with pytest.raises(psycopg.OperationalError, match="acknowledgment"):
        with repo._connection() as connection:
            connection.execute("INSERT INTO pool_probe VALUES (%s,%s)", ("server-committed", "value"))
    assert database.values == {"server-committed": "value"}
    assert sum(query.startswith("INSERT INTO") for query, _ in database.calls) == 1
    assert repo._search_snapshot is None and repo.search_generation() == generation + 1
    assert repo.search_cache_get("old")[1] is None
    repo.search_cache_put("racing", generation, ["stale ranking"], None)
    assert repo.search_cache_get("racing")[1] is None
    assert database.connections[0].closed
    database.lose_commit_response = False
    assert repo.healthy() and len(database.connections) == 2


def test_ambiguous_commit_releases_capacity_before_waiting_for_serialized_writer():
    database = FakeDatabase()
    repo = Repository("unused", pool_size=1, pool_timeout=1, connection_factory=database.connect)
    reader_has_lease, writer_has_lock = Event(), Event()
    def reader():
        with repo._connection() as connection:
            original_commit = connection.commit
            def lost_response():
                database.lose_commit_response = True
                try:
                    original_commit()
                finally:
                    database.lose_commit_response = False
            connection.commit = lost_response
            connection.execute("INSERT INTO pool_probe VALUES (%s,%s)", ("committed", "value"))
            reader_has_lease.set()
            assert writer_has_lock.wait(1)
    def writer():
        assert reader_has_lease.wait(1)
        with repo._search_lock:
            writer_has_lock.set()
            return repo.upsert("questions", {"id": "following-write"})
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            reading = executor.submit(reader)
            writing = executor.submit(writer)
            assert writing.result(timeout=3) == {"id": "following-write"}
            with pytest.raises(psycopg.OperationalError, match="acknowledgment"):
                reading.result(timeout=3)
        assert repo.search_generation() == 2 and repo._search_snapshot is None
        assert repo._pool.checkedout() == 0
    finally:
        repo.close()


def test_failed_rollback_evicts_connection_and_preserves_original_error(pooled):
    repo, database = pooled
    database.fail_rollback = True
    with pytest.raises(psycopg.errors.DivisionByZero):
        with repo._connection() as connection:
            connection.execute("SELECT 1/0")
    assert database.connections[0].closed
    database.fail_rollback = False
    assert repo.healthy() and len(database.connections) == 2


def test_closed_idle_connection_is_replaced_by_real_dialect_pre_ping(pooled):
    repo, database = pooled
    assert repo.healthy()
    dead = database.connections[0]
    dead.close()
    dead.broken = True
    assert repo.healthy()
    assert len(database.connections) == 2 and database.connections[1] is not dead
    assert database.connections[1].info.transaction_status == TransactionStatus.IDLE


def test_disconnect_during_a_lease_is_not_retried(pooled):
    repo, database = pooled
    with pytest.raises(psycopg.OperationalError):
        with repo._connection() as connection:
            connection.execute("INSERT INTO pool_probe VALUES (%s,%s)", ("ambiguous", "value"))
            connection.close()
            connection.broken = True
            connection.execute("SELECT 1")
    assert not database.values and len(database.connections) == 1
    assert repo.healthy() and len(database.connections) == 2


def test_copy_failure_cleans_transaction_temp_table_and_snapshot_before_reuse(pooled):
    repo, database = pooled
    repo._search_snapshot = {"original": {"id": "original", "hotel_id": "hotel"}}
    database.fail_copy_after = 1
    values = [{"id": "one", "hotel_id": "hotel"}, {"id": "two", "hotel_id": "hotel"}]
    with pytest.raises(RuntimeError, match="COPY failure"):
        repo.replace_materialized_facts(["hotel"], values)
    assert repo._search_snapshot == {"original": {"id": "original", "hotel_id": "hotel"}}
    assert repo.search_generation() == 0 and not database.connections[0].temp_tables
    database.fail_copy_after = None
    repo.replace_materialized_facts(["hotel"], values)
    assert set(repo._search_snapshot) == {"one", "two"} and repo.search_generation() == 1
    assert len(database.connections) == 1 and not database.connections[0].temp_tables


def test_reset_failure_discards_connection_after_successful_commit(pooled):
    repo, database = pooled
    database.fail_reset = True
    repo.upsert("questions", {"id": "committed"})
    assert repo.search_generation() == 1 and database.connections[0].closed
    database.fail_reset = False
    assert repo.healthy() and len(database.connections) == 2


def test_capacity_has_no_overflow_and_times_out_without_a_hidden_connection(pooled):
    repo, database = pooled
    with repo._connection(), repo._connection():
        assert len(database.connections) == 2 and repo._pool.checkedout() == 2
        with pytest.raises(PoolTimeout):
            repo.healthy()
        assert len(database.connections) == 2
    assert repo._pool.checkedout() == 0 and repo.healthy()


def test_concurrent_transactions_never_share_a_physical_lease():
    database = FakeDatabase()
    repo = Repository("unused", pool_size=2, pool_timeout=2, connection_factory=database.connect)
    first_two = Barrier(2)
    lock, active = Lock(), set()
    maximum = [0]
    def write(index):
        with repo._connection() as connection:
            with lock:
                assert connection.identity not in active
                active.add(connection.identity)
                maximum[0] = max(maximum[0], len(active))
            if index < 2:
                first_two.wait(timeout=2)
            connection.execute("INSERT INTO pool_probe VALUES (%s,%s)", (index, index))
            time.sleep(.002)
            with lock:
                active.remove(connection.identity)
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write, range(16)))
        assert maximum[0] == 2 and len(database.connections) == 2
        assert database.values == {index: index for index in range(16)}
        assert repo._pool.checkedout() == 0
    finally:
        repo.close()


def test_close_is_idempotent_rejects_checkout_and_drains_active_transaction(pooled):
    repo, database = pooled
    with repo._connection() as connection:
        connection.execute("INSERT INTO pool_probe VALUES (%s,%s)", ("finishing", "value"))
        repo.close()
        assert not connection.closed
        with pytest.raises(RuntimeError, match="closed"):
            repo.healthy()
    assert connection.closed and database.values == {"finishing": "value"}
    repo.close()
    assert len(database.connections) == 1


def test_finalizer_releases_idle_connection_without_retaining_repository():
    database = FakeDatabase()
    repo = Repository("unused", connection_factory=database.connect)
    repo.healthy()
    reference = weakref.ref(repo)
    del repo
    gc.collect()
    assert reference() is None and database.connections[0].closed


def test_process_exit_cleanup_marks_a_still_live_repository_closed(pooled):
    repo, database = pooled
    repo.healthy()
    repo._pool_cleanup()
    assert database.connections[0].closed
    with pytest.raises(RuntimeError, match="closed"):
        repo.healthy()


def test_close_rejects_an_already_waiting_checkout_without_leaking_its_driver():
    database = FakeDatabase()
    repo = Repository("unused", pool_size=1, pool_timeout=1, connection_factory=database.connect)
    started = Event()
    def waiting():
        started.set()
        return repo.healthy()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with repo._connection():
                future = executor.submit(waiting)
                assert started.wait(1)
                repo.close()
            # Returning the active lease permits a pending acquisition to
            # finish internally, but it must never expose another raw lease.
            with pytest.raises(RuntimeError, match="closed"):
                future.result(timeout=3)
        assert all(connection.closed for connection in database.connections)
    finally:
        repo.close()


@pytest.mark.parametrize("kwargs", [{"pool_size": 0}, {"pool_size": -1}, {"pool_size": True},
                                    {"pool_timeout": -1}, {"pool_timeout": float("nan")}])
def test_invalid_capacity_or_unbounded_wait_is_rejected_before_connect(kwargs):
    database = FakeDatabase()
    with pytest.raises(ValueError):
        Repository("unused", connection_factory=database.connect, **kwargs)
    assert not database.connections


@pytest.mark.parametrize("configuration", ["autocommit", "active-transaction"])
def test_a_creator_cannot_bypass_transaction_ownership(configuration):
    database = FakeDatabase()
    def creator():
        connection = database.connect()
        if configuration == "autocommit":
            connection.autocommit = True
        else:
            connection.execute("INSERT INTO pool_probe VALUES (%s,%s)", ("preexisting", "value"))
        return connection
    repo = Repository("unused", connection_factory=creator)
    try:
        with pytest.raises(psycopg.InterfaceError, match="idle transactional"):
            repo.healthy()
        assert database.values == {} and database.connections[0].closed
        assert repo.search_generation() == 0
        assert not any(query == "SELECT 1" for query, _ in database.calls)
    finally:
        repo.close()
