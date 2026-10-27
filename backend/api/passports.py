"""Durable expectation passports and explicitly local demo hotel responses."""
from __future__ import annotations

import itertools
import math
import re
from collections import Counter, defaultdict
from datetime import UTC
from threading import RLock

from fastapi import APIRouter, Depends, HTTPException

from backend.agents.interviewer import stable_id
from backend.agents.query_match import route_constraints
from backend.api.hotelier import _get
from backend.catalogue import searchable
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
from backend.security import Principal, authorize_hotel, optional_principal, require_hotelier

router = APIRouter(prefix="/api", tags=["passports"])
_LOCK = RLock()
_REPO = Depends(get_repo)
_AUTH = Depends(require_hotelier)
_OPTIONAL_AUTH = Depends(optional_principal)
ROOM_CHARACTERISTICS = {"sea_view", "sunset_view", "mountain_view", "quiet_night", "soundproofing", "room_orientation"}


def scopes_compatible(scopes: list[Scope]) -> bool:
    """One room must satisfy every critical facade, floor and room type."""
    facades = {scope.facade for scope in scopes if scope.facade}
    room_types = {scope.room_type for scope in scopes if scope.room_type}
    months = {scope.month for scope in scopes if scope.month}
    minimum = max([scope.floor_min for scope in scopes if scope.floor_min] or [1])
    maximum = min([scope.floor_max for scope in scopes if scope.floor_max] or [100])
    return len(facades) <= 1 and len(room_types) <= 1 and len(months) <= 1 and minimum <= maximum


def _value_comparison(fact: Fact, criterion: Criterion | None = None) -> bool | None:
    value = fact.value
    field = criterion.field or "available" if criterion else "available"
    actual = value
    if isinstance(value, dict):
        actual = value
        for part in field.split("."):
            actual = actual.get(part) if isinstance(actual, dict) else None
        if field != "available" and value.get("available") is False:
            return False
    if actual is None:
        return None
    if criterion is None:
        return actual is True if isinstance(actual, bool) else None
    target = criterion.value
    if criterion.op in {"lte", "gte"}:
        if isinstance(actual, bool) or isinstance(target, bool):
            return None
        try:
            actual_number, target_number = float(actual), float(target)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(actual_number) or not math.isfinite(target_number):
            return None
        return actual_number <= target_number if criterion.op == "lte" else actual_number >= target_number
    if criterion.op == "contains":
        if isinstance(actual, str) and isinstance(target, str):
            return target.casefold() in actual.casefold()
        if not isinstance(actual, list | tuple):
            return None
        if isinstance(target, str):
            aliases = {"dog": {"dog", "dogs", "собака", "собаки"}, "cat": {"cat", "cats", "кошка", "кошки"}}
            allowed = {str(item).casefold() for item in actual}
            if field == "species" and target in aliases:
                return bool(allowed.intersection(aliases[target] | {"all", "любые", "pets", "животные"}))
        return target in actual
    if isinstance(target, bool):
        return actual is target if isinstance(actual, bool) else None
    if isinstance(actual, str) and isinstance(target, str):
        return actual.casefold() == target.casefold()
    return actual == target


def _comparison(fact: Fact, criterion: Criterion | None = None) -> bool | None:
    """Conjoin the numeric/boolean condition with known route constraints."""
    if fact.status not in {"computed", "confirmed"} or fact.value is None or fact.valid_until and fact.valid_until <= now():
        return None
    primary = _value_comparison(fact, criterion)
    route = route_constraints(fact, criterion) if criterion else True
    # A known violation wins over another unknown; missing tags never imply
    # that stairs or crossings are absent. Notifications reuse this predicate.
    return False if primary is False or route is False else None if primary is None or route is None else True


def _holds(fact: Fact, criterion: Criterion | None = None) -> bool:
    # Public helper is also consumed by the change-notification service.
    return _comparison(fact, criterion) is True


def _effective_scope(fact: Fact) -> Scope | None:
    """Room qualifiers inside a value must not disappear at confirmation."""
    scope = fact.scope.model_copy(deep=True)
    value = fact.value if isinstance(fact.value, dict) else {}
    side = value.get("facade")
    if scope.facade and side and scope.facade != side:
        return None
    if not scope.facade and side in {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}:
        scope.facade = side
    if value.get("available") is True:
        for attr, key in (("floor_min", "min_floor"), ("floor_max", "max_floor")):
            if getattr(scope, attr) is None and type(value.get(key)) is int:
                setattr(scope, attr, value[key])
    scope.type = "room_type" if scope.room_type else "floor_range" if scope.facade and (scope.floor_min or scope.floor_max) else "facade" if scope.facade else "hotel"
    try:
        return Scope.model_validate(scope.model_dump())
    except ValueError:
        return None


def _all_hold(fact: Fact, criteria: list[Criterion]) -> bool:
    return all(_holds(fact, criterion) for criterion in criteria) if criteria else _holds(fact)


def _stored_criteria(stored: dict) -> list[Criterion]:
    # Read earlier passports while writing the new complete list.
    values = stored.get("criteria")
    if values is None:
        values = [stored["criterion"]] if stored.get("criterion") else []
    return [Criterion.model_validate(value) for value in values]


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


def _fact_candidates(item: PassportItem, facts: list[Fact], criteria: list[Criterion]):
    candidates = []
    for fact in facts:
        if fact.characteristic_id != item.characteristic_id or not _all_hold(fact, criteria):
            continue
        actual_scope = _effective_scope(fact)
        scope = _combine_scope(item.scope, actual_scope) if actual_scope else None
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


def _trip_reviews(passport: ExpectationPassport, facts: list[Fact]) -> list[str]:
    """Current evidence is not a promise beyond its next review deadline."""
    trip_end = passport.check_out or passport.check_in
    if trip_end is None:
        return []
    by_id = {fact.id: fact for fact in facts}
    critical = []
    trip_text = (f"{passport.check_in.isoformat()}–{passport.check_out.isoformat()}"
                 if passport.check_in and passport.check_out else trip_end.isoformat())
    for item in passport.items:
        deadlines = [by_id[fid].valid_until.astimezone(UTC).date() for fid in item.fact_ids
                     if fid in by_id and by_id[fid].valid_until is not None]
        expired_for_trip = [deadline for deadline in deadlines if deadline < trip_end]
        if not expired_for_trip:
            continue
        review_by = min(expired_for_trip)
        text = (f"Нужна перепроверка до {review_by.isoformat()}: срок действия текущих данных заканчивается раньше окончания поездки "
                f"{trip_text}. Текущие сведения не гарантируют условия на будущие даты.")
        if item.status in {"pending", "confirmed", "at_risk"}:
            item.status = "at_risk"
        item.response = (item.response + " " + text) if item.response and text not in item.response else item.response or text
        if item.critical:
            critical.append(item.id)
        review_key = stable_id("trip_evidence_review", passport.id, item.id, review_by.isoformat(), trip_end.isoformat())
        if not any(event.get("review_key") == review_key for event in passport.timeline):
            _event(passport, "evidence_review", text, item_id=item.id, review_by=review_by.isoformat(),
                   trip_until=trip_end.isoformat(), review_key=review_key)
            _notify(passport, text, kind="evidence_review")
    return critical


def create_passport(request: PassportCreate, repo) -> ExpectationPassport:
    hotel = _get(repo, "hotels", request.hotel_id, "Отель")
    if not searchable(hotel):
        raise HTTPException(422, 'Объект временно скрыт из подбора: идентичность или координаты требуют проверки')
    if request.check_in and request.check_out and request.check_out <= request.check_in:
        raise HTTPException(422, "Дата выезда должна быть позже заезда")
    if len(request.guest_name.strip()) > 120 or not request.guest_name.strip():
        raise HTTPException(422, "Укажите имя гостя длиной до 120 символов")
    facts = [Fact.model_validate(value) for value in repo.find("facts", hotel_id=request.hotel_id)]
    criteria: dict[str, list[Criterion]] = {}
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
                criteria[item.id] = [criterion.model_copy(deep=True)]
                items.append(item)
    if request.items and request.parsed:
        grouped: dict[str, list[Criterion]] = defaultdict(list)
        for criterion in [*request.parsed.must, *request.parsed.nice]:
            grouped[criterion.characteristic_id].append(criterion)
        counts = Counter(item.characteristic_id for item in items)
        positions: dict[str, int] = defaultdict(int)
        for item in items:
            matching = grouped[item.characteristic_id]
            # The editor emits one row per criterion in parsed order. A single
            # merged row keeps all requirements for that characteristic.
            if counts[item.characteristic_id] == len(matching) and matching:
                matching = [matching[positions[item.characteristic_id]]]
                positions[item.characteristic_id] += 1
            criteria[item.id] = [criterion.model_copy(deep=True) for criterion in matching]
    scope_conflicts: set[str] = set()
    for item in items:
        item_criteria = criteria.setdefault(item.id, [])
        wanted_scopes = [criterion.scope for criterion in item_criteria if criterion.scope]
        if request.parsed and item.characteristic_id in ROOM_CHARACTERISTICS:
            wanted_scopes.append(Scope(facade=request.parsed.facade, floor_min=request.parsed.min_floor))
        for wanted in wanted_scopes:
            combined = _combine_scope(item.scope, wanted)
            if combined is None:
                scope_conflicts.add(item.id)
            else:
                item.scope = combined
        positive_pets = item.characteristic_id == "pets" and any(
            (criterion.field or "available") == "available" and criterion.value is True for criterion in item_criteria)
        if request.parsed and positive_pets:
            weight = max((criterion.weight for criterion in item_criteria), default=1)
            if request.parsed.pet_weight_kg is not None:
                item_criteria.append(Criterion(characteristic_id="pets", label="Допустимый вес питомца",
                    field="max_weight_kg", op="gte", value=request.parsed.pet_weight_kg, weight=weight))
            pet_text = request.parsed.text.casefold() + " " + item.text.casefold()
            for species, pattern in (("dog", r"собак|лабрадор|\bdog\b"), ("cat", r"кошк|котом|\bcat\b")):
                if re.search(pattern, pet_text):
                    item_criteria.append(Criterion(characteristic_id="pets", label="Разрешённый вид питомца",
                        field="species", op="contains", value=species, weight=weight))
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
        spec = _get(repo, "characteristics", item.characteristic_id, "Характеристика")
        for criterion in criteria.get(item.id, []):
            if criterion.field is None:
                criterion.field = spec.get("search", {}).get("field", "available")
        if any(fid not in fact_by_id or fact_by_id[fid].characteristic_id != item.characteristic_id for fid in item.fact_ids):
            raise HTTPException(422, "Факты ожидания должны принадлежать этому отелю и характеристике")
        item.status, item.response = "pending", None
        candidates = _fact_candidates(item, facts, criteria.get(item.id, [])) if item.id not in scope_conflicts else []
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
    incompatible = bool(scope_conflicts.intersection(item.id for item in items if item.critical)) or not scopes_compatible([item.scope for item in items if item.critical])
    if incompatible:
        passport.status = "alternative"
        for item in passport.items:
            if item.critical:
                item.status, item.response = "alternative", "Критичные условия относятся к несовместимым сторонам или этажам; требуется альтернатива."
    _event(passport, "sent", "Паспорт ожиданий передан в локальный демо-кабинет. Бронирование синтетическое.")
    if _trip_reviews(passport, facts) and not incompatible:
        passport.status = "at_risk"
    if incompatible:
        _event(passport, "scope_conflict", "Все критичные условия нельзя подтвердить для одного номера; предложите альтернативу.")
    _notify(passport, "Паспорт создан. Ответ отеля появится здесь; внешние сообщения не отправляются.")
    repo.insert("expectation_passports", passport.model_dump(mode="json"))
    for item in items:
        repo.upsert("passport_items", {**item.model_dump(mode="json"), "passport_id": passport.id,
                                      "criteria": [criterion.model_dump(mode="json") for criterion in criteria.get(item.id, [])],
                                      "scope_conflict": item.id in scope_conflicts})
    return passport


@router.post("/passports", response_model=ExpectationPassport)
def create(request: PassportCreate, repo=_REPO):
    return create_passport(request, repo)


@router.get("/passports", response_model=list[ExpectationPassport])
def list_passports(hotel_id: str | None = None, repo=_REPO, _user=_AUTH):
    if hotel_id:
        authorize_hotel(_user, hotel_id)
        _get(repo, "hotels", hotel_id, "Отель")
        return repo.find("expectation_passports", hotel_id=hotel_id)
    return [value for value in repo.all("expectation_passports") if _user.permits(value["hotel_id"])]


@router.get("/passports/{passport_id}", response_model=ExpectationPassport)
def get_passport(passport_id: str, repo=_REPO, _user=_OPTIONAL_AUTH):
    passport = _get(repo, "expectation_passports", passport_id, "Паспорт")
    if _user is not None:
        authorize_hotel(_user, passport["hotel_id"])
    # UUID possession is the existing guest read capability, never a write token.
    return passport


def passport_action(passport_id: str, request: PassportAction, repo, *, principal: Principal) -> ExpectationPassport:
    with _LOCK:
        passport = ExpectationPassport.model_validate(_get(repo, "expectation_passports", passport_id, "Паспорт"))
        authorize_hotel(principal, passport.hotel_id)
        key = stable_id("passport_action", passport.id, request.model_dump_json())
        facts = []
        if request.action == "confirm":
            facts = [Fact.model_validate(value) for value in repo.find("facts", hotel_id=passport.hotel_id)]
            if passport.check_out or passport.check_in:
                # Renewed evidence can be checked with the same confirm action;
                # an unchanged deadline snapshot still returns the saved result.
                characteristics = {item.characteristic_id for item in passport.items}
                deadlines = [(fact.id, fact.valid_until.isoformat() if fact.valid_until else None)
                             for fact in sorted(facts, key=lambda fact: fact.id) if fact.characteristic_id in characteristics]
                key = stable_id(key, deadlines)
        if any(event.get("action_key") == key for event in passport.timeline):
            return passport
        if len(request.text) > 4000:
            raise HTTPException(422, "Ответ должен быть не длиннее 4000 символов")
        if request.action == "confirm":
            if not request.room_number or not request.room_number.strip() or len(request.room_number) > 20:
                raise HTTPException(422, "Для локального демо-подтверждения укажите номер комнаты")
            critical = [item for item in passport.items if item.critical]
            incompatible = not scopes_compatible([item.scope for item in critical])
            declined = []
            alternatives = []
            for item in passport.items:
                all_related = [fact for fact in facts if fact.characteristic_id == item.characteristic_id]
                related = [fact for fact in all_related if (actual_scope := _effective_scope(fact)) is not None
                           and scopes_compatible([item.scope, actual_scope])]
                stored = repo.get("passport_items", item.id) or {}
                item_criteria = _stored_criteria(stored)
                eligible = [fact for fact in related if _all_hold(fact, item_criteria)]
                trip_end = passport.check_out or passport.check_in
                if eligible and trip_end:
                    # Prefer an equally suitable fact reviewed through the stay.
                    chosen = max(eligible, key=lambda fact: (
                        fact.valid_until is None or fact.valid_until.astimezone(UTC).date() >= trip_end, fact.confidence))
                    item.fact_ids = [chosen.id]
                known_negative = not eligible and any(
                    any(_comparison(fact, criterion) is False for criterion in item_criteria)
                    if item_criteria else _comparison(fact) is False for fact in related)
                targeted = any((criterion.field or "available") != "available" or criterion.no_stairs or criterion.no_crossings
                               for criterion in item_criteria)
                unresolved = not eligible and targeted and not known_negative
                scope_mismatch = bool(stored.get("scope_conflict")) or not related and any(
                    _all_hold(fact, item_criteria) for fact in all_related)
                room_mismatch = bool(eligible) and all(
                    isinstance(fact.value, dict) and fact.value.get("rooms")
                    and request.room_number.strip() not in [str(room) for room in fact.value["rooms"]] for fact in eligible)
                if incompatible and item.critical:
                    item.status, item.response = "alternative", "Условия несовместимы для одного номера; выберите сторону или этаж."
                elif not item.critical and not scopes_compatible([item.scope] + [required.scope for required in critical]):
                    item.status, item.response = "alternative", "Желательное условие несовместимо с критичными условиями выбранного номера."
                elif scope_mismatch:
                    item.status, item.response = "alternative", "Сведения относятся к другой стороне или этажам; требуется альтернатива."
                    if item.critical:
                        alternatives.append(item.id)
                elif unresolved:
                    item.status, item.response = "at_risk", "Нет подтверждённых данных для проверки указанного порога или ограничения."
                    if item.critical:
                        declined.append(item.id)
                elif known_negative or room_mismatch:
                    item.status, item.response = "declined", "Текущие сведения не подтверждают условие для выбранного номера."
                    if item.critical:
                        declined.append(item.id)
                else:
                    item.status, item.response = "confirmed", "Явно подтверждено отельером в локальном демо; это не реальная бронь."
            needs_review = _trip_reviews(passport, facts)
            passport.status = "alternative" if incompatible or alternatives else "at_risk" if declined or needs_review else "confirmed"
            passport.room_number = request.room_number.strip() if passport.status == "confirmed" else None
            message = (f"Локальное демо-подтверждение: номер {passport.room_number}. " + (request.text or "Критичные ожидания приняты отельером.")) if passport.status == "confirmed" else "Требуется альтернатива: критичные условия несовместимы или не выполняются."
            if passport.status == "at_risk" and needs_review:
                message = (f"Для номера {request.room_number.strip()} в демо нужна перепроверка критичных ожиданий до поездки. "
                           "Назначение номера не гарантирует условия на будущие даты.")
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
    return passport_action(passport_id, request, repo, principal=_user)
