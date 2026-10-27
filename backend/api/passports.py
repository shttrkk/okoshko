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
from backend.agents.query_match import MONTHLY, route_constraints
from backend.api.hotelier import _get
from backend.catalogue import searchable
from backend.contracts import (
    Criterion,
    ExpectationPassport,
    Fact,
    ParsedQuery,
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
_GROUP_CAVEAT = ("Подтверждаются условия размещения по отдельным запросам, а не распределение реальных номеров. "
                 "Количество свободных номеров, их наличие, вместимость, цена и соседство не подтверждены; "
                 "одинаковое положение двух запросов не означает две свободные комнаты.")


def _room_scope(parsed: ParsedQuery, index: int) -> Scope:
    requested = parsed.rooms[index].scope
    return Scope(facade=requested.facade or parsed.facade,
                 floor_min=requested.floor_min if requested.floor_max is not None else requested.floor_min or parsed.min_floor,
                 floor_max=requested.floor_max,
                 room_type=requested.room_type, month=requested.month or parsed.month)


def _group_rows(parsed: ParsedQuery, edited: list[PassportItem]):
    """Keep the criterion mapping keyed by guest index as well as characteristic."""
    generated = []
    criteria: dict[str, list[Criterion]] = {}
    metadata: dict[str, dict] = {}
    for index, room in [(None, parsed), *enumerate(parsed.rooms)]:
        scope = _room_scope(parsed, index) if index is not None else Scope(month=parsed.month)
        label = parsed.rooms[index].label if index is not None else None
        for critical, unsupported, group in ((True, False, room.must), (False, False, room.nice),
                                             (True, True, room.no_data_must), (False, True, room.no_data)):
            for criterion in group:
                # no_data may also include an already-required unsupported condition.
                if unsupported and not critical and criterion in room.no_data_must:
                    continue
                item = PassportItem(characteristic_id=criterion.characteristic_id, text=criterion.label,
                                    critical=critical, scope=scope.model_copy(deep=True),
                                    room_index=index, room_label=label)
                generated.append(item)
                criteria[item.id] = [criterion.model_copy(deep=True)]
                metadata[item.id] = {"unsupported": unsupported, "scope_only": False}
        if index is not None and not [item for item in generated if item.room_index == index]:
            position = []
            if scope.facade:
                position.append("сторона " + scope.facade)
            if scope.floor_min and scope.floor_max:
                position.append(f"этажи {scope.floor_min}–{scope.floor_max}")
            elif scope.floor_min:
                position.append(f"этаж не ниже {scope.floor_min}-го")
            elif scope.floor_max:
                position.append(f"этаж не выше {scope.floor_max}-го")
            if scope.room_type:
                position.append("запрошенный тип «" + scope.room_type + "»")
            text = "Положение: " + (", ".join(position) if position else "требует уточнения") + "; наличие отдельного номера не подтверждено"
            item = PassportItem(characteristic_id="room_orientation", text=text,
                                scope=scope.model_copy(deep=True), room_index=index, room_label=label)
            generated.append(item)
            criteria[item.id] = []
            metadata[item.id] = {"unsupported": False, "scope_only": True}
    if not edited:
        return generated, criteria, metadata
    grouped: dict[tuple[int | None, str], list[PassportItem]] = defaultdict(list)
    for item in generated:
        grouped[(item.room_index, item.characteristic_id)].append(item)
    counts = Counter((item.room_index, item.characteristic_id) for item in edited)
    positions = Counter()
    used = set()
    for item in edited:
        if item.room_index is not None:
            if item.room_index >= len(parsed.rooms):
                raise HTTPException(422, "room_index должен указывать на запрос комнаты в parsed.rooms")
            item.room_label = parsed.rooms[item.room_index].label
        elif item.room_label is not None:
            raise HTTPException(422, "Для подписи комнаты необходимо указать room_index")
        key = (item.room_index, item.characteristic_id)
        matching = grouped.get(key, [])
        if not matching:
            raise HTTPException(422, "Пункт группового паспорта должен соответствовать критерию этого room_index")
        used.add(key)
        if counts[key] == len(matching):
            matching = [matching[positions[key]]]
            positions[key] += 1
        criteria[item.id] = [criterion.model_copy(deep=True) for expected in matching for criterion in criteria[expected.id]]
        metadata[item.id] = {field: any(metadata[expected.id][field] for expected in matching)
                             for field in ("unsupported", "scope_only")}
    # An edited request cannot silently drop another guest or an uneditable unknown.
    return [*edited, *[item for item in generated if (item.room_index, item.characteristic_id) not in used]], criteria, metadata


def _scope_groups(items: list[PassportItem], grouped: bool) -> dict[int | None, list[int]]:
    groups: dict[int | None, list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        if item.critical:
            groups[item.room_index if grouped else None].append(index)
    return groups


def _incompatible_groups(items: list[PassportItem], grouped: bool) -> set[int | None]:
    return {key for key, indices in _scope_groups(items, grouped).items()
            if not scopes_compatible([items[index].scope for index in indices])}


def _group_month_applies(fact: Fact, month: int | None) -> bool:
    value = fact.value if isinstance(fact.value, dict) else {}
    months = [entry for entry in (fact.scope.month, value.get("month")) if entry is not None]
    if any(entry != month for entry in months):
        return False
    covered = value.get("months")
    if fact.characteristic_id in MONTHLY:
        return (bool(months) or isinstance(covered, list) and month in covered) and (
            not isinstance(covered, list) or month in covered)
    return True


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
    metadata: dict[str, dict] = {}
    grouped_passport = bool(request.parsed and request.parsed.rooms)
    items = [item.model_copy(deep=True) for item in request.items]
    if grouped_passport:
        items, criteria, metadata = _group_rows(request.parsed, items)
    elif any(item.room_index is not None or item.room_label is not None for item in items):
        raise HTTPException(422, "Для комнатных пунктов передайте parsed.rooms")
    if not grouped_passport and not items and request.parsed:
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
    if not grouped_passport and request.items and request.parsed:
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
        if grouped_passport and item.room_index is not None:
            wanted_scopes.append(_room_scope(request.parsed, item.room_index))
        elif grouped_passport:
            wanted_scopes.append(Scope(month=request.parsed.month))
        elif not grouped_passport and request.parsed and item.characteristic_id in ROOM_CHARACTERISTICS:
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
        unsupported = metadata.get(item.id, {}).get("unsupported", False)
        spec = {} if unsupported else _get(repo, "characteristics", item.characteristic_id, "Характеристика")
        for criterion in criteria.get(item.id, []):
            if criterion.field is None:
                criterion.field = spec.get("search", {}).get("field", "available")
        if any(fid not in fact_by_id or fact_by_id[fid].characteristic_id != item.characteristic_id for fid in item.fact_ids):
            raise HTTPException(422, "Факты ожидания должны принадлежать этому отелю и характеристике")
        item.status, item.response = "pending", None
        unverifiable = unsupported or metadata.get(item.id, {}).get("scope_only", False)
        applicable = [fact for fact in facts if _group_month_applies(fact, item.scope.month)] if grouped_passport else facts
        candidates = _fact_candidates(item, applicable, criteria.get(item.id, [])) if item.id not in scope_conflicts and not unverifiable else []
        candidate_sets.append(candidates)
    # A shared position is required within each guest request, never across guests.
    selected = {}
    for indices in _scope_groups(items, grouped_passport).values():
        choices = [candidate_sets[index] or [(items[index].scope, None)] for index in indices]
        # Bound combinations per room to avoid a cross-product of group allocations.
        for combination in itertools.islice(itertools.product(*choices), 4096):
            if scopes_compatible([scope for scope, _fact in combination]):
                selected.update(zip(indices, combination, strict=False))
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
                                  check_out=request.check_out or (request.parsed.check_out if request.parsed else None), items=items,
                                  room_requests=[room.model_copy(deep=True) for room in request.parsed.rooms] if grouped_passport else [])
    if passport.check_in and passport.check_out and passport.check_out <= passport.check_in:
        raise HTTPException(422, "Дата выезда должна быть позже заезда")
    incompatible_groups = _incompatible_groups(items, grouped_passport)
    incompatible_groups.update(item.room_index if grouped_passport else None for item in items
                               if item.critical and item.id in scope_conflicts)
    incompatible = bool(incompatible_groups)
    if incompatible:
        passport.status = "alternative"
        for item in passport.items:
            if item.critical and (item.room_index if grouped_passport else None) in incompatible_groups:
                item.status, item.response = "alternative", "Критичные условия относятся к несовместимым сторонам или этажам; требуется альтернатива."
    _event(passport, "sent", "Паспорт ожиданий передан в локальный демо-кабинет. Бронирование синтетическое.")
    if grouped_passport:
        _event(passport, "group_conditions", _GROUP_CAVEAT, room_request_count=len(passport.room_requests))
    if _trip_reviews(passport, facts) and not incompatible:
        passport.status = "at_risk"
    if incompatible:
        _event(passport, "scope_conflict", "Критичные условия внутри одного запроса комнаты несовместимы; предложите альтернативу."
               if grouped_passport else "Все критичные условия нельзя подтвердить для одного номера; предложите альтернативу.")
    _notify(passport, "Паспорт создан. Ответ отеля появится здесь; внешние сообщения не отправляются.")
    repo.insert("expectation_passports", passport.model_dump(mode="json"))
    for item in items:
        repo.upsert("passport_items", {**item.model_dump(mode="json"), "passport_id": passport.id,
                                      "criteria": [criterion.model_dump(mode="json") for criterion in criteria.get(item.id, [])],
                                      "scope_conflict": item.id in scope_conflicts,
                                      **metadata.get(item.id, {})})
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
        grouped_passport = bool(passport.room_requests)
        key = stable_id("passport_action", passport.id, request.model_dump_json())
        facts = []
        if request.action == "confirm":
            facts = [Fact.model_validate(value) for value in repo.find("facts", hotel_id=passport.hotel_id)]
            if grouped_passport:
                # Group reconfirmation must observe current facts without turning
                # a retry on an unchanged snapshot into another hotel response.
                characteristics = {item.characteristic_id for item in passport.items}
                snapshot = [(fact.id, fact.status, fact.scope.model_dump_json(), fact.value,
                             fact.valid_until.isoformat() if fact.valid_until else None,
                             fact.valid_until is None or fact.valid_until > now())
                            for fact in sorted(facts, key=lambda fact: fact.id)
                            if fact.characteristic_id in characteristics]
                key = stable_id(key, snapshot)
            elif passport.check_out or passport.check_in:
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
            if grouped_passport and request.room_number is not None and len(request.room_number) > 4000:
                raise HTTPException(422, "Общая заметка о размещении должна быть не длиннее 4000 символов")
            if not grouped_passport and (not request.room_number or not request.room_number.strip() or len(request.room_number) > 20):
                raise HTTPException(422, "Для локального демо-подтверждения укажите номер комнаты")
            critical = [item for item in passport.items if item.critical]
            incompatible_groups = _incompatible_groups(passport.items, grouped_passport)
            incompatible = bool(incompatible_groups)
            declined = []
            alternatives = []
            for item in passport.items:
                all_related = [fact for fact in facts if fact.characteristic_id == item.characteristic_id
                               and (not grouped_passport or _group_month_applies(fact, item.scope.month))]
                related = [fact for fact in all_related if (actual_scope := _effective_scope(fact)) is not None
                           and scopes_compatible([item.scope, actual_scope])]
                stored = repo.get("passport_items", item.id) or {}
                item_criteria = _stored_criteria(stored)
                eligible = [fact for fact in related if _all_hold(fact, item_criteria)]
                unverifiable = grouped_passport and (stored.get("scope_only") or stored.get("unsupported"))
                if unverifiable:
                    eligible = []
                if grouped_passport:
                    item.fact_ids = [max(eligible, key=lambda fact: fact.confidence).id] if eligible else []
                trip_end = passport.check_out or passport.check_in
                if eligible and trip_end:
                    # Prefer an equally suitable fact reviewed through the stay.
                    chosen = max(eligible, key=lambda fact: (
                        fact.valid_until is None or fact.valid_until.astimezone(UTC).date() >= trip_end, fact.confidence))
                    item.fact_ids = [chosen.id]
                negative = any(
                    any(_comparison(fact, criterion) is False for criterion in item_criteria)
                    if item_criteria else _comparison(fact) is False for fact in related)
                known_negative = not eligible and negative and not unverifiable
                conflicting = grouped_passport and not unverifiable and (
                    any(fact.status == "disputed" for fact in related) or bool(eligible) and negative)
                targeted = any((criterion.field or "available") != "available" or criterion.no_stairs or criterion.no_crossings
                               for criterion in item_criteria)
                unresolved = not eligible and (targeted or grouped_passport) and not known_negative
                scope_mismatch = bool(stored.get("scope_conflict")) or not related and any(
                    _all_hold(fact, item_criteria) for fact in all_related)
                room_mismatch = not grouped_passport and bool(eligible) and all(
                    isinstance(fact.value, dict) and fact.value.get("rooms")
                    and request.room_number.strip() not in [str(room) for room in fact.value["rooms"]] for fact in eligible)
                room_key = item.room_index if grouped_passport else None
                same_room_critical = [required for required in critical
                                      if not grouped_passport or required.room_index == item.room_index]
                if room_key in incompatible_groups and item.critical:
                    item.status, item.response = "alternative", "Условия несовместимы для одного номера; выберите сторону или этаж."
                elif not item.critical and not scopes_compatible([item.scope] + [required.scope for required in same_room_critical]):
                    item.status, item.response = "alternative", "Желательное условие несовместимо с критичными условиями выбранного номера."
                elif scope_mismatch:
                    item.status, item.response = "alternative", "Сведения относятся к другой стороне или этажам; требуется альтернатива."
                    if item.critical:
                        alternatives.append(item.id)
                elif conflicting or unresolved:
                    item.status, item.response = "at_risk", ("Сведения для этого запроса комнаты расходятся или оспариваются; требуется уточнение."
                                                             if conflicting else "Нет подтверждённых данных для проверки указанного порога или ограничения.")
                    if unverifiable:
                        item.response = "Положение и наличие номера или условие без данных требуют отдельного подтверждения; фактическое размещение не установлено."
                    if item.critical:
                        declined.append(item.id)
                elif known_negative or room_mismatch:
                    item.status, item.response = "declined", "Текущие сведения не подтверждают условие для выбранного номера."
                    if item.critical:
                        declined.append(item.id)
                else:
                    item.status, item.response = "confirmed", ("Условие этого запроса принято отельером в локальном демо; наличие отдельного номера не подтверждено."
                                                               if grouped_passport else "Явно подтверждено отельером в локальном демо; это не реальная бронь.")
            needs_review = _trip_reviews(passport, facts)
            passport.status = "alternative" if incompatible or alternatives else "at_risk" if declined or needs_review else "confirmed"
            passport.room_number = request.room_number.strip() if request.room_number and (passport.status == "confirmed" or grouped_passport) else None
            if grouped_passport:
                message = ("Локальное демо-подтверждение условий размещения. " if passport.status == "confirmed"
                           else "Условия размещения требуют альтернативы или перепроверки. ") + _GROUP_CAVEAT
                if passport.room_number:
                    message += " Общая заметка, не распределение комнат: " + passport.room_number + "."
                if request.text.strip():
                    message += " " + request.text.strip()
            else:
                message = (f"Локальное демо-подтверждение: номер {passport.room_number}. " + (request.text or "Критичные ожидания приняты отельером.")) if passport.status == "confirmed" else "Требуется альтернатива: критичные условия несовместимы или не выполняются."
            if not grouped_passport and passport.status == "at_risk" and needs_review:
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
