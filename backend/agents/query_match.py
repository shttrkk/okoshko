"""Evidence evaluation and finite room-scope intersection shared by search and UI.

A room context is a representative of a partition, not an invented room number.
Floor boundaries from facts make the search exact for closed floor intervals.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import product
from typing import Any

from backend.contracts import CharacteristicSpec, Criterion, Fact, ParsedQuery

TRUSTED_CONFIDENCE = .7
ROOM_CHARACTERISTICS = {"sea_view", "sunset_view", "mountain_view", "quiet_night", "soundproofing", "room_orientation"}
DEFAULT_FIELDS = {"beach_walk": "minutes", "climate": "water_c", "air_quality": "european_aqi", "connectivity": "download_mbps"}
MONTHLY = {"climate", "sunset_view", "morning_swim"}


@dataclass(frozen=True)
class RoomContext:
    facade: str | None = None
    floor: int = 1
    room_type: str | None = None
    month: int = 9


@dataclass
class Match:
    status: str = "unknown"
    facts: list[Fact] = field(default_factory=list)
    reason: str = "Нет данных по этому условию"
    value: Any = None

    @property
    def confidence(self) -> float:
        return min((f.confidence for f in self.facts), default=0)

    @property
    def utility(self) -> float:
        # Reliability of a statement is not P(satisfies). Unknown has a fixed prior.
        return {"yes": .6 + .4*self.confidence, "partial": .35 + .4*self.confidence,
                "unknown": .2, "no": .05*(1-self.confidence)}[self.status]


@dataclass
class Assessment:
    context: RoomContext
    matches: list[Match]
    scope_conflict: bool = False
    alternatives: dict[int, list[Fact]] = field(default_factory=dict)


def fresh(fact: Fact, at: datetime | None = None) -> bool:
    at = at or datetime.now(UTC)
    deadline = fact.valid_until
    if deadline and deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return fact.status in {"computed", "confirmed"} and (deadline is None or deadline > at)


def value_at(fact: Fact, field_name: str):
    result = fact.value
    if not isinstance(result, dict):
        return result if field_name == "available" or isinstance(result, float | int) and not isinstance(result, bool) else None
    for part in field_name.split("."):
        if not isinstance(result, dict):
            return None
        result = result.get(part)
    return result


def scope_values(fact: Fact) -> dict:
    scope = fact.scope.model_dump(exclude_none=True)
    value = fact.value if isinstance(fact.value, dict) else {}
    if not scope.get("facade") and value.get("facade") in {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}:
        scope["facade"] = value["facade"]
    # A negative value's min_floor says where the view would START, not its scope.
    if value.get("available") is True:
        if not scope.get("floor_min") and isinstance(value.get("min_floor"), int):
            scope["floor_min"] = value["min_floor"]
        if not scope.get("floor_max") and isinstance(value.get("max_floor"), int):
            scope["floor_max"] = value["max_floor"]
    if not scope.get("month") and isinstance(value.get("month"), int):
        scope["month"] = value["month"]
    return scope


def _includes(scope: dict, context: RoomContext) -> bool:
    return (not scope.get("facade") or scope["facade"] == context.facade) and (
        not scope.get("floor_min") or scope["floor_min"] <= context.floor) and (
        not scope.get("floor_max") or scope["floor_max"] >= context.floor) and (
        not scope.get("room_type") or scope["room_type"] == context.room_type) and (
        not scope.get("month") or scope["month"] == context.month)


def _compare(actual, criterion: Criterion) -> bool | None:
    target = criterion.value
    if actual is None:
        return None
    if criterion.op in {"lte", "gte"}:
        if isinstance(actual, bool) or isinstance(target, bool):
            return None
        try:
            a, b = float(actual), float(target)
            if not math.isfinite(a) or not math.isfinite(b):
                return None
            return a <= b if criterion.op == "lte" else a >= b
        except (TypeError, ValueError):
            return None
    if criterion.op == "contains":
        if isinstance(actual, str) and isinstance(target, str):
            return target.casefold() in actual.casefold()
        if isinstance(actual, list | tuple):
            return target in actual
        return None
    if isinstance(target, bool):
        return actual is target if isinstance(actual, bool) else None
    if isinstance(actual, str) and isinstance(target, str):
        return actual.casefold().replace("ё", "е") == target.casefold().replace("ё", "е")
    return actual == target


def evaluate_fact(fact: Fact, criterion: Criterion, parsed: ParsedQuery, field_name: str) -> Match:
    if not fresh(fact):
        reason = "Данные устарели; нужно перепроверить" if fact.status == "stale" or fact.valid_until else "Данные неизвестны или оспариваются"
        return Match(facts=[fact], reason=reason)
    if not fact.evidence or not fact.supporting_claim_ids:
        return Match(facts=[fact], reason="Нет цепочки подтверждающих источников")
    actual = value_at(fact, field_name)
    value = fact.value if isinstance(fact.value, dict) else {}
    result = _compare(actual, criterion)
    if result is None and value.get("available") is False and criterion.value is not False:
        result = False
    if fact.characteristic_id in MONTHLY and value.get("months") and parsed.month not in value["months"]:
        result = criterion.value is False
    incomplete = False
    if fact.characteristic_id == "pets" and result and criterion.value is True:
        if parsed.pet_weight_kg is not None:
            maximum = value.get("max_weight_kg")
            if isinstance(maximum, int | float):
                result = parsed.pet_weight_kg <= maximum
            else:
                incomplete = True
        species = value.get("species")
        requested = "dog" if any(t in parsed.text.casefold() for t in ("собак", "лабрадор")) else "cat" if any(t in parsed.text.casefold() for t in ("кошк", "котом")) else None
        if requested:
            allowed = {str(s).casefold() for s in species} if isinstance(species, list) else set()
            aliases = {"dog": {"dog", "dogs", "собака", "собаки"}, "cat": {"cat", "cats", "кошка", "кошки"}}
            if not allowed:
                incomplete = True
            elif not allowed.intersection(aliases[requested]) and not allowed.intersection({"all", "любые", "pets", "животные"}):
                result = False
    if result is None:
        if fact.characteristic_id == "soundproofing" and field_name == "available" and any(value.get(k) is not None for k in ("windows", "blackout", "air_conditioning")):
            return Match("partial", [fact], "Есть сведения об окнах, но шумоизоляция не подтверждена", actual)
        return Match(facts=[fact], reason="Нужное поле не подтверждено", value=actual)
    if result and (fact.confidence < TRUSTED_CONFIDENCE or incomplete):
        return Match("partial", [fact], "Есть предварительные данные; нужно уточнить ограничения" if incomplete else "Предварительная оценка с низкой уверенностью", actual)
    if not result and fact.confidence < TRUSTED_CONFIDENCE:
        return Match("unknown", [fact], "Модель предполагает несоответствие, но уверенности недостаточно для отказа", actual)
    return Match("yes" if result else "no", [fact], "Условие выполнено" if result else "Условие не выполняется", actual)


def _best_match(facts: list[Fact], criterion: Criterion, parsed: ParsedQuery, context: RoomContext, field_name: str) -> Match:
    scoped = [f for f in facts if _includes(scope_values(f), context)]
    if criterion.scope and not _includes(criterion.scope.model_dump(exclude_none=True), context):
        return Match(reason="Этот номер не соответствует запрошенной стороне или этажу")
    if not scoped:
        return Match(reason="Для этой стороны, этажа или месяца нет данных")
    # Scoped room calculations outrank hotel-wide marketing, including positive quotes.
    if criterion.characteristic_id in ROOM_CHARACTERISTICS and any(
        any(scope_values(f).get(k) is not None for k in ("facade", "floor_min", "floor_max", "room_type"))
        and fresh(f) and f.evidence for f in facts
    ):
        scoped = [f for f in scoped if any(scope_values(f).get(k) is not None for k in ("facade", "floor_min", "floor_max", "room_type")) or value_at(f, field_name) is not True]
    if not scoped:
        return Match(reason="Общее заявление отеля не подтверждает конкретный номер")
    evaluated = [evaluate_fact(f, criterion, parsed, field_name) for f in scoped]
    known = [m for m in evaluated if m.status != "unknown"]
    if known:
        # Within overlapping evidence, a more specific resolved fact wins.
        def specificity(m):
            return sum(scope_values(m.facts[0]).get(k) is not None for k in ("facade", "floor_min", "floor_max", "room_type", "month"))
        most = max(map(specificity, known))
        known = [m for m in known if specificity(m) == most]
        if any(m.status == "yes" for m in known) and any(m.status == "no" for m in known):
            return Match("partial", [f for m in known for f in m.facts], "Источники расходятся для одного и того же номера")
        return max(known, key=lambda m: ({"yes": 3, "no": 2, "partial": 1}[m.status], m.confidence))
    return max(evaluated, key=lambda m: m.confidence, default=Match())


def assess_hotel(facts: list[Fact], parsed: ParsedQuery, specs: list[CharacteristicSpec] | None = None) -> Assessment:
    criteria = [*parsed.must, *parsed.nice]
    fields = {s.id: s.search.field for s in specs or []}
    by_id = {c.characteristic_id: [f for f in facts if f.characteristic_id == c.characteristic_id] for c in criteria}
    relevant = [f for f in facts if f.characteristic_id in by_id]
    facades = {scope_values(f).get("facade") for f in relevant} - {None}
    room_types = {scope_values(f).get("room_type") for f in relevant} - {None}
    floors = {parsed.min_floor or 1}
    for fact in relevant:
        scope = scope_values(fact)
        if scope.get("floor_min"):
            floors.add(scope["floor_min"])
        if scope.get("floor_max") and scope["floor_max"] < 100:
            floors.add(scope["floor_max"]+1)
    for criterion in criteria:
        if criterion.scope:
            if criterion.scope.facade:
                facades.add(criterion.scope.facade)
            if criterion.scope.room_type:
                room_types.add(criterion.scope.room_type)
            if criterion.scope.floor_min:
                floors.add(criterion.scope.floor_min)
    floors = {f for f in floors if (parsed.min_floor or 1) <= f <= 100}
    contexts = [RoomContext(side, floor, room_type, parsed.month) for side, floor, room_type in product(
        [parsed.facade] if parsed.facade else sorted(facades) or [None], sorted(floors), sorted(room_types) or [None])]
    candidates = []
    alternatives: dict[int, list[Fact]] = {}
    for context in contexts:
        matches = [_best_match(by_id[c.characteristic_id], c, parsed, context,
                               c.field or fields.get(c.characteristic_id) or DEFAULT_FIELDS.get(c.characteristic_id, "available")) for c in criteria]
        candidates.append(Assessment(context, matches))
        for index, match in enumerate(matches[:len(parsed.must)]):
            if match.status in {"yes", "partial"}:
                alternatives.setdefault(index, []).extend(match.facts)
    def key(item):
        required = item.matches[:len(parsed.must)]
        yes = sum(m.status == "yes" for m in required)
        progress = sum({"yes": 1, "partial": .5, "unknown": .1, "no": 0}[m.status] for m in required)
        utility = sum(m.utility*c.weight*(2 if i < len(parsed.must) else 1) for i, (c, m) in enumerate(zip(criteria, item.matches, strict=True)))
        return yes, progress, utility, -item.context.floor
    best = max(candidates, key=key, default=Assessment(RoomContext(month=parsed.month), []))
    best.alternatives = {i: list({f.id: f for f in fs}.values()) for i, fs in alternatives.items()}
    best.scope_conflict = bool(parsed.must) and len(alternatives) == len(parsed.must) and not any(
        all(m.status in {"yes", "partial"} for m in c.matches[:len(parsed.must)]) for c in candidates)
    return best
