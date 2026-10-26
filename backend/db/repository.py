"""Small synchronous PostgreSQL repository shared by independent agent modules."""
from __future__ import annotations

import json
from datetime import date, datetime
from functools import lru_cache
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from backend.contracts import Claim
from backend.settings import DB_URL

TABLES = frozenset({
    "regions", "sources", "hotels", "hotel_facades", "room_types", "buildings",
    "osm_features", "characteristics", "characteristic_versions", "agent_runs",
    "agent_steps", "claims", "facts", "conflicts", "questions", "answers",
    "change_events", "jobs", "tool_cache", "llm_cache", "search_queries",
    "eval_labels", "eval_results", "expectation_passports", "passport_items",
})


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=lambda obj: obj.isoformat() if isinstance(obj, date | datetime) else str(obj))


class Repository:
    def __init__(self, db_url: str = DB_URL):
        self.db_url = db_url

    @staticmethod
    def table(name: str) -> sql.Identifier:
        if name not in TABLES:
            raise ValueError(f"Unknown table: {name}")
        return sql.Identifier(name)

    def all(self, table: str) -> list[dict]:
        query = sql.SQL("SELECT payload FROM {} ORDER BY created_at, id").format(self.table(table))
        with psycopg.connect(self.db_url) as conn:
            return [row[0] for row in conn.execute(query)]

    def get(self, table: str, id: str) -> dict | None:
        query = sql.SQL("SELECT payload FROM {} WHERE id=%s").format(self.table(table))
        with psycopg.connect(self.db_url) as conn:
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
        with psycopg.connect(self.db_url) as conn:
            return [row[0] for row in conn.execute(query, params)]

    def insert(self, table: str, value: dict) -> dict:
        table_sql = self.table(table)
        if table == "claims":
            value = Claim.model_validate(value).model_dump(mode="json")
        query = sql.SQL("INSERT INTO {} (id,payload) VALUES (%s,%s)").format(table_sql)
        with psycopg.connect(self.db_url) as conn:
            conn.execute(query, (value["id"], Jsonb(value, dumps=dump_json)))
        return value

    def upsert(self, table: str, value: dict) -> dict:
        if table == "claims":
            raise ValueError("Claims are append-only; use add_claim")
        query = sql.SQL("""INSERT INTO {} (id,payload) VALUES (%s,%s)
            ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload, updated_at=now()""").format(self.table(table))
        with psycopg.connect(self.db_url) as conn:
            conn.execute(query, (value["id"], Jsonb(value, dumps=dump_json)))
        return value

    def add_claim(self, claim: Claim | dict) -> dict:
        value = claim.model_dump(mode="json") if isinstance(claim, Claim) else claim
        return self.insert("claims", value)

    def upsert_many(self, table: str, values: list[dict]) -> None:
        if not values:
            return
        if table == "claims":
            raise ValueError("Claims are append-only")
        query = sql.SQL("""INSERT INTO {} (id,payload) VALUES (%s,%s)
            ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload, updated_at=now()""").format(self.table(table))
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cursor:
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

    def insert_claims(self, values: list[Claim | dict]) -> list[dict]:
        validated = [Claim.model_validate(value).model_dump(mode="json") for value in values]
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cursor:
            cursor.executemany("INSERT INTO claims(id,payload) VALUES(%s,%s)",
                               [(value["id"], Jsonb(value)) for value in validated])
        return validated

    def replace_facts(self, hotel_id: str, values: list[dict]) -> None:
        # Atomic materialized-view replacement removes obsolete floor-range keys.
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cursor:
            cursor.execute("DELETE FROM facts WHERE payload->>'hotel_id'=%s",(hotel_id,))
            cursor.executemany("INSERT INTO facts(id,payload) VALUES(%s,%s)",
                               [(value["id"],Jsonb(value,dumps=dump_json)) for value in values])

    def replace_materialized_facts(self, hotel_ids: list[str], values: list[dict]) -> None:
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cursor:
            cursor.execute("CREATE TEMP TABLE resolved_import(id text PRIMARY KEY,payload jsonb NOT NULL) ON COMMIT DROP")
            with cursor.copy("COPY resolved_import(id,payload) FROM STDIN") as copy:
                for value in values:
                    copy.write_row((value["id"], dump_json(value)))
            cursor.execute("DELETE FROM facts WHERE payload->>'hotel_id'=ANY(%s)", (hotel_ids,))
            cursor.execute("INSERT INTO facts(id,payload) SELECT id,payload FROM resolved_import")

    def count(self, table: str) -> int:
        with psycopg.connect(self.db_url) as conn:
            return conn.execute(sql.SQL("SELECT count(*) FROM {}").format(self.table(table))).fetchone()[0]

    def healthy(self) -> bool:
        with psycopg.connect(self.db_url, connect_timeout=3) as conn:
            return conn.execute("SELECT 1").fetchone()[0] == 1

    def search_facts(self, characteristic_ids: list[str] | None = None, month: int | None = None) -> list[dict]:
        # Keep evidence references, quotes and limitations; omit repeated raw calculation payloads.
        predicates, params = [], []
        if characteristic_ids is not None:
            predicates.append("payload->>'characteristic_id'=ANY(%s)")
            params.append(characteristic_ids)
        if month is not None:
            predicates.append("(payload#>>'{scope,month}' IS NULL OR (payload#>>'{scope,month}')::int=%s)")
            params.append(month)
        where = " AND ".join(predicates) if predicates else "true"
        query = """SELECT (payload-'evidence') || jsonb_build_object('evidence', COALESCE(
            (SELECT jsonb_agg(evidence-'inputs'-'output') FROM jsonb_array_elements(payload->'evidence') evidence),
            '[]'::jsonb)) FROM facts WHERE """+where
        with psycopg.connect(self.db_url) as conn:
            return [row[0] for row in conn.execute(query,params)]


@lru_cache
def get_repo() -> Repository:
    return Repository()

