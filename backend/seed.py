"""Idempotent local-only seed ingestion with COPY and shared model validation."""
from __future__ import annotations

import csv
import gzip
import json
import logging
from pathlib import Path

import psycopg
import yaml
from psycopg import sql
from psycopg.types.json import Jsonb

from backend.agents.resolver import resolve_all
from backend.contracts import CharacteristicSpec, Claim, Conflict, Fact, Hotel, Question, Source
from backend.db.repository import TABLES, dump_json, get_repo
from backend.settings import DATA, DB_URL

MODELS = {"hotels": Hotel, "sources": Source, "claims": Claim, "facts": Fact,
          "conflicts": Conflict, "questions": Question, "characteristics": CharacteristicSpec}
ORDER = ("regions", "sources", "hotels", "hotel_facades", "room_types", "buildings", "osm_features",
         "characteristics", "characteristic_versions", "agent_runs", "agent_steps", "claims", "facts",
         "conflicts", "questions", "answers", "llm_cache", "tool_cache", "eval_labels", "eval_results")


def seed_csv(conn, path: Path, table: str) -> int:
    if table not in TABLES:
        raise ValueError(f"Unrecognized seed table {table}")
    model = MODELS.get(table)
    conn.execute("CREATE TEMP TABLE seed_import(id text PRIMARY KEY, payload jsonb NOT NULL) ON COMMIT DROP")
    count = 0
    with gzip.open(path, "rt", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != ["id", "payload"]:
            raise ValueError(f"{path}: expected id,payload headers")
        with conn.cursor().copy("COPY seed_import (id,payload) FROM STDIN") as copy:
            for row in reader:
                value = json.loads(row["payload"])
                if value.get("id") != row["id"]:
                    raise ValueError(f"{path}: seed id mismatch")
                if model:
                    value = model.model_validate(value).model_dump(mode="json")
                copy.write_row((row["id"], dump_json(value)))
                count += 1
    conn.execute(sql.SQL("INSERT INTO {} (id,payload) SELECT id,payload FROM seed_import ON CONFLICT (id) DO NOTHING").format(sql.Identifier(table)))
    conn.execute("DROP TABLE seed_import")
    return count


def load_seed(data_dir: Path = DATA):
    # Seed files are inserts only: restart must never overwrite guest/partner actions.
    with psycopg.connect(DB_URL) as conn:
        for table in ORDER:
            path = data_dir / "seed" / f"{table}.csv.gz"
            if path.exists():
                count = seed_csv(conn, path, table)
                logging.info("Seed %s: validated %s rows", table, count)
        for path in sorted((data_dir / "characteristics").glob("*.yaml")):
            spec = CharacteristicSpec.model_validate(yaml.safe_load(path.read_text()))
            conn.execute("INSERT INTO characteristics (id,payload) VALUES (%s,%s) ON CONFLICT(id) DO NOTHING",
                         (spec.id, Jsonb(spec.model_dump(mode="json"))))
        known_hotels = {row[0] for row in conn.execute("SELECT id FROM hotels")}
        for path in (data_dir/"seed"/"open_claims.jsonl.gz", data_dir/"geo"/"geo_claims.jsonl.gz", data_dir/"eval"/"website_claims.jsonl"):
            if path.exists():
                count = seed_claims(conn, path, known_hotels)
                logging.info("Seed evidence ledger %s: %s claims", path.name, count)
        cache_path = data_dir / "llm_cache.jsonl"
        if cache_path.exists():
            for line in cache_path.read_text().splitlines():
                if line.strip():
                    entry = json.loads(line)
                    conn.execute("INSERT INTO llm_cache (id,payload) VALUES (%s,%s) ON CONFLICT(id) DO NOTHING",
                                 (entry["id"], Jsonb(entry)))
    logging.info("Seed ready: %s hotels", get_repo().count("hotels"))
    if get_repo().count("hotels"):
        logging.info("Resolver materialized %s", resolve_all(get_repo()))


def seed_claims(conn, path: Path, known_hotels: set[str]) -> int:
    opener = gzip.open if path.suffix == ".gz" else open
    conn.execute("CREATE TEMP TABLE seed_claim_import(id text PRIMARY KEY, payload jsonb NOT NULL) ON COMMIT DROP")
    count = 0
    with opener(path, "rt", encoding="utf-8") as file, conn.cursor().copy("COPY seed_claim_import(id,payload) FROM STDIN") as copy:
        for line in file:
            if not line.strip():
                continue
            claim = Claim.model_validate_json(line)
            if claim.hotel_id not in known_hotels:
                continue
            copy.write_row((claim.id, claim.model_dump_json()))
            count += 1
    conn.execute("INSERT INTO claims(id,payload) SELECT id,payload FROM seed_claim_import ON CONFLICT(id) DO NOTHING")
    conn.execute("DROP TABLE seed_claim_import")
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    load_seed()

