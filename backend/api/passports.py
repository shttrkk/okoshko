"""Durable expectation passports and explicitly local demo hotel responses."""
from __future__ import annotations

import itertools
from threading import RLock

from fastapi import APIRouter, Depends, HTTPException

from backend.agents.interviewer import stable_id
from backend.api.hotelier import _get, require_hotelier
from backend.contracts import (
    Criterion,
    ExpectationPassport,
    Fact,
    PassportAction,
    PassportCreate,
    PassportItem,
    Scope,
    now,
)
from backend.db.repository import get_repo

router = APIRouter(prefix="/api", tags=["passports"])
_LOCK = RLock()
_REPO = Depends(get_repo)
_AUTH = Depends(require_hotelier)


def scopes_compatible(scopes: list[Scope]) -> bool:
    """One room must satisfy every critical facade, floor and room type."""
    facades = {scope.facade for scope in scopes if scope.facade}
    room_types = {scope.room_type for scope in scopes if scope.room_type}
    months = {scope.month for scope in scopes if scope.month}
    minimum = max([scope.floor_min for scope in scopes if scope.floor_min] or [1])
    maximum = min([scope.floor_max for scope in scopes if scope.floor_max] or [100])
    return len(facades) <= 1 and len(room_types) <= 1 and len(months) <= 1 and minimum <= maximum


def _holds(fact: Fact, criterion: Criterion | None = None) -> bool:
    if fact.status not in {"computed", "confirmed"} or fact.value is None:
        return False
    value = fact.value
    if criterion:
        field = criterion.field or "available"
        actual = value.get(field) if isinstance(value, dict) else value
        if actual is None:
            return False
        try:
            if criterion.op == "lte":
                return not isinstance(actual, bool) and float(actual) <= float(criterion.value)
            if criterion.op == "gte":
                return not isinstance(actual, bool) and float(actual) >= float(criterion.value)
            if criterion.op == "contains":
                return criterion.value in actual
            return actual == criterion.value
        except (TypeError, ValueError):
            return False
    return value.get("available") is True if isinstance(value, dict) else value is True


def _combine_scope(wanted: Scope, actual: Scope) -> Scope | None:
    if not scopes_compatible([wanted, actual]):
        return None
    data = wanted.model_dump()
    for key in ("facade", "room_type", "month"):
        data[key] = data[key] or getattr(actual, key)
    data["floor_min"] = max(filter(None, [wanted.floor_min, actual.floor_min]), default=None)
    data["floor_max"] = min(filter(None, [wanted.floor_max, actual.floor_max]), default=None)
    data["type"] = "room_type" if data["room_type"] else "floor_range" if data["facade"] and (data["floor_min"] or data["floor_max"]) else "facade" if data["facade"] else "hotel"
    return Scope.model_validate(data)


def _fact_candidates(item: PassportItem, facts: list[Fact], criterion: Criterion | None = None):
    candidates = []
    for fact in facts:
        if fact.characteristic_id != item.characteristic_id or not _holds(fact, criterion):
            continue
        scope = _combine_scope(item.scope, fact.scope)
        if scope is not None:
            candidates.append((scope, fact))
    return sorted(candidates, key=lambda pair: -pair[1].confidence)[:12]


def _event(passport: ExpectationPassport, kind: str, text: str, **extra):
    passport.timeline.append({"id": stable_id(passport.id, kind, len(passport.timeline)), "type": kind,
                              "text": text, "at": now().isoformat(), "synthetic": True, **extra})


def _notify(passport: ExpectationPassport, text: str, *, kind="status"):
    passport.notifications.append({"id": stable_id(passport.id, kind, len(passport.notifications)),
                                   "type": kind, "channel": "local", "text": text,
                                   "at": now().isoformat(), "synthetic": True})


def create_passport(request: PassportCreate, repo) -> ExpectationPassport:
    _get(repo, "hotels", request.hotel_id, "Отель")
    if request.check_in and request.check_out and request.check_out <= request.check_in:
        raise HTTPException(422, "Дата выезда должна быть позже заезда")
    if len(request.guest_name.strip()) > 120 or not request.guest_name.strip():
        raise HTTPException(422, "Укажите имя гостя длиной до 120 символов")
    facts = [Fact.model_validate(value) for value in repo.find("facts", hotel_id=request.hotel_id)]
    criteria: dict[str, Criterion] = {}
    items = [item.model_copy(deep=True) for item in request.items]
    if not items and request.parsed:
        for critical, group in ((True, request.parsed.must), (False, request.parsed.nice)):
            for criterion in group:
                scope = criterion.scope.model_copy(deep=True) if criterion.scope else Scope()
                if request.parsed.facade and not scope.facade:
                    scope.facade = request.parsed.facade
                    scope.type = "facade"
                if request.parsed.min_floor and not scope.floor_min:
                    scope.floor_min = request.parsed.min_floor
                    if scope.facade:
                        scope.type = "floor_range"
                item = PassportItem(characteristic_id=criterion.characteristic_id, text=criterion.label, critical=critical, scope=scope)
                criteria[item.id] = criterion
                items.append(item)
    if not items:
        # Facts can prefill a passport, but an unknown is never silently invented.
        for fact in sorted(facts, key=lambda value: -value.confidence):
            if _holds(fact) and fact.characteristic_id in {"sunset_view", "quiet_night", "sea_view", "pets", "room_orientation"}:
                items.append(PassportItem(characteristic_id=fact.characteristic_id, text="Ожидание: " + fact.characteristic_id,
                                          scope=fact.scope, fact_ids=[fact.id]))
            if len(items) == 4:
                break
    if not items:
        raise HTTPException(422, "Добавьте хотя бы одно ожидание или передайте разобранный запрос")
    if len(items) > 30:
        raise HTTPException(422, "Паспорт может содержать не более 30 ожиданий")
    if len({item.id for item in items}) != len(items):
        raise HTTPException(422, "Идентификаторы ожиданий должны различаться")
    fact_by_id = {fact.id: fact for fact in facts}
    candidate_sets = []
    for item in items:
        _get(repo, "characteristics", item.characteristic_id, "Характеристика")
        if any(fid not in fact_by_id or fact_by_id[fid].characteristic_id != item.characteristic_id for fid in item.fact_ids):
            raise HTTPException(422, "Факты ожидания должны принадлежать этому отелю и характеристике")
        item.status, item.response = "pending", None
        candidates = _fact_candidates(item, facts, criteria.get(item.id))
        candidate_sets.append(candidates)
    # Find a shared room scope when several facade/floor alternatives exist.
    critical_indices = [index for index, item in enumerate(items) if item.critical]
    choices = [candidate_sets[index] or [(items[index].scope, None)] for index in critical_indices]
    selected = None
    # Bound combinations to avoid unbounded work on guest input.
    for combination in itertools.islice(itertools.product(*choices), 4096):
        if scopes_compatible([scope for scope, _fact in combination]):
            selected = dict(zip(critical_indices, combination, strict=False))
            break
    for index, item in enumerate(items):
        choice = selected.get(index) if selected else None
        if choice is None and candidate_sets[index]:
            choice = candidate_sets[index][0]
        if choice:
            item.scope, fact = choice
            item.fact_ids = [fact.id] if fact else []
        else:
            item.fact_ids = []
    passport = ExpectationPassport(hotel_id=request.hotel_id, guest_name=request.guest_name.strip(),
                                  check_in=request.check_in or (request.parsed.check_in if request.parsed else None),
                                  check_out=request.check_out or (request.parsed.check_out if request.parsed else None), items=items)
    if passport.check_in and passport.check_out and passport.check_out <= passport.check_in:
        raise HTTPException(422, "Дата выезда должна быть позже заезда")
    incompatible = not scopes_compatible([item.scope for item in items if item.critical])
    if incompatible:
        passport.status = "alternative"
        for item in passport.items:
            if item.critical:
                item.status, item.response = "alternative", "Критичные условия относятся к несовместимым сторонам или этажам; требуется альтернатива."
    _event(passport, "sent", "Паспорт ожиданий передан в локальный демо-кабинет. Бронирование синтетическое.")
    if incompatible:
        _event(passport, "scope_conflict", "Все критичные условия нельзя подтвердить для одного номера; предложите альтернативу.")
    _notify(passport, "Паспорт создан. Ответ отеля появится здесь; внешние сообщения не отправляются.")
    repo.insert("expectation_passports", passport.model_dump(mode="json"))
    for item in items:
        repo.upsert("passport_items", {**item.model_dump(mode="json"), "passport_id": passport.id,
                                      "criterion": criteria[item.id].model_dump(mode="json") if item.id in criteria else None})
    return passport


@router.post("/passports", response_model=ExpectationPassport)
def create(request: PassportCreate, repo=_REPO):
    return create_passport(request, repo)


@router.get("/passports", response_model=list[ExpectationPassport])
def list_passports(hotel_id: str | None = None, repo=_REPO):
    if hotel_id:
        _get(repo, "hotels", hotel_id, "Отель")
    return repo.find("expectation_passports", hotel_id=hotel_id) if hotel_id else repo.all("expectation_passports")


@router.get("/passports/{passport_id}", response_model=ExpectationPassport)
def get_passport(passport_id: str, repo=_REPO):
    return _get(repo, "expectation_passports", passport_id, "Паспорт")


def passport_action(passport_id: str, request: PassportAction, repo) -> ExpectationPassport:
    with _LOCK:
        passport = ExpectationPassport.model_validate(_get(repo, "expectation_passports", passport_id, "Паспорт"))
        key = stable_id("passport_action", passport.id, request.model_dump_json())
        if any(event.get("action_key") == key for event in passport.timeline):
            return passport
        if len(request.text) > 4000:
            raise HTTPException(422, "Ответ должен быть не длиннее 4000 символов")
        if request.action == "confirm":
            if not request.room_number or not request.room_number.strip() or len(request.room_number) > 20:
                raise HTTPException(422, "Для локального демо-подтверждения укажите номер комнаты")
            facts = [Fact.model_validate(value) for value in repo.find("facts", hotel_id=passport.hotel_id)]
            critical = [item for item in passport.items if item.critical]
            incompatible = not scopes_compatible([item.scope for item in critical])
            declined = []
            for item in passport.items:
                related = [fact for fact in facts if fact.characteristic_id == item.characteristic_id and scopes_compatible([item.scope, fact.scope])]
                stored = repo.get("passport_items", item.id) or {}
                criterion = Criterion.model_validate(stored["criterion"]) if stored.get("criterion") else None
                eligible = [fact for fact in related if _holds(fact, criterion)]
                known_negative = related and not eligible and any(
                    fact.status in {"computed", "confirmed"} and isinstance(fact.value, dict)
                    and (fact.value.get("available") is False or criterion is not None and
                         fact.value.get(criterion.field or "available") is not None and not _holds(fact, criterion))
                    for fact in related)
                room_mismatch = bool(eligible) and all(isinstance(fact.value, dict) and fact.value.get("rooms") and request.room_number.strip() not in [str(room) for room in fact.value["rooms"]] for fact in eligible)
                if incompatible and item.critical:
                    item.status, item.response = "alternative", "Условия несовместимы для одного номера; выберите сторону или этаж."
                elif not item.critical and not scopes_compatible([item.scope] + [required.scope for required in critical]):
                    item.status, item.response = "alternative", "Желательное условие несовместимо с критичными условиями выбранного номера."
                elif known_negative or room_mismatch:
                    item.status, item.response = "declined", "Текущие сведения не подтверждают условие для выбранного номера."
                    if item.critical:
                        declined.append(item.id)
                else:
                    item.status, item.response = "confirmed", "Явно подтверждено отельером в локальном демо; это не реальная бронь."
            passport.status = "alternative" if incompatible else "at_risk" if declined else "confirmed"
            passport.room_number = request.room_number.strip() if passport.status == "confirmed" else None
            message = (f"Локальное демо-подтверждение: номер {passport.room_number}. " + (request.text or "Критичные ожидания приняты отельером.")) if passport.status == "confirmed" else "Требуется альтернатива: критичные условия несовместимы или не выполняются."
        elif request.action == "alternative":
            if not request.text.strip():
                raise HTTPException(422, "Опишите предлагаемую альтернативу")
            passport.status = "alternative"
            passport.room_number = None
            for item in passport.items:
                if item.status != "declined":
                    item.status, item.response = "alternative", request.text.strip()
            message = "Отель предлагает альтернативу в демо: " + request.text.strip()
        else:
            if request.matched is None:
                raise HTTPException(422, "Для обратной связи укажите matched=true или false")
            source_ids = {source for item in passport.items for fid in item.fact_ids
                          for fact in [repo.get("facts", fid)] if fact
                          for evidence in fact.get("evidence", []) for source in evidence.get("source_ids", [])}
            adjustments = []
            for sid in sorted(source_ids):
                source = repo.get("sources", sid)
                if not source:
                    continue
                if key in source.get("notes", ""):
                    continue
                field = "alpha" if request.matched else "beta"
                source[field] = source.get(field, 7 if field == "alpha" else 3) + 1
                marker = "DEMO: надёжность скорректирована синтетической обратной связью, не реальной оценкой гостя."
                if marker not in source.get("notes", ""):
                    source["notes"] = (source.get("notes", "") + " " + marker).strip()
                source["notes"] += " [feedback:" + key + "]"
                repo.upsert("sources", source)
                adjustments.append({"source_id": sid, "field": field, "delta": 1, "synthetic": True})
            message = "Синтетическая обратная связь: " + ("ожидания совпали" if request.matched else "ожидания не совпали") + (". " + request.text.strip() if request.text.strip() else "")
            _event(passport, "reliability_demo", "Демонстрационное обновление надёжности источников", adjustments=adjustments)
        _event(passport, request.action, message, action_key=key, room_number=passport.room_number)
        _notify(passport, message)
        repo.upsert("expectation_passports", passport.model_dump(mode="json"))
        for item in passport.items:
            stored = repo.get("passport_items", item.id) or {}
            repo.upsert("passport_items", {**stored, **item.model_dump(mode="json"), "passport_id": passport.id})
        return passport


@router.post("/passports/{passport_id}/action", response_model=ExpectationPassport)
def action(passport_id: str, request: PassportAction, repo=_REPO, _user=_AUTH):
    return passport_action(passport_id, request, repo)
