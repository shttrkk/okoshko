"""Local demo hotelier cabinet and the preview → confirm evidence boundary."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import timedelta
from threading import RLock

import jsonschema
from fastapi import APIRouter, Depends, Header, HTTPException
from psycopg.errors import UniqueViolation

from backend.agents.answer_parser import parse_answer
from backend.agents.interviewer import create_question, generate_questions, stable_id
from backend.agents.resolver import resolve_hotel
from backend.contracts import (
    AgentRun,
    AgentStep,
    Answer,
    AnswerRequest,
    AskRequest,
    Claim,
    Evidence,
    LoginRequest,
    Question,
    Source,
    now,
)
from backend.db.repository import get_repo
from backend.llm.client import LLMClient
from backend.sim.hotelier import simulate_answer

router = APIRouter(prefix="/api", tags=["hotelier"])
_SESSION_KEY = secrets.token_bytes(32)
_CONFIRM_LOCK = RLock()
_REPO = Depends(get_repo)


def require_hotelier(authorization: str | None = Header(default=None)) -> str:
    try:
        scheme, token = (authorization or "").split(" ", 1)
        username, expires, nonce, signature = token.split(".")
        payload = f"{username}.{expires}.{nonce}"
        expected = hmac.new(_SESSION_KEY, payload.encode(), hashlib.sha256).hexdigest()
        valid = scheme.lower() == "bearer" and username == "hotelier" and int(expires) > time.time() and hmac.compare_digest(signature, expected)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise HTTPException(401, "Войдите в демо-кабинет отельера", headers={"WWW-Authenticate": "Bearer"})
    return "hotelier"


_AUTH = Depends(require_hotelier)


def _get(repo, table: str, item_id: str, label: str):
    value = repo.get(table, item_id)
    if value is None:
        raise HTTPException(404, label + " не найден")
    return value


@router.post("/hotelier/login")
def login(request: LoginRequest):
    if not (hmac.compare_digest(request.username.encode(), b"hotelier") and hmac.compare_digest(request.password.encode(), b"hotelier123")):
        raise HTTPException(401, "Неверный логин или пароль")
    payload = f"hotelier.{int(time.time())+12*3600}.{secrets.token_hex(12)}"
    signature = hmac.new(_SESSION_KEY, payload.encode(), hashlib.sha256).hexdigest()
    return {"token": payload + "." + signature, "username": "hotelier", "synthetic": True}


@router.get("/hotelier/{hotel_id}")
def cabinet(hotel_id: str, repo=_REPO, _user=_AUTH):
    hotel = _get(repo, "hotels", hotel_id, "Отель")
    facts = repo.find("facts", hotel_id=hotel_id)
    questions = generate_questions(hotel_id, repo)
    strengths = [{"characteristic_id": fact["characteristic_id"], "value": fact["value"],
                  "confidence": fact.get("confidence", 0), "synthetic": fact.get("synthetic", False),
                  "demand_synthetic": True} for fact in facts
                 if fact.get("status") in {"confirmed", "computed"} and isinstance(fact.get("value"), dict) and fact["value"].get("available") is True]
    return {"hotel": hotel, "facts": facts, "claims": repo.find("claims", hotel_id=hotel_id),
            "questions": [q.model_dump(mode="json") for q in questions],
            "conflicts": repo.find("conflicts", hotel_id=hotel_id),
            "passports": repo.find("expectation_passports", hotel_id=hotel_id),
            "strengths": strengths, "demand_synthetic": True}


@router.post("/questions", response_model=Question)
def ask(request: AskRequest, repo=_REPO):
    _get(repo, "hotels", request.hotel_id, "Отель")
    spec = _get(repo, "characteristics", request.characteristic_id, "Характеристика")
    if not spec.get("hotelier_answerable", True):
        raise HTTPException(422, "Характеристика не предусматривает вопрос отельеру")
    return create_question(request.hotel_id, request.characteristic_id, repo)


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


@router.post("/questions/{question_id}/answer", response_model=Answer)
def answer_question(question_id: str, request: AnswerRequest, repo=_REPO, _user=_AUTH):
    question = Question.model_validate(_get(repo, "questions", question_id, "Вопрос"))
    if question.status != "pending":
        raise HTTPException(409, "Вопрос уже закрыт; создайте новый вопрос для исправления")
    if request.confirm:
        raise HTTPException(422, "Сначала проверьте предпросмотр, затем подтвердите /api/answers/{id}/confirm")
    answer = parse_answer(question, request.text, synthetic=request.synthetic, llm=LLMClient(repo))
    return _save_preview(answer, question, repo)


@router.post("/questions/{question_id}/simulate", response_model=Answer)
def simulate_question(question_id: str, repo=_REPO, _user=_AUTH):
    question = Question.model_validate(_get(repo, "questions", question_id, "Вопрос"))
    if question.status != "pending":
        raise HTTPException(409, "Вопрос уже закрыт")
    return _save_preview(simulate_answer(question, repo), question, repo)


def confirm_answer(answer_id: str, repo) -> Answer:
    """Deterministic claim IDs recover partial writes and make retries idempotent."""
    with _CONFIRM_LOCK:
        answer = Answer.model_validate(_get(repo, "answers", answer_id, "Ответ"))
        question = Question.model_validate(_get(repo, "questions", answer.question_id, "Вопрос"))
        if answer.confirmed:
            if question.status != "answered":
                question.status = "answered"
                repo.upsert("questions", question.model_dump(mode="json"))
            return answer
        if not answer.updates:
            raise HTTPException(422, "В ответе нет подтверждаемых значений; требуется уточнение")
        # Validate the entire preview before the first durable claim write.
        specs = {}
        for update in answer.updates:
            spec = _get(repo, "characteristics", update.characteristic_id, "Характеристика")
            try:
                jsonschema.validate(update.value, spec["value_schema"])
            except jsonschema.ValidationError as exc:
                raise HTTPException(422, "Значение не соответствует схеме характеристики: " + update.characteristic_id) from exc
            specs[update.characteristic_id] = spec
        source_id = "hotelier_simulator" if answer.synthetic else "hotelier"
        if not repo.get("sources", source_id):
            source = Source(id=source_id, name="Синтетический отельер (демо)" if answer.synthetic else "Заявления отельера",
                            kind="simulation" if answer.synthetic else "hotelier", url="/api/answers/" + answer.id + "/confirm",
                            license="Local demo data", attribution="О!кно: локальный кабинет отельера", observed_at=now(), retrieved_at=now(),
                            notes="Демонстрация; не реальные сведения о гостиницах" if answer.synthetic else "Пользовательский ответ с цитатами; без независимых измерений")
            repo.upsert("sources", source.model_dump(mode="json"))
        created = []
        for index, update in enumerate(answer.updates):
            claim_id = stable_id("answer_claim", answer.id, index)
            created.append(claim_id)
            if repo.get("claims", claim_id):
                continue
            at = answer.created_at
            prior = [claim for claim in repo.find("claims", hotel_id=question.hotel_id)
                     if claim["characteristic_id"] == update.characteristic_id and claim["source"] == source_id
                     and claim.get("scope", {}) == update.scope.model_dump(mode="json")]
            prior.sort(key=lambda claim: claim["observed_at"], reverse=True)
            limitations = ["Заявление отельера, а не независимое измерение"]
            if answer.synthetic:
                limitations.append("Синтетический сценарий; не сведения о реальном отеле")
            if update.characteristic_id == "soundproofing" and update.value.get("available") is None:
                limitations.append("Тип окон, шторы и кондиционер не доказывают шумоизоляцию")
            evidence = Evidence(id=stable_id("answer_evidence", answer.id, index), kind="simulation" if answer.synthetic else "hotelier",
                                label="Синтетический ответ отельера" if answer.synthetic else "Ответ отельера: дословная цитата",
                                source_ids=[source_id], quote=update.quote, message_id=answer.id,
                                inputs={"question_id": question.id, "answer_id": answer.id},
                                output={"characteristic_id": update.characteristic_id, "value": update.value}, limitations=limitations)
            spec = specs[update.characteristic_id]
            claim = Claim(id=claim_id, hotel_id=question.hotel_id, characteristic_id=update.characteristic_id,
                          spec_version=spec.get("version", 1), scope=update.scope, value=update.value,
                          source=source_id, method="hotelier_answer@1", evidence=[evidence], confidence=min(update.confidence, .85),
                          observed_at=at, valid_until=at+timedelta(days=spec.get("ttl_days", 180)),
                          supersedes=prior[0]["id"] if prior else None, synthetic=answer.synthetic)
            try:
                repo.add_claim(claim)
            except UniqueViolation:
                if not repo.get("claims", claim_id):
                    raise
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
            run.steps.append(AgentStep(run_id=run.id, seq=len(run.steps), agent="resolver", type="confirmation", name="Ответ подтверждён; факты обновлены",
                                       input={"answer_id": answer.id}, output={"created_claim_ids": created, "synthetic": answer.synthetic},
                                       rationale="Цитаты сохранены в неизменяемых claims; Resolver обновил facts", evidence_refs=[c for c in created], mode=answer.parser))
            repo.upsert("agent_runs", run.model_dump(mode="json"))
        return answer


@router.post("/answers/{answer_id}/confirm", response_model=Answer)
def confirm(answer_id: str, repo=_REPO, _user=_AUTH):
    return confirm_answer(answer_id, repo)
