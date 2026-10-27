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
from backend.contracts import (
    AgentRun,
    AgentStep,
    Answer,
    CharacteristicSpec,
    Claim,
    Conflict,
    Fact,
    Hotel,
    Question,
    Source,
    now,
)
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
            # Shipped YAML is the product definition: a changed file reaches existing
            # databases, but a studio publication with a newer version is kept.
            conn.execute("INSERT INTO characteristics (id,payload) VALUES (%s,%s) ON CONFLICT(id) DO UPDATE "
                         "SET payload=EXCLUDED.payload, updated_at=now() "
                         "WHERE (characteristics.payload->>'version')::int <= (EXCLUDED.payload->>'version')::int "
                         "AND characteristics.payload IS DISTINCT FROM EXCLUDED.payload",
                         (spec.id, Jsonb(spec.model_dump(mode="json"))))
        known_hotels = {row[0] for row in conn.execute("SELECT id FROM hotels")}
        if (data_dir/"seed"/"demo_hotelier_claims.jsonl").exists():
            source = Source(id="hotelier_simulator", name="Синтетический демонстрационный отельер", kind="simulation",
                            url="/data/seed/demo_hotelier_claims.jsonl", license="MIT (project-authored synthetic fixtures)",
                            attribution="О!кно: вымышленные ответы для демонстрации", observed_at=now(), retrieved_at=now(),
                            notes="Помеченные фикстуры:3 ответа о тишине и7 завершённых pets-диалогов. Не сведения, подтверждения или измерения реальных отелей.", alpha=6, beta=4)
            conn.execute("INSERT INTO sources(id,payload) VALUES(%s,%s) ON CONFLICT(id) DO NOTHING", (source.id,Jsonb(source.model_dump(mode="json"))))
        if (data_dir/"eval"/"website_claims.jsonl").exists():
            source = Source(id="hotel_website",name="Короткие цитаты с официальных сайтов отелей",kind="text_quote",
                            url="/data/eval/website_fetch_log.jsonl",license="Фактические выдержки до 25 слов на страницу; права на тексты у сайтов отелей",
                            attribution="Официальные сайты отелей: точный URL у каждой цитаты",observed_at=now(),retrieved_at=now(),
                            notes="Слабые метки с проверкой robots.txt. Не доказательство реального вида из каждого номера; не используются для оценки самих себя",alpha=5.5,beta=4.5)
            conn.execute("INSERT INTO sources(id,payload) VALUES(%s,%s) ON CONFLICT(id) DO NOTHING",(source.id,Jsonb(source.model_dump(mode="json"))))
        for path in (data_dir/"seed"/"open_claims.jsonl.gz", data_dir/"geo"/"geo_claims.jsonl.gz",
                     data_dir/"seed"/"geo_trait_claims.jsonl.gz", data_dir/"seed"/"connectivity_claims.jsonl.gz",
                     data_dir/"eval"/"website_claims.jsonl",
                     data_dir/"seed"/"demo_hotelier_claims.jsonl"):
            if path.exists():
                count = seed_claims(conn, path, known_hotels)
                logging.info("Seed evidence ledger %s: %s claims", path.name, count)
        dialogues_path = data_dir/"seed"/"demo_hotelier_dialogues.json"
        if dialogues_path.exists():
            seed_dialogues(conn, dialogues_path, known_hotels)
        cache_path = data_dir / "llm_cache.jsonl"
        if cache_path.exists():
            for line in cache_path.read_text().splitlines():
                if line.strip():
                    entry = json.loads(line)
                    conn.execute("INSERT INTO llm_cache (id,payload) VALUES (%s,%s) ON CONFLICT(id) DO NOTHING",
                                 (entry["id"], Jsonb(entry)))
    from backend.tz_seed import load_tz_seed
    logging.info("Prepared regional observations: %s", load_tz_seed(data_dir, DB_URL))
    from backend.addresses import apply_address_supplement
    logging.info("Address supplement: %s", apply_address_supplement(data_dir, DB_URL))
    logging.info("Seed ready: %s hotels", get_repo().count("hotels"))
    if get_repo().count("hotels"):
        bootstrap_poi(get_repo(),data_dir)
        logging.info("Resolver materialized %s", resolve_all(get_repo()))
        seed_collect_run(get_repo())
        from backend.agents.freshness import seed_change_event
        event = seed_change_event(get_repo(), data_dir)
        if event is not None:
            logging.info("Synthetic new-building scenario applied to %s: %s notifications", event.affected_hotel_ids[0], len(event.notifications))


# A real, completed collection trace on first start, so the agent page is not empty.
# The hotel has a website/geo contradiction, so the trace shows the dispute and the question.
SEED_COLLECT_HOTEL = "osm-node-13825400642"


def seed_collect_run(repo, hotel_id: str = SEED_COLLECT_HOTEL):
    if repo.get("hotels", hotel_id) is None:
        return None
    if any(run.get("kind") == "collect" and run.get("status") == "completed" for run in repo.all("agent_runs")):
        return None
    from backend.agents.orchestrator import collect_hotel
    run = collect_hotel(hotel_id, repo, trigger="seed")
    logging.info("Seed collection trace %s: %s steps for %s", run.id, len(run.steps), hotel_id)
    return run


POI_DEFAULT_KINDS = {"night_pharmacy": "pharmacy", "family_nearby": "family", "car_access": "car"}


def poi_plans(specs: list[CharacteristicSpec]) -> list[tuple[CharacteristicSpec, dict]]:
    """Every active characteristic computed by the local POI tool, as declared in its spec."""
    plans = []
    for spec in specs:
        if spec.status != "active":
            continue
        for method in spec.methods:
            if method.tool == "poi_nearby":
                params = dict(method.params)
                if not params.get("category"):
                    params.setdefault("kind", POI_DEFAULT_KINDS.get(spec.id, "pharmacy"))
                plans.append((spec, params))
    return plans


def bootstrap_poi(repo,data_dir):
    from backend.contracts import ToolCall, ToolContext
    from backend.tools.registry import Registry, poi_method
    from backend.tracing import Trace
    specs = [CharacteristicSpec.model_validate(row) for row in repo.all("characteristics")]
    plans = poi_plans(specs)
    characteristic_ids = {spec.id for spec, _ in plans}
    if not characteristic_ids:
        return
    if hasattr(repo, "prior_poi_claims"):
        known_claims = repo.prior_poi_claims(sorted(characteristic_ids))
    else:
        # Lightweight test/in-memory repositories retain the existing surface.
        known_claims = [claim for claim in repo.all("claims")
                        if claim["characteristic_id"] in characteristic_ids
                        and str(claim.get("method", "")).startswith("local_poi_")]
    current = {(claim["hotel_id"],claim["characteristic_id"],claim["method"]) for claim in known_claims if claim["method"].startswith("local_poi_")}
    hotels = repo.all("hotels")
    # A newer method version (changed value semantics) is recomputed and supersedes older claims.
    missing = [(hotel,spec,params) for hotel in hotels for spec,params in plans
               if (hotel["id"],spec.id,poi_method(params)) not in current]
    if not missing:
        return
    trace = Trace(repo,"backfill",trigger="seed")
    registry = Registry(repo,data_dir)
    registry._known_claims = {claim["id"]:claim for claim in known_claims}
    registry._hotels = {hotel["id"]:hotel for hotel in hotels}
    values=[]
    for hotel,spec,params in missing:
        result = registry.execute(ToolContext(hotel_id=hotel["id"],characteristic_id=spec.id,run_id=trace.run.id),ToolCall(tool="poi_nearby",params=params))
        for claim in result.claims:
            claim.spec_version = spec.version
        values.extend(result.claims)
    repo.insert_claims(values)
    trace.step("collector","tool_call","Локальные OSM POI",input={"characteristics":sorted({spec.id for _,spec,_ in missing}),"hotels":len(hotels)},
               output={"claims":len(values)},evidence_refs=[ev.id for claim in values for ev in claim.evidence],
               rationale="Близость и известные часы работы рассчитаны по сохранённому снимку; отсутствие данных остаётся unknown")
    trace.finish()


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


def seed_dialogues(conn, path: Path, known_hotels: set[str]):
    document = json.loads(path.read_text())
    if document.get("schema_version") != 1 or document.get("synthetic") is not True:
        raise ValueError("Demo dialogue envelope must be explicitly synthetic version1")
    models = {"questions": Question, "answers": Answer, "agent_runs": AgentRun, "agent_steps": AgentStep, "claims": Claim}
    records = {table: [model.model_validate(row).model_dump(mode="json") for row in document[table]] for table, model in models.items()}
    questions = {row["id"]:row for row in records["questions"]}
    answers = {row["id"]:row for row in records["answers"]}
    claims = {row["id"] for row in records["claims"]}
    runs = {row["id"] for row in records["agent_runs"]}
    if any(row["hotel_id"] not in known_hotels for table in ("questions", "claims") for row in records[table]):
        raise ValueError("Demo dialogue references a hotel outside this seed")
    if any(row["question_id"] not in questions or not row["synthetic"] or not row["confirmed"] or not set(row["created_claim_ids"]) <= claims for row in records["answers"]):
        raise ValueError("Invalid demo answer references")
    if any(not row["synthetic"] or row["source"] != "hotelier_simulator" or row["run_id"] not in runs
           or any(ev["kind"] != "simulation" or ev["message_id"] not in answers for ev in row["evidence"]) for row in records["claims"]):
        raise ValueError("Invalid demo claim provenance")
    if any(questions[answers[ev["message_id"]]["question_id"]]["hotel_id"] != row["hotel_id"]
           for row in records["claims"] for ev in row["evidence"]):
        raise ValueError("Demo claim and answer refer to different hotels")
    if any(row["run_id"] not in runs for row in records["agent_steps"]):
        raise ValueError("Orphan demo trace step")
    for table, rows in records.items():
        query = sql.SQL("INSERT INTO {}(id,payload) VALUES(%s,%s) ON CONFLICT(id) DO NOTHING").format(sql.Identifier(table))
        with conn.cursor() as cursor:
            cursor.executemany(query, [(row["id"],Jsonb(row)) for row in rows])
    logging.info("Synthetic demo dialogue seed: %s completed answers, %s claims; real hotel confirmations are not implied", len(answers), len(claims))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    load_seed()

