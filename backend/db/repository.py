"""Small synchronous PostgreSQL repository shared by independent agent modules."""
from __future__ import annotations

import json
import math
import time
import weakref
from collections import OrderedDict
from contextlib import contextmanager
from datetime import date, datetime
from functools import lru_cache, partial, wraps
from threading import RLock
from typing import Any

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
from sqlalchemy.pool import QueuePool

from backend.contracts import Claim
from backend.settings import DB_URL

TABLES = frozenset({
    "regions", "sources", "hotels", "hotel_facades", "room_types", "buildings",
    "osm_features", "characteristics", "characteristic_versions", "agent_runs",
    "agent_steps", "claims", "facts", "conflicts", "questions", "answers",
    "change_events", "jobs", "tool_cache", "llm_cache", "search_queries",
    "eval_labels", "eval_results", "expectation_passports", "passport_items",
})
# Visible data mutations advance the polling revision. Search logs and agent
# traces deliberately do not: a read/search must not trigger another refetch.
REVISION_TABLES = frozenset({"hotels", "characteristics", "questions", "answers", "conflicts",
                             "change_events", "expectation_passports", "passport_items"})

COMPACT_FACT = """(payload-'evidence') || jsonb_build_object('evidence', COALESCE(
    (SELECT jsonb_agg((evidence-'inputs'-'output'-'limitations') || jsonb_build_object('inputs',
      jsonb_strip_nulls(jsonb_build_object(
        'facade_azimuth_deg', evidence->'inputs'->'facade_azimuth_deg',
        'window_half_angle_deg', evidence->'inputs'->'window_half_angle_deg'))))
     FROM jsonb_array_elements(payload->'evidence') evidence),
    '[]'::jsonb))"""


def search_fact(value: dict) -> dict:
    """Retain small geometry scalars used by explanations, never raw arrays."""
    return {**value, "evidence": [
        {**{key: field for key, field in evidence.items() if key not in {"inputs", "output", "limitations"}},
         "inputs": {key: field for key, field in evidence.get("inputs", {}).items()
                    if key in {"facade_azimuth_deg", "window_half_angle_deg"} and field is not None}}
        for evidence in value.get("evidence", [])]}


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=lambda obj: obj.isoformat() if isinstance(obj, date | datetime) else str(obj))


def serialized_write(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        with self._search_lock:
            return function(self, *args, **kwargs)
    return wrapped


class Repository:
    def __init__(self, db_url: str = DB_URL, *, pool_size: int = 4, pool_timeout: float = 3,
                 connection_factory=None):
        if not isinstance(pool_size, int) or isinstance(pool_size, bool) or pool_size < 1:
            raise ValueError("Repository pool_size must be a positive integer")
        if not isinstance(pool_timeout, int | float) or not math.isfinite(pool_timeout) or pool_timeout < 0:
            raise ValueError("Repository pool_timeout must be finite and nonnegative")
        self.db_url = db_url
        self._search_snapshot = None
        self._search_lock = RLock()
        self._search_generation = 0
        self._search_results = OrderedDict()
        self._pool_lock = RLock()
        self._closed = False
        # Capture only the DSN/factory, not self: a finalizer must not keep its
        # Repository alive through pool -> creator -> Repository references.
        creator = connection_factory or partial(psycopg.connect, db_url, connect_timeout=3)
        self._pool = QueuePool(creator, pool_size=pool_size, max_overflow=0, timeout=pool_timeout,
                               use_lifo=True, pre_ping=True, reset_on_return="rollback",
                               dialect=PGDialect_psycopg(dbapi=psycopg))
        self._pool_cleanup = weakref.finalize(self, Repository._dispose_pool, weakref.ref(self), self._pool)

    @staticmethod
    def _dispose_pool(reference, pool):
        repository = reference()
        if repository is None:
            pool.dispose()
        else:
            # At process exit the singleton can still be alive. Mark it closed
            # too, so background work cannot reopen a disposed pool.
            with repository._pool_lock:
                repository._closed = True
                pool.dispose()

    @contextmanager
    def _connection(self):
        """One exclusive transaction per lease; never retry business SQL."""
        with self._pool_lock:
            if self._closed:
                raise RuntimeError("Repository is closed")
        # A waiting checkout must not prevent an active lease from returning.
        lease = self._pool.connect()
        connection = lease.dbapi_connection
        discard, committing, failure = False, False, None
        try:
            with self._pool_lock:
                if self._closed:
                    raise RuntimeError("Repository is closed")
            if (connection.closed or connection.broken or connection.autocommit
                    or connection.info.transaction_status != TransactionStatus.IDLE):
                discard = True
                raise psycopg.InterfaceError("Repository requires an idle transactional connection")
            yield connection
            committing = True
            connection.commit()
            if connection.info.transaction_status != TransactionStatus.IDLE:
                raise psycopg.InterfaceError("Repository transaction did not finish")
        except BaseException as error:
            failure = error
            # A commit failure may be ambiguous; evict, never replay the write.
            discard = discard or committing or connection.closed or connection.broken
            try:
                if not connection.closed:
                    connection.rollback()
                discard = discard or connection.info.transaction_status != TransactionStatus.IDLE
            except BaseException:
                discard = True
            raise
        finally:
            # Serialize check-in with dispose: no live connection may enter the
            # idle queue after close() has drained it.
            try:
                with self._pool_lock:
                    try:
                        if discard or self._closed or connection.closed or connection.broken:
                            lease.invalidate(failure)
                    finally:
                        lease.close()
            finally:
                if committing and failure is not None:
                    # The server may have committed despite a lost response.
                    # Publish no tentative values, but force a new DB snapshot.
                    # Return the lease BEFORE waiting for the search lock: a
                    # serialized writer may own that lock while awaiting pool
                    # capacity, so the opposite order would deadlock.
                    with self._search_lock:
                        self._search_snapshot = None
                        self._search_generation += 1
                        self._search_results.clear()

    def close(self):
        """Reject new leases, close idle connections, drain active ones on return."""
        with self._pool_lock:
            if not self._closed:
                self._closed = True
                self._pool_cleanup()

    def _update_search(self, values: list[dict], *, hotel_ids: list[str] | None = None):
        with self._search_lock:
            self._search_generation += 1
            self._search_results.clear()
            if self._search_snapshot is None:
                return
            snapshot = {key: value for key, value in self._search_snapshot.items()
                        if hotel_ids is None or value["hotel_id"] not in hotel_ids}
            for value in values:
                snapshot[value["id"]] = search_fact(value)
            self._search_snapshot = snapshot

    def _invalidate_revision(self, table: str):
        """Invalidate derived results, preserving the unaffected facts snapshot."""
        if table in REVISION_TABLES:
            with self._search_lock:
                self._search_generation += 1
                self._search_results.clear()

    def search_cache_get(self, key: str):
        """Cache derived rankings only; every request still gets its own trace/log.

        The generation prevents a computation racing a fact/catalog write from
        entering the cache. Expiry is capped by both fact validity and two
        minutes. This has the same single-process ownership as search_facts.
        """
        with self._search_lock:
            cached = self._search_results.get(key)
            if cached and cached[0] > time.time():
                self._search_results.move_to_end(key)
                return self._search_generation, cached[1]
            self._search_results.pop(key, None)
            return self._search_generation, None

    def search_generation(self) -> int:
        with self._search_lock:
            return self._search_generation

    def search_cache_put(self, key: str, generation: int, value, valid_until: float | None):
        with self._search_lock:
            if generation != self._search_generation:
                return
            deadline = min(time.time() + 120, valid_until) if valid_until else time.time() + 120
            if deadline > time.time():
                self._search_results[key] = deadline, value
                self._search_results.move_to_end(key)
                while len(self._search_results) > 32:
                    self._search_results.popitem(last=False)

    @staticmethod
    def table(name: str) -> sql.Identifier:
        if name not in TABLES:
            raise ValueError(f"Unknown table: {name}")
        return sql.Identifier(name)

    def all(self, table: str) -> list[dict]:
        query = sql.SQL("SELECT payload FROM {} ORDER BY created_at, id").format(self.table(table))
        with self._connection() as conn:
            return [row[0] for row in conn.execute(query)]

    def prior_poi_claims(self, characteristic_ids: list[str]) -> list[dict]:
        """Only observations used for declared POI backfill/version checks."""
        if not characteristic_ids:
            return []
        query = ("SELECT payload FROM claims WHERE payload->>'characteristic_id'=ANY(%s) "
                 "AND starts_with(payload->>'method', %s) ORDER BY created_at,id")
        with self._connection() as conn:
            return [row[0] for row in conn.execute(query, (sorted(set(characteristic_ids)), "local_poi_"))]

    def get(self, table: str, id: str) -> dict | None:
        query = sql.SQL("SELECT payload FROM {} WHERE id=%s").format(self.table(table))
        with self._connection() as conn:
            row = conn.execute(query, (id,)).fetchone()
            return row[0] if row else None

    def find(self, table: str, **equal) -> list[dict]:
        predicates = []
        params = []
        for key, value in equal.items():
            if isinstance(value,str):
                predicates.append(sql.SQL("payload->>{} = %s").format(sql.Literal(key)))
                params.append(value)
            else:
                predicates.append(sql.SQL("payload->%s = %s"))
                params.extend([key, Jsonb(value, dumps=dump_json)])
        where = sql.SQL(" AND ").join(predicates) if predicates else sql.SQL("true")
        query = sql.SQL("SELECT payload FROM {} WHERE {} ORDER BY created_at, id").format(self.table(table), where)
        with self._connection() as conn:
            return [row[0] for row in conn.execute(query, params)]

    def get_many(self, table: str, ids: list[str]) -> list[dict]:
        query = sql.SQL("SELECT payload FROM {} WHERE id=ANY(%s) ORDER BY created_at,id").format(self.table(table))
        with self._connection() as conn:
            return [row[0] for row in conn.execute(query, (ids,))]

    def catalog_details(self, hotel_ids: list[str], *, compact: bool = False) -> dict[str, list[dict]]:
        """One connection and a constant number of bound queries for 1–4 hotels.

        Raw calculation payloads are removed in SQL, before transfer from DB.
        Full reads keep the legacy facts/claims/geometry contract intact.
        """
        tables = ["hotels", "facts", "conflicts", "questions"]
        if not compact:
            tables.extend(["claims", "change_events"])
        result = {"claims": [], "change_events": []}
        with self._connection() as conn:
            for table in tables:
                projection = sql.SQL(COMPACT_FACT if compact and table == "facts" else "payload")
                predicate = sql.SQL("id=ANY(%s)" if table == "hotels" else "payload->>'hotel_id'=ANY(%s)")
                if table == "change_events":
                    # Include events with only affected_hotel_ids, as read_geo does.
                    predicate = sql.SQL("payload->>'hotel_id'=ANY(%s) OR (payload->'affected_hotel_ids') ?| %s")
                query = sql.SQL("SELECT {} FROM {} WHERE {} ORDER BY created_at,id").format(projection, self.table(table), predicate)
                params = (hotel_ids, hotel_ids) if table == "change_events" else (hotel_ids,)
                result[table] = [row[0] for row in conn.execute(query, params)]
        return result

    def insert(self, table: str, value: dict) -> dict:
        table_sql = self.table(table)
        if table == "claims":
            value = Claim.model_validate(value).model_dump(mode="json")
        query = sql.SQL("INSERT INTO {} (id,payload) VALUES (%s,%s)").format(table_sql)
        with self._connection() as conn:
            conn.execute(query, (value["id"], Jsonb(value, dumps=dump_json)))
        if table == "facts":
            self._update_search([value])
        self._invalidate_revision(table)
        return value

    @serialized_write
    def upsert(self, table: str, value: dict) -> dict:
        if table == "claims":
            raise ValueError("Claims are append-only; use add_claim")
        query = sql.SQL("""INSERT INTO {} (id,payload) VALUES (%s,%s)
            ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload, updated_at=now()""").format(self.table(table))
        with self._connection() as conn:
            conn.execute(query, (value["id"], Jsonb(value, dumps=dump_json)))
        if table == "facts":
            self._update_search([value])
        self._invalidate_revision(table)
        return value

    def add_claim(self, claim: Claim | dict) -> dict:
        value = claim.model_dump(mode="json") if isinstance(claim, Claim) else claim
        return self.insert("claims", value)

    @serialized_write
    def upsert_many(self, table: str, values: list[dict]) -> None:
        if not values:
            return
        if table == "claims":
            raise ValueError("Claims are append-only")
        query = sql.SQL("""INSERT INTO {} (id,payload) VALUES (%s,%s)
            ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload, updated_at=now()""").format(self.table(table))
        with self._connection() as conn, conn.cursor() as cursor:
            if len(values) < 50:
                cursor.executemany(query, [(value["id"], Jsonb(value, dumps=dump_json)) for value in values])
            else:
                # A single COPY and merge avoids tens of thousands of prepared
                # statements during startup under amd64 emulation.
                cursor.execute("CREATE TEMP TABLE batch_import(id text PRIMARY KEY,payload jsonb NOT NULL) ON COMMIT DROP")
                with cursor.copy("COPY batch_import(id,payload) FROM STDIN") as copy:
                    for value in values:
                        copy.write_row((value["id"], dump_json(value)))
                cursor.execute(sql.SQL("INSERT INTO {}(id,payload) SELECT id,payload FROM batch_import ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload,updated_at=now()").format(self.table(table)))
        if table == "facts":
            self._update_search(values)
        self._invalidate_revision(table)

    def insert_claims(self, values: list[Claim | dict]) -> list[dict]:
        validated = [Claim.model_validate(value).model_dump(mode="json") for value in values]
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.executemany("INSERT INTO claims(id,payload) VALUES(%s,%s)",
                               [(value["id"], Jsonb(value)) for value in validated])
        return validated

    @serialized_write
    def replace_facts(self, hotel_id: str, values: list[dict]) -> None:
        # Atomic materialized-view replacement removes obsolete floor-range keys.
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute("DELETE FROM facts WHERE payload->>'hotel_id'=%s",(hotel_id,))
            cursor.executemany("INSERT INTO facts(id,payload) VALUES(%s,%s)",
                               [(value["id"],Jsonb(value,dumps=dump_json)) for value in values])
        self._update_search(values, hotel_ids=[hotel_id])

    @serialized_write
    def replace_materialized_facts(self, hotel_ids: list[str], values: list[dict]) -> None:
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute("CREATE TEMP TABLE resolved_import(id text PRIMARY KEY,payload jsonb NOT NULL) ON COMMIT DROP")
            with cursor.copy("COPY resolved_import(id,payload) FROM STDIN") as copy:
                for value in values:
                    copy.write_row((value["id"], dump_json(value)))
            cursor.execute("DELETE FROM facts WHERE payload->>'hotel_id'=ANY(%s)", (hotel_ids,))
            cursor.execute("INSERT INTO facts(id,payload) SELECT id,payload FROM resolved_import")
        self._update_search(values, hotel_ids=hotel_ids)

    def count(self, table: str) -> int:
        with self._connection() as conn:
            return conn.execute(sql.SQL("SELECT count(*) FROM {}").format(self.table(table))).fetchone()[0]

    def healthy(self) -> bool:
        with self._connection() as conn:
            return conn.execute("SELECT 1").fetchone()[0] == 1

    def expiry_token(self) -> str | None:
        with self._connection() as conn:
            return conn.execute("SELECT md5(string_agg(id,',' ORDER BY id)) FROM claims WHERE (payload->>'valid_until')::timestamptz <= now()").fetchone()[0]

    def search_facts(self, characteristic_ids: list[str] | None = None, month: int | None = None) -> list[dict]:
        # Keep references and quotes. Full source-method limitations are loaded
        # by the evidence API, rather than repeated in every search snapshot.
        query = "SELECT " + COMPACT_FACT + " FROM facts"
        with self._search_lock:
            if self._search_snapshot is None:
                with self._connection() as conn:
                    self._search_snapshot = {row[0]["id"]: row[0] for row in conn.execute(query)}
            snapshot = self._search_snapshot
        ids = set(characteristic_ids) if characteristic_ids is not None else None
        return [row for row in snapshot.values() if (ids is None or row["characteristic_id"] in ids)
                and (month is None or row.get("scope", {}).get("month") in {None, month})]


@lru_cache
def get_repo() -> Repository:
    return Repository()

