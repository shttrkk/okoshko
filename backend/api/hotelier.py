"""Local demo hotelier cabinet and the preview → confirm evidence boundary."""
from __future__ import annotations

from datetime import timedelta
from threading import RLock

from fastapi import APIRouter, Depends, HTTPException, Response
from jsonschema import SchemaError, ValidationError
from psycopg.errors import UniqueViolation
from referencing.exceptions import Unresolvable

from backend.agents.answer_parser import HOTELIER_VALUE_FIELDS, ROOM_GROUP_FIELDS, parse_answer
from backend.agents.demand import search_demand
from backend.agents.interviewer import _build_question, create_question, generate_questions, stable_id
from backend.agents.resolver import resolve_hotel
from backend.contracts import (
    AgentRun,
    AgentStep,
    Answer,
    AnswerRequest,
    AskRequest,
    Claim,
    Evidence,
    FieldUpdate,
    HotelierQuestionRequest,
    HotelierSummary,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    Question,
    Scope,
    Source,
    now,
)
from backend.contracts.validation import validate_value
from backend.db.repository import get_repo
from backend.llm.client import LLMClient
from backend.security import (
    SESSION_COOKIE,
    SESSION_SECONDS,
    Principal,
    authenticate,
    authorize_hotel,
    issue_token,
    require_hotelier,
)
from backend.sim.hotelier import simulate_answer

router = APIRouter(prefix="/api", tags=["hotelier"])
_CONFIRM_LOCK = RLock()
_REPO = Depends(get_repo)


_AUTH = Depends(require_hotelier)


def _get(repo, table: str, item_id: str, label: str):
    value = repo.get(table, item_id)
    if value is None:
        raise HTTPException(404, label + " не найден")
    return value


@router.post("/hotelier/login", response_model=LoginResponse)
def login(request: LoginRequest, response: Response):
    principal = authenticate(request.username, request.password)
    token = issue_token(principal)
    # EventSource cannot supply Bearer headers. The cookie is only sent to the
    # read-only run routes; all other private endpoints still require Bearer.
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                        samesite="strict", path="/api/runs")
    response.headers["Cache-Control"] = "no-store"
    return {"token": token, "username": principal.username, "synthetic": True,
            "role": principal.role, "hotel_ids": list(principal.hotel_ids)}


@router.post("/hotelier/logout", response_model=LogoutResponse)
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/api/runs", httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"logged_out": True, "synthetic": True}


@router.get("/hotelier", response_model=list[HotelierSummary])
def hotelier_summaries(response: Response, repo=_REPO, _user=_AUTH):
    """Read existing queues only; selecting a default must not generate tasks."""
    response.headers["Cache-Control"] = "no-store"
    allowed = {hotel["id"] for hotel in repo.all("hotels") if _user.permits(hotel["id"])}
    counts = dict.fromkeys(allowed, 0)
    for question in repo.all("questions"):
        hotel_id = question.get("hotel_id")
        if hotel_id in counts and question.get("status") == "pending":
            counts[hotel_id] += 1
    return [HotelierSummary(hotel_id=hotel_id, pending_count=count)
            for hotel_id, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


@router.get("/hotelier/{hotel_id}")
def cabinet(hotel_id: str, repo=_REPO, _user=_AUTH):
    authorize_hotel(_user, hotel_id)
    hotel = _get(repo, "hotels", hotel_id, "Отель")
    facts = repo.find("facts", hotel_id=hotel_id)
    questions = generate_questions(hotel_id, repo)
    demand = search_demand(repo, hotel_id)
    strengths = [{"characteristic_id": fact["characteristic_id"], "value": fact["value"],
                  "confidence": fact.get("confidence", 0), "synthetic": fact.get("synthetic", False),
                  "demand": demand["shown_with"].get(fact["characteristic_id"], 0), "demand_synthetic": False} for fact in facts
                 if fact.get("status") in {"confirmed", "computed"} and isinstance(fact.get("value"), dict) and fact["value"].get("available") is True]
    return {"hotel": hotel, "facts": facts, "claims": repo.find("claims", hotel_id=hotel_id),
            "questions": [q.model_dump(mode="json") for q in questions],
            "answered_questions": [q for q in repo.find("questions", hotel_id=hotel_id) if q.get("status") == "answered"],
            "answers": [answer for answer in repo.all("answers") if answer.get("confirmed") and
                        (question := repo.get("questions", answer["question_id"])) and question["hotel_id"] == hotel_id],
            "conflicts": repo.find("conflicts", hotel_id=hotel_id),
            "passports": repo.find("expectation_passports", hotel_id=hotel_id),
            "strengths": strengths, "search_demand": demand, "demand_synthetic": False}


@router.post("/hotelier/{hotel_id}/questions/{characteristic_id}", response_model=Question)
def hotelier_question(hotel_id: str, characteristic_id: str, request: HotelierQuestionRequest, repo=_REPO, _user=_AUTH):
    authorize_hotel(_user, hotel_id)
    _get(repo, "hotels", hotel_id, "Отель")
    spec = _get(repo, "characteristics", characteristic_id, "Характеристика")
    if not spec.get("hotelier_answerable", True):
        raise HTTPException(422, "По этой характеристике отель пока не может уточнить сведения")
    with _CONFIRM_LOCK:
        facts = repo.find("facts", hotel_id=hotel_id)
        fact = None
        if request.fact_id is not None:
            fact = _get(repo, "facts", request.fact_id, "Факт")
            if fact.get("hotel_id") != hotel_id or fact.get("characteristic_id") != characteristic_id:
                raise HTTPException(422, "Выбранный факт относится к другому отелю или теме")
            if request.scope is not None and request.scope != Scope.model_validate(fact.get("scope", {})):
                raise HTTPException(422, "Область вопроса не совпадает с выбранным фактом")
        question = create_question(hotel_id, characteristic_id, repo, reason="low_confidence")
        if fact is not None or request.scope is not None:
            selected = [fact] if fact is not None else [item for item in facts if item.get("scope") == request.scope.model_dump(mode="json")]
            context = _build_question(hotel_id, characteristic_id, selected, spec=spec,
                                      reason=question.reason, demand=question.demand_estimate)
            question = question.model_copy(update={"text": context.text, "hypothesis": context.hypothesis,
                                                   "hypothesis_update": context.hypothesis_update,
                                                   "evidence_refs": context.evidence_refs})
            repo.upsert("questions", question.model_dump(mode="json"))
        return question


@router.post("/questions", response_model=Question)
def ask(request: AskRequest, repo=_REPO):
    from backend.agents.interviewer import QuestionScopeLimit

    _get(repo, "hotels", request.hotel_id, "Отель")
    spec = _get(repo, "characteristics", request.characteristic_id, "Характеристика")
    if not spec.get("hotelier_answerable", True):
        raise HTTPException(422, "Характеристика не предусматривает вопрос отельеру")
    try:
        # A scope aggregation must not overwrite a question concurrently closed
        # by confirm_answer. Both public paths observe the same lock ordering.
        with _CONFIRM_LOCK:
            return create_question(request.hotel_id, request.characteristic_id, repo, scope=request.scope)
    except QuestionScopeLimit as error:
        raise HTTPException(409, str(error)) from error


def _save_preview(answer: Answer, question: Question, repo) -> Answer:
    repo.insert("answers", answer.model_dump(mode="json"))
    run = AgentRun(kind="interview", hotel_id=question.hotel_id, status="waiting", llm_mode=answer.parser,
                   trigger="hotelier_preview")
    run.steps = [AgentStep(run_id=run.id, seq=0, agent="answer_parser", type="extraction", name="Предпросмотр ответа с цитатами",
                           input={"question_id": question.id, "answer_id": answer.id},
                           output={"updates": [update.model_dump(mode="json") for update in answer.updates],
                                   "unknown_fields": answer.unknown_fields, "synthetic": answer.synthetic},
                           rationale="Claims появятся только после явного подтверждения отельером", mode=answer.parser)]
    repo.insert("agent_runs", run.model_dump(mode="json"))
    return answer


@router.post("/answers/{answer_id}/revise", response_model=Question)
def revise_answer(answer_id: str, repo=_REPO, _user=_AUTH):
    """Reopen the original topic; corrections never manufacture guest demand."""
    with _CONFIRM_LOCK:
        answer = Answer.model_validate(_get(repo, "answers", answer_id, "Ответ"))
        question = Question.model_validate(_get(repo, "questions", answer.question_id, "Исходный вопрос"))
        authorize_hotel(_user, question.hotel_id)
        if not answer.confirmed:
            raise HTTPException(409, "Этот ответ ещё не подтверждён; исправьте текст в предпросмотре")
        for other in repo.find("questions", hotel_id=question.hotel_id):
            if (other["id"] != question.id and other.get("status") == "pending"
                    and other.get("characteristic_ids") == question.characteristic_ids
                    and other.get("requested_scopes", []) == [scope.model_dump(mode="json") for scope in question.requested_scopes]):
                raise HTTPException(409, "По этой теме и области уже есть открытый вопрос. Выберите его в очереди; прежний ответ сохранён в истории")
        if question.status != "pending":
            question.asked_at = now()
        question.status = "pending"
        repo.upsert("questions", question.model_dump(mode="json"))
        return question


@router.post("/questions/{question_id}/answer", response_model=Answer)
def answer_question(question_id: str, request: AnswerRequest, repo=_REPO, _user=_AUTH):
    question = Question.model_validate(_get(repo, "questions", question_id, "Вопрос"))
    authorize_hotel(_user, question.hotel_id)
    if question.status != "pending":
        raise HTTPException(409, "Вопрос уже закрыт; создайте новый вопрос для исправления")
    if request.confirm:
        raise HTTPException(422, "Сначала проверьте предпросмотр, затем подтвердите /api/answers/{id}/confirm")
    # Principal.synthetic describes the demonstration login, not authorship.
    # Only the simulator (or an explicitly marked input) supplies demo text.
    answer = parse_answer(question, request.text, synthetic=request.synthetic, llm=LLMClient(repo))
    return _save_preview(answer, question, repo)


@router.post("/questions/{question_id}/simulate", response_model=Answer)
def simulate_question(question_id: str, repo=_REPO, _user=_AUTH):
    question = Question.model_validate(_get(repo, "questions", question_id, "Вопрос"))
    authorize_hotel(_user, question.hotel_id)
    if question.status != "pending":
        raise HTTPException(409, "Вопрос уже закрыт")
    return _save_preview(simulate_answer(question, repo), question, repo)


def confirm_answer(answer_id: str, repo, *, principal: Principal) -> Answer:
    """Deterministic claim IDs recover partial writes and make retries idempotent."""
    with _CONFIRM_LOCK:
        answer = Answer.model_validate(_get(repo, "answers", answer_id, "Ответ"))
        question = Question.model_validate(_get(repo, "questions", answer.question_id, "Вопрос"))
        authorize_hotel(principal, question.hotel_id)
        if answer.confirmed:
            # A retry of an old answer must not close a question reopened for
            # correction or interrupt a new preview on that same question.
            return answer
        if question.status != "pending":
            raise HTTPException(409, "Вопрос уже закрыт другим ответом; создайте новый вопрос для исправления")
        if answer.created_at < question.asked_at:
            raise HTTPException(409, "Предпросмотр относится к прежнему ответу. Разберите исправленный текст заново")
        expected = parse_answer(question, answer.raw_text, synthetic=answer.synthetic)
        if not answer.updates and answer.disposition != "unknown":
            raise HTTPException(422, "В ответе нет подтверждаемых значений; требуется уточнение")
        if (answer.disposition != expected.disposition or
                [update.model_dump(mode="json") for update in answer.updates] != [update.model_dump(mode="json") for update in expected.updates]):
            raise HTTPException(422, "Предпросмотр не соответствует проверяемым цитатам; создайте новый ответ")
        # Validate the entire preview before the first durable claim write.
        specs = {}
        for update in answer.updates:
            fields = HOTELIER_VALUE_FIELDS.get(update.characteristic_id)
            if not fields or not isinstance(update.value, dict) or set(update.value) - fields:
                raise HTTPException(422, "Неподдерживаемые поля ответа отельера")
            groups = update.value.get("groups", [])
            if update.characteristic_id == "room_orientation" and (not isinstance(groups, list) or
                    any(not isinstance(group, dict) or set(group) - ROOM_GROUP_FIELDS for group in groups)):
                raise HTTPException(422, "Неподдерживаемые сведения о группе номеров")
            spec = _get(repo, "characteristics", update.characteristic_id, "Характеристика")
            try:
                validate_value(update.value, spec["value_schema"])
            except (ValueError, SchemaError, ValidationError, Unresolvable) as exc:
                raise HTTPException(422, "Значение не соответствует схеме характеристики: " + update.characteristic_id) from exc
            specs[update.characteristic_id] = spec
        # Corrections supersede only this question's earlier answers. Their
        # exact scopes may change; unrelated guest questions stay pending.
        previous_answer_ids = {item["id"] for item in repo.all("answers")
                               if item.get("question_id") == question.id and item.get("confirmed") and item["id"] != answer.id}
        previous_claim_ids = {claim_id for item in repo.all("answers") if item["id"] in previous_answer_ids
                              for claim_id in item.get("created_claim_ids", [])}
        source_id = "hotelier_simulator" if answer.synthetic else "hotelier"
        writes = list(answer.updates)
        retraction = answer.disposition == "unknown" and bool(previous_claim_ids)
        if retraction:
            # An explicit withdrawal is provenance, not an unknown observation
            # invented by the parser. Only this question's prior knowledge can
            # be retired; the first "Не знаю" still creates no claims.
            seen = set()
            for prior_claim in sorted(repo.find("claims", hotel_id=question.hotel_id), key=lambda item: item["id"]):
                if (prior_claim["id"] not in previous_claim_ids
                        or prior_claim["characteristic_id"] not in question.characteristic_ids
                        or not prior_claim.get("source", "").startswith("hotelier")
                        or answer.synthetic and not prior_claim.get("synthetic")):
                    continue
                key = (prior_claim["characteristic_id"], str(sorted(prior_claim.get("scope", {}).items())))
                if key in seen:
                    continue
                seen.add(key)
                cid = prior_claim["characteristic_id"]
                specs[cid] = _get(repo, "characteristics", cid, "Характеристика")
                writes.append(FieldUpdate(characteristic_id=cid, scope=prior_claim.get("scope", {}), value=None, quote=answer.raw_text))
        if writes and not repo.get("sources", source_id):
            source = Source(id=source_id, name="Синтетический отельер (демо)" if answer.synthetic else "Заявления отельера",
                            kind="simulation" if answer.synthetic else "hotelier", url="/api/answers/" + answer.id + "/confirm",
                            license="Local demo data", attribution="О!кно: локальный кабинет отельера", observed_at=now(), retrieved_at=now(),
                            notes="Демонстрация; не реальные сведения о гостиницах" if answer.synthetic else "Пользовательский ответ с цитатами; без независимых измерений")
            repo.upsert("sources", source.model_dump(mode="json"))
        created = []
        for index, update in enumerate(writes):
            claim_id = stable_id("answer_claim", answer.id, index)
            created.append(claim_id)
            if repo.get("claims", claim_id):
                continue
            at = answer.created_at
            prior = [claim for claim in repo.find("claims", hotel_id=question.hotel_id)
                     if claim["characteristic_id"] == update.characteristic_id and claim["source"] == source_id
                     and claim.get("scope", {}) == update.scope.model_dump(mode="json")]
            if retraction:
                prior = [claim for claim in prior if claim["id"] in previous_claim_ids]
            prior.sort(key=lambda claim: claim["observed_at"], reverse=True)
            limitations = ["Заявление отельера, а не независимое измерение"]
            if answer.synthetic:
                limitations.append("Синтетический сценарий; не сведения о реальном отеле")
            if update.characteristic_id == "soundproofing" and isinstance(update.value, dict) and update.value.get("available") is None:
                limitations.append("Тип окон, шторы и кондиционер не доказывают шумоизоляцию")
            evidence = Evidence(id=stable_id("answer_evidence", answer.id, index), kind="simulation" if answer.synthetic else "hotelier",
                                label="Синтетический ответ отельера" if answer.synthetic else "Ответ отельера: дословная цитата",
                                source_ids=[source_id], quote=update.quote, message_id=answer.id,
                                inputs={"question_id": question.id, "answer_id": answer.id,
                                        "question_text": question.text, "question_hypothesis": question.hypothesis,
                                        "hypothesis_update": getattr(question, "hypothesis_update", None).model_dump(mode="json")
                                        if getattr(question, "hypothesis_update", None) is not None else None},
                                output={"characteristic_id": update.characteristic_id, "value": update.value, "retraction": retraction}, limitations=limitations)
            spec = specs[update.characteristic_id]
            claim = Claim(id=claim_id, hotel_id=question.hotel_id, characteristic_id=update.characteristic_id,
                          spec_version=spec.get("version", 1), scope=update.scope, value=update.value,
                          source=source_id, method="hotelier_retraction@1" if retraction else "hotelier_answer@1", evidence=[evidence], confidence=0 if retraction else .85,
                          observed_at=at, valid_until=at+timedelta(days=spec.get("ttl_days", 180)),
                          supersedes=prior[0]["id"] if prior else None,
                          supersedes_ids=sorted(claim["id"] for claim in repo.find("claims", hotel_id=question.hotel_id)
                                                if claim["id"] in previous_claim_ids
                                                and claim["characteristic_id"] == update.characteristic_id
                                                and (not answer.synthetic or claim.get("synthetic"))),
                          synthetic=answer.synthetic)
            try:
                repo.add_claim(claim)
            except UniqueViolation:
                if not repo.get("claims", claim_id):
                    raise
        if created:
            resolve_hotel(question.hotel_id, repo)
        answer.confirmed = True
        answer.created_claim_ids = created
        repo.upsert("answers", answer.model_dump(mode="json"))
        question.status = "answered"
        repo.upsert("questions", question.model_dump(mode="json"))
        for value in repo.find("agent_runs", hotel_id=question.hotel_id):
            if value.get("trigger") != "hotelier_preview" or not any(step.get("input", {}).get("answer_id") == answer.id for step in value.get("steps", [])):
                continue
            run = AgentRun.model_validate(value)
            run.status = "completed"
            run.finished_at = now()
            run.steps.append(AgentStep(run_id=run.id, seq=len(run.steps), agent="resolver", type="confirmation",
                                       name="Ответ принят; данные остаются неизвестными" if answer.disposition == "unknown" else "Ответ подтверждён; факты обновлены",
                                       input={"answer_id": answer.id}, output={"created_claim_ids": created, "synthetic": answer.synthetic},
                                       rationale="Отель не располагает данными; новых утверждений нет" if answer.disposition == "unknown" else "Цитаты сохранены в неизменяемых claims; Resolver обновил facts",
                                       evidence_refs=[c for c in created], mode=answer.parser))
            repo.upsert("agent_runs", run.model_dump(mode="json"))
        return answer


@router.post("/answers/{answer_id}/confirm", response_model=Answer)
def confirm(answer_id: str, repo=_REPO, _user=_AUTH):
    return confirm_answer(answer_id, repo, principal=_user)
