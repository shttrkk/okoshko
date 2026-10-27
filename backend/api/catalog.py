"""Read APIs use only durable facts and precomputed local geometry."""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from backend.catalogue import annotate, load_reviews, searchable
from backend.contracts import (
    CharacteristicSpec,
    ComparisonRequest,
    Fact,
    FactEvidence,
    Hotel,
    HotelDetail,
    LabelRequest,
    ParsedQuery,
    SearchRequest,
    SearchResult,
    Source,
    now,
    uid,
)
from backend.coverage import region_coverage
from backend.db.repository import get_repo, search_fact
from backend.llm.client import LLMClient
from backend.quality import current_value, known_value
from backend.settings import DATA
from backend.tools.registry import Registry
from backend.tracing import Trace

router = APIRouter(prefix="/api", tags=["catalog"])
_REPO = Depends(get_repo)
_INSTANCE = uid()


def runtime_dir() -> Path:
    return Path(os.getenv("RUNTIME_DIR", str(DATA.parent / "runtime")))


def required(repo, table, item_id, label="Объект"):
    value = repo.get(table, item_id)
    if value is None:
        raise HTTPException(404, label + " не найден")
    return value


def catalog(repo):
    return [CharacteristicSpec.model_validate(value) for value in repo.all("characteristics")
            if value.get("status", "active") == "active"]


def searchable_hotel(repo, hotel_id: str):
    hotel = required(repo, 'hotels', hotel_id, 'Отель')
    if not searchable(hotel):
        raise HTTPException(422, 'Объект временно скрыт из подбора: идентичность или координаты требуют проверки. Основания — /data/seed/catalogue_reviews.json')
    return hotel


def search_coverage(rows: list[dict], specs: list[CharacteristicSpec], hotel_count: int,
                    parsed: ParsedQuery) -> dict[str, dict]:
    """Coverage of the requested fields; a known sibling field is not evidence."""
    coverage = region_coverage(rows, specs, hotel_count)
    requested = defaultdict(set)
    defaults = {spec.id: spec.search.field for spec in specs}
    for criterion in [*parsed.must, *parsed.nice, *parsed.no_data]:
        if criterion.characteristic_id in defaults:
            requested[criterion.characteristic_id].add(criterion.field or defaults[criterion.characteristic_id])
    known = defaultdict(set)
    at = now()
    for row in rows:
        if row["characteristic_id"] not in requested or not current_value({**row, "value": True}, at):
            continue
        for field in requested[row["characteristic_id"]]:
            value = row.get("value")
            if isinstance(value, dict):
                for part in field.split("."):
                    value = value.get(part) if isinstance(value, dict) else None
            elif field != "available" and (isinstance(value, bool) or not isinstance(value, int | float)):
                value = None
            if known_value(value) or isinstance(value, list):
                known[row["characteristic_id"], field].add(row["hotel_id"])
    for spec in specs:
        coverage[spec.id]["search_field"] = spec.search.field
        coverage[spec.id]["fields"] = {}
        for field in requested[spec.id]:
            count = len(known[spec.id, field])
            status = ("available" if count == hotel_count else "partial") if count else (
                "awaiting_hotels" if spec.hotelier_answerable else "no_data")
            coverage[spec.id]["fields"][field] = {"status": status}
    return coverage


def read_geo(hotel_id: str, repo, *, hotel_row: dict | None = None, events: list[dict] | None = None,
             fact_rows: list[dict] | None = None) -> dict:
    hotel = hotel_row if hotel_row is not None else required(repo, "hotels", hotel_id, "Отель")
    if not searchable(hotel):
        return {'hotel_id': hotel_id, 'facades': [],
                'limitations': ['Геопривязка исходного объекта требует проверки; модель не используется для выбора отеля.']}
    if "/" in hotel_id or "\\" in hotel_id or hotel_id in {".", ".."}:
        raise HTTPException(422, "Некорректный ID отеля")
    # A change stores the exact durable artifact; do not silently show the old map.
    if events is None:
        changes = repo.find("change_events", hotel_id=hotel_id)
        if not changes:
            changes = [row for row in repo.all("change_events") if hotel_id in row.get("affected_hotel_ids", [])]
    else:
        changes = [row for row in events if row.get("hotel_id") == hotel_id or hotel_id in row.get("affected_hotel_ids", [])]
    changes = sorted(changes, key=lambda row: row.get("detected_at", ""), reverse=True)
    paths = []
    for event in changes[:1]:
        artifact = event.get("payload", {}).get("geo_artifact", "")
        if artifact.startswith("/data/runtime/geo/"):
            filename = Path(artifact).name
            paths.append(runtime_dir() / "geo" / filename)
        elif artifact.startswith("/data/geo/"):
            # A shipped, precomputed synthetic change artifact.
            paths.append(DATA / "geo" / Path(artifact).name)
    paths.extend([runtime_dir() / "geo" / (hotel_id + ".json"), DATA / "geo" / (hotel_id + ".json")])
    for path in paths:
        if path.is_file():
            try:
                document = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            geo = document.get("geo", document)
            # Claims are exposed separately and can be large; indexes are private calculation inputs.
            public = {key: value for key, value in geo.items()
                      if key not in {"claims", "dependency_building_ids", "profile_arrays", "spatial_index", "building_index"}}
            from backend.nearby import linked_places
            facts = fact_rows if fact_rows is not None else repo.find("facts", hotel_id=hotel_id)
            public["nearby"] = linked_places(facts, public, DATA)
            return public
    return {"hotel_id": hotel_id, "center": [hotel["lon"], hotel["lat"]], "facades": [],
            "limitations": ["Георасчёт для этого отеля ещё не подготовлен; отсутствие данных не означает отсутствие вида."]}


def details(hotel_ids: list[str], repo, *, text: str = "", month: int | None = None,
            parsed: ParsedQuery | None = None, compact: bool = False) -> list[HotelDetail]:
    if month is not None and month not in range(1, 13):
        raise HTTPException(422, "Месяц должен быть от 1 до 12")
    from backend.agents.explainer import disappointments, recommend_room
    specs = catalog(repo) if text or parsed is not None else []
    if text and parsed is None:
        from backend.agents.query_parser import parse_query
        parsed = parse_query(SearchRequest(text=text), specs)
        if month is not None:
            parsed.month = month
    if hasattr(repo, "catalog_details"):
        rows = repo.catalog_details(hotel_ids, compact=compact)
    else:
        rows = {table: [row for hotel_id in hotel_ids for row in repo.find(table, hotel_id=hotel_id)]
                for table in ("facts", "conflicts", "questions")}
        rows["hotels"] = [required(repo, "hotels", hotel_id, "Отель") for hotel_id in hotel_ids]
        rows["claims"] = [] if compact else [row for hotel_id in hotel_ids for row in repo.find("claims", hotel_id=hotel_id)]
        rows["change_events"] = [] if compact else repo.all("change_events")
    if compact:
        rows = {**rows, "claims": [], "change_events": []}
    hotels_by_id = {row["id"]: row for row in rows["hotels"]}
    if any(hotel_id not in hotels_by_id for hotel_id in hotel_ids):
        raise HTTPException(404, "Отель не найден")
    grouped = {}
    for table in ("facts", "claims", "conflicts", "questions"):
        grouped[table] = defaultdict(list)
        for row in rows[table]:
            if compact and table == "facts":
                row = search_fact(row)
            grouped[table][row["hotel_id"]].append(row)
    result = []
    for hotel_id in hotel_ids:
        hotel = Hotel.model_validate(annotate(hotels_by_id[hotel_id]))
        eligible = searchable(hotel)
        facts = [Fact.model_validate(row) for row in grouped["facts"][hotel_id]]
        verdicts = []
        if parsed is not None and eligible:
            from backend.agents.ranker import rank_hotels
            hits = rank_hotels([hotel], facts, parsed, specs)
            verdicts = hits[0].verdicts if hits else []
        public_facts = [fact.model_copy(update={"evidence": []}) for fact in facts] if compact else facts
        result.append(HotelDetail(hotel=hotel, facts=public_facts, claims=grouped["claims"][hotel_id],
                                  conflicts=grouped["conflicts"][hotel_id], questions=grouped["questions"][hotel_id],
                                  verdicts=verdicts, room_recommendation=recommend_room(facts, parsed, hotel=hotel) if eligible else 'Идентичность или координаты объекта требуют проверки',
                                  disappointments=disappointments(facts, parsed, hotel=hotel) if eligible else ['Модель исходной точки не используется для выбора номера'],
                                  geo={} if compact else read_geo(hotel_id, repo, hotel_row=hotels_by_id[hotel_id],
                                                               events=rows["change_events"], fact_rows=grouped["facts"][hotel_id]),
                                  evidence_deferred=compact, geometry_deferred=compact))
    return result


def detail(hotel_id: str, repo, **kwargs) -> HotelDetail:
    return details([hotel_id], repo, **kwargs)[0]


def compact_payload(value: HotelDetail) -> dict:
    """Omit optional empty evidence fields; validators restore their defaults."""
    payload = value.model_dump(mode="json")
    for fact in payload["facts"]:
        if fact.get("synthetic") is False:
            fact.pop("synthetic")
        if fact.get("conflicting_claim_ids") == []:
            fact.pop("conflicting_claim_ids")
        if fact.get("valid_until") is None:
            fact.pop("valid_until")
        # Aggregation metadata is available with the full evidence response.
        fact.pop("resolution", None)
        fact["scope"] = {key: field for key, field in fact["scope"].items()
                         if field is not None and (key != "type" or field != "hotel")}
        for evidence in fact["evidence"]:
            for key in ("url", "artifact", "quote", "message_id", "inputs", "output"):
                if evidence.get(key) is None or evidence.get(key) == {}:
                    evidence.pop(key, None)
    return payload


@router.get("/revision")
def revision(repo=_REPO):
    """A process-local signal for open views, with no guest or hotel data."""
    generation = repo.search_generation() if hasattr(repo, "search_generation") else 0
    return JSONResponse({"instance": _INSTANCE, "revision": generation}, headers={"Cache-Control": "no-store"})


@router.get("/status")
def status(repo=_REPO):
    manifest_path = DATA / "seed" / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    hotel_rows = repo.all('hotels')
    eligible = {hotel['id'] for hotel in hotel_rows if searchable(hotel)}
    hotel_count = len(eligible)
    specs = catalog(repo)
    rows = repo.search_facts() if hasattr(repo, "search_facts") else repo.all("facts")
    rows = [row for row in rows if row['hotel_id'] in eligible]
    coverage = region_coverage(rows, specs, hotel_count)
    return {"name": "О!кно", "mode": LLMClient(repo).mode, "version": "1.0.0",
            "hotel_count": hotel_count, "source_object_count": len(hotel_rows),
            "held_object_count": len(hotel_rows)-hotel_count, "characteristic_count": len(specs),
            "region_coverage": coverage, "no_data_characteristics": sorted(cid for cid, row in coverage.items() if row["status"] == "no_data"),
            "seed_version": manifest.get("seed_version", manifest.get("generated_at", "unknown")),
            "region_name": "Сочи: центр + Адлер–Имеретинка"}


@router.get("/hotels", response_model=list[Hotel])
def hotels(repo=_REPO):
    return [annotate(hotel) for hotel in repo.all("hotels") if searchable(hotel)]


@router.get("/hotels/{hotel_id}", response_model=HotelDetail)
def hotel(hotel_id: str, text: str = "", month: int | None = None, compact: bool = False, repo=_REPO):
    result = detail(hotel_id, repo, text=text, month=month, compact=compact)
    return JSONResponse(compact_payload(result)) if compact else result


@router.get("/hotels/{hotel_id}/facts/{fact_id}/evidence", response_model=FactEvidence)
def fact_evidence(hotel_id: str, fact_id: str, repo=_REPO):
    required(repo, "hotels", hotel_id, "Отель")
    fact = required(repo, "facts", fact_id, "Факт")
    if fact["hotel_id"] != hotel_id:
        raise HTTPException(404, "Факт не найден")
    ids = list(dict.fromkeys([*fact.get("supporting_claim_ids", []), *fact.get("conflicting_claim_ids", [])]))
    claims = repo.get_many("claims", ids) if hasattr(repo, "get_many") else [row for cid in ids if (row := repo.get("claims", cid))]
    return FactEvidence(fact=fact, claims=[row for row in claims if row["hotel_id"] == hotel_id])


@router.get("/hotels/{hotel_id}/geo")
def geo(hotel_id: str, repo=_REPO):
    return read_geo(hotel_id, repo)


@router.post("/hotels/{hotel_id}/evaluate", response_model=HotelDetail)
def evaluate_hotel(hotel_id: str, body: ParsedQuery, compact: bool = False, repo=_REPO):
    result = detail(hotel_id, repo, parsed=body, compact=compact)
    return JSONResponse(compact_payload(result)) if compact else result


@router.get("/compare")
def compare(ids: str, text: str = "", month: int | None = None, compact: bool = False, repo=_REPO):
    selected = list(dict.fromkeys(part.strip() for part in ids.split(",") if part.strip()))
    if not 2 <= len(selected) <= 4:
        raise HTTPException(422, "Для сравнения выберите от 2 до 4 разных отелей")
    for hotel_id in selected:
        searchable_hotel(repo, hotel_id)
    result = {"hotels": details(selected, repo, text=text, month=month, compact=compact), "characteristics": catalog(repo)}
    return JSONResponse({"hotels": [compact_payload(h) for h in result["hotels"]],
                         "characteristics": [spec.model_dump(mode="json") for spec in result["characteristics"]]}) if compact else result


@router.post("/compare")
def compare_parsed(body: ComparisonRequest, compact: bool = False, repo=_REPO):
    for hotel_id in body.ids:
        searchable_hotel(repo, hotel_id)
    result = {"hotels": details(body.ids, repo, parsed=body.parsed, compact=compact), "characteristics": catalog(repo)}
    return JSONResponse({"hotels": [compact_payload(h) for h in result["hotels"]],
                         "characteristics": [spec.model_dump(mode="json") for spec in result["characteristics"]]}) if compact else result


@router.post("/search", response_model=SearchResult)
def search(body: SearchRequest, repo=_REPO):
    from backend.agents.query_parser import parse_query
    from backend.agents.ranker import rank_hotels, similar_hotels
    started = time.monotonic()
    trace = Trace(repo, "search", trigger="user")
    try:
        # Capture before reading either catalog or facts. A concurrent write at
        # any point during parsing/ranking must not cache an older snapshot.
        read_generation = repo.search_generation() if hasattr(repo, "search_generation") else None
        specs = catalog(repo)
        parsed = parse_query(body, specs, llm=LLMClient(repo))
        trace.run.llm_mode = parsed.mode
        trace.step("search", "decision", "Разбор запроса", input={"text": body.text},
                   output=parsed.model_dump(mode="json"), mode=parsed.mode,
                   rationale="Текст сопоставлен с каталогом; неизвестные условия требуют уточнения")
        if "region_out_of_scope" in parsed.unmapped:
            hits = []
        else:
            all_hotel_rows = [Hotel.model_validate(row) for row in repo.all("hotels")]
            hotel_rows = [hotel for hotel in all_hotel_rows if searchable(hotel)]
            reference_id = body.similar_to or (body.parsed.similar_to if body.parsed else None)
            reference = Hotel.model_validate(required(repo, "hotels", reference_id, "Отель")) if reference_id else None
            if reference is None and not body.parsed:
                from backend.agents.similarity import reference_hotel
                reference, ambiguous = reference_hotel(body.text, all_hotel_rows)
                if ambiguous:
                    parsed.clarifications = ["Похожих названий несколько: " + ", ".join(ambiguous) + ". Уточните, какой отель вам понравился."]
            if reference is not None:
                searchable_hotel(repo, reference.id)
                parsed.similar_to, parsed.similar_to_name = reference.id, reference.name
                # The reference phrase names a hotel; it is not an unknown condition.
                from backend.agents.similarity import only_reference_phrase
                parsed.unmapped = [fragment for fragment in parsed.unmapped if not only_reference_phrase(fragment, reference.name)]
                if not parsed.unmapped:
                    parsed.clarifications = [message for message in parsed.clarifications if not message.startswith(
                        ("Что важнее для поездки:", "Не удалось сопоставить с каталогом:"))]
            ids = None if reference else sorted({criterion.characteristic_id for criterion in [*parsed.must, *parsed.nice, *parsed.no_data]} | {"beach_walk"})
            # Similarity uses a September profile; explicit extra conditions
            # still require their requested month, rather than September data.
            fact_month = None if reference and (parsed.must or parsed.nice or parsed.no_data) and parsed.month != 9 else 9 if reference else parsed.month
            rows = repo.search_facts(ids, fact_month) if hasattr(repo, "search_facts") else repo.all("facts")
            eligible = {hotel.id for hotel in hotel_rows}
            rows = [row for row in rows if row['hotel_id'] in eligible]
            from backend.agents.query_rules import apply_region_coverage
            coverage = search_coverage(rows, [spec for spec in specs if ids is None or spec.id in set(ids)], len(hotel_rows), parsed)
            apply_region_coverage(parsed, coverage)
            cache_key = json.dumps({"parsed": parsed.model_dump(mode="json"), "limit": body.limit}, ensure_ascii=False, sort_keys=True)
            generation, hits = repo.search_cache_get(cache_key) if hasattr(repo, "search_cache_get") else (None, None)
            if read_generation != generation:
                hits = None
            if hits is None:
                facts = [Fact.model_validate(row) for row in rows]
                if reference is not None:
                    # Buildings of one complex share a name; they are not "similar" discoveries.
                    candidates = [hotel for hotel in hotel_rows if hotel.id == reference.id or hotel.name.casefold() != reference.name.casefold()]
                    hits = similar_hotels(reference.id, candidates, facts, specs, parsed=parsed, explain_limit=body.limit)
                else:
                    hits = rank_hotels(hotel_rows, facts, parsed, specs, explain_limit=body.limit)
                if hasattr(repo, "search_cache_put"):
                    deadlines = [fact.valid_until.timestamp() for fact in facts if fact.valid_until]
                    repo.search_cache_put(cache_key, read_generation, hits, min(deadlines) if deadlines else None)
            if reference is None:
                if not parsed.must and not parsed.nice and not parsed.no_data_must:
                    browse = ("Не нашли в запросе условий из каталога, поэтому показываем обзор отелей без подбора. "
                              "Опишите, что важно: тишина, вид на море или закат, дорога к пляжу, парковка, «в гуще событий».")
                    parsed.clarifications = list(dict.fromkeys([browse, *[c for c in parsed.clarifications if not c.startswith("Что важнее")]]))[:2]
        trace.step("search", "decision", "Ранжирование по фактам", output={"total": len(hits), "hotel_ids": [hit.hotel.id for hit in hits[:body.limit]]},
                   rationale="Обязательные условия проверены для совместимой стороны и этажей; неизвестное не засчитано как выполненное")
        trace.finish()
        result = SearchResult(parsed=parsed, results=hits[:body.limit], total=len(hits),
                              latency_ms=(time.monotonic()-started)*1000, run_id=trace.run.id, mode=parsed.mode)
        repo.insert("search_queries", {"id": uid(), "text": body.text, "parsed": parsed.model_dump(mode="json"),
                                      "results": [hit.hotel.id for hit in result.results], "latency_ms": result.latency_ms,
                                      "created_at": now().isoformat(), "run_id": trace.run.id})
        return result
    except Exception as error:
        trace.finish("failed", str(error)[:300])
        raise


@router.get("/characteristics", response_model=list[CharacteristicSpec])
def characteristics(repo=_REPO):
    return catalog(repo)


@router.get("/tools")
def tools(repo=_REPO):
    return Registry(repo).list()


@router.get("/sources", response_model=list[Source])
def sources(repo=_REPO):
    rows = repo.all("sources")
    reviews = list(load_reviews().values())
    if reviews and not any(row['id'] == 'catalogue_identity_review' for row in rows):
        observed = min(row['observed_at'] for row in reviews)
        rows.append(Source(id='catalogue_identity_review', name='Проверка идентичности объектов по первичным источникам',
                           kind='data_quality_review', url='/data/seed/catalogue_reviews.json',
                           license='OSM: ODbL; фактические цитаты официальных сайтов — права у источников; собственная методика — MIT',
                           attribution='OSM/Geofabrik и официальные сайты гостиниц и города; URL в каждой записи',
                           observed_at=observed, retrieved_at=observed,
                           notes='Экспертные некалиброванные оценки необходимости проверки. Hold не означает, что реальная гостиница не существует.').model_dump(mode='json'))
    return rows


@lru_cache(maxsize=1)
def parser_metrics():
    path = DATA / "hotelier_profiles" / "parser_eval.json"
    if not path.is_file():
        return {"synthetic": True, "evaluated": 0, "accuracy": None}
    from backend.agents.answer_parser import parse_answer
    from backend.contracts import Question
    dataset = json.loads(path.read_text())
    correct = 0
    for case in dataset["cases"]:
        cid = case["question_characteristic_id"]
        answer = parse_answer(Question(hotel_id="synthetic-evaluation", characteristic_ids=[cid], text="Уточните"), case["text"], synthetic=True)
        matching = [update for update in answer.updates if update.characteristic_id == cid]
        actual = {key: value for update in matching for key, value in update.value.items()}
        ok = not matching if case["absent"] else bool(matching) and all(actual.get(key) == value for key, value in case["expected"].items())
        ok = ok and (not case["unknown"] or cid in answer.unknown_fields)
        correct += bool(ok)
    total = len(dataset["cases"])
    return {"synthetic": True, "evaluated": total, "correct": correct, "accuracy": correct/total if total else None,
            "limitation": "Ручные синтетические регрессии парсера; не точность характеристик реальных отелей."}


@router.get("/quality")
def quality(repo=_REPO):
    from backend.quality import from_repository
    result = from_repository(repo, DATA)
    result["synthetic_parser"] = parser_metrics()
    from geo.io import exists, read_json
    path = DATA / "eval" / "filter_baseline.json"
    result["filter_comparison"] = read_json(path) if exists(path) else None
    return result


@router.get("/quality/label")
def label_queue(repo=_REPO):
    from backend.quality import read_labels
    facts_by_hotel = defaultdict(list)
    rows = repo.search_facts() if hasattr(repo, "search_facts") else repo.all("facts")
    for row in rows:
        facts_by_hotel[row["hotel_id"]].append(row)
    labels_by_hotel = defaultdict(list)
    for row in [*read_labels(DATA / "eval" / "labels.csv"), *repo.all("eval_labels")]:
        labels_by_hotel[row["hotel_id"]].append(row)
    result = []
    for hotel in repo.all("hotels"):
        facts = facts_by_hotel[hotel["id"]]
        links = list(dict.fromkeys(evidence.get("url") or evidence.get("artifact") for fact in facts
                                  for evidence in fact.get("evidence", []) if evidence.get("url") or evidence.get("artifact")))
        result.append({"hotel": hotel, "facts": facts, "evidence_links": links, "existing_labels": labels_by_hotel[hotel["id"]]})
    return result


@router.post("/quality/label")
def label(body: LabelRequest, repo=_REPO):
    required(repo, "hotels", body.hotel_id, "Отель")
    required(repo, "characteristics", body.characteristic_id, "Характеристика")
    if not body.evidence_url.strip() or not body.evidence_url.startswith(("https://", "http://", "/data/", "/api/")):
        raise HTTPException(422, "Нужна ссылка на доказательство разметки")
    value = {"id": uid(), **body.model_dump(mode="json"), "label_type": "manual", "observed_at": now().isoformat()}
    return repo.insert("eval_labels", value)
