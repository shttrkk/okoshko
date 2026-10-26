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
COMPASS_AZIMUTHS = {side: index * 45 for index, side in enumerate(("N", "NE", "E", "SE", "S", "SW", "W", "NW"))}


@dataclass(frozen=True)
class SunsetAspect:
    angle_deg: float
    minimum_deg: float
    maximum_deg: float
    exact_normal: bool
    window_half_angle_deg: float = 75

    @property
    def oblique(self) -> bool:
        return self.angle_deg > 45


def _finite_number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def sunset_aspect(fact: Fact) -> SunsetAspect | None:
    """Display geometry only; a compass code is a sector, never an exact normal."""
    if fact.characteristic_id != "sunset_view" or not isinstance(fact.value, dict):
        return None
    azimuth = fact.value.get("azimuth_deg")
    if not _finite_number(azimuth):
        return None
    inputs = [e.inputs for e in fact.evidence if e.kind in {"calculation", "simulation"}]
    normals = {i["facade_azimuth_deg"] % 360 for i in inputs if _finite_number(i.get("facade_azimuth_deg"))}
    windows = {i["window_half_angle_deg"] for i in inputs if _finite_number(i.get("window_half_angle_deg")) and 0 < i["window_half_angle_deg"] <= 180}
    window = next(iter(windows)) if len(windows) == 1 else 75
    if len(normals) == 1:
        normal = next(iter(normals))
        angle = abs((azimuth - normal + 180) % 360 - 180)
        return SunsetAspect(angle, angle, angle, True, window)
    side = scope_values(fact).get("facade")
    if side not in COMPASS_AZIMUTHS:
        return None
    angle = abs((azimuth - COMPASS_AZIMUTHS[side] + 180) % 360 - 180)
    return SunsetAspect(angle, max(0, angle - 22.5), min(180, angle + 22.5), False, window)


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
    condition_met: bool = False

    @property
    def supported(self) -> bool:
        return self.status == "yes" or self.status == "partial" and self.condition_met and self.confidence >= .6

    @property
    def confidence(self) -> float:
        return min((f.confidence for f in self.facts), default=0)

    @property
    def utility(self) -> float:
        # Reliability of a statement is not P(satisfies). Unknown has a fixed prior.
        utility = {"yes": .6 + .4*self.confidence, "partial": .35 + .4*self.confidence,
                   "unknown": .2, "no": .05*(1-self.confidence)}[self.status]
        # Oblique sunset is useful, but is a weaker fit than a view straight ahead.
        if self.condition_met and any((aspect := sunset_aspect(f)) and aspect.oblique for f in self.facts):
            utility *= .85
        return utility


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
    scope = {key: value for key, value in (("facade", fact.scope.facade), ("floor_min", fact.scope.floor_min),
             ("floor_max", fact.scope.floor_max), ("room_type", fact.scope.room_type), ("month", fact.scope.month)) if value is not None}
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


def route_constraints(fact: Fact, criterion: Criterion) -> bool | None:
    """Evaluate additional route requirements with three-valued AND semantics.

    Missing OSM flags never mean an accessible route. Freshness, provenance,
    confidence and the primary numeric condition are checked by the caller.
    """
    if criterion.characteristic_id != "beach_walk":
        return True
    if fact.characteristic_id != "beach_walk":
        return None
    value = fact.value if isinstance(fact.value, dict) else {}
    checks = []
    if criterion.no_stairs:
        steps = value.get("has_steps")
        checks.append(not steps if isinstance(steps, bool) else None)
    if criterion.no_crossings:
        crossings = value.get("crossings")
        if isinstance(crossings, list):
            checks.append(not crossings)
        elif _finite_number(crossings) and crossings >= 0:
            checks.append(crossings == 0)
        else:
            checks.append(None)
    return False if False in checks else None if None in checks else True


def _route_reason(fact: Fact, criterion: Criterion) -> str:
    value = fact.value if isinstance(fact.value, dict) else {}
    details = []
    if criterion.no_stairs:
        if value.get("has_steps") is True:
            details.append("на маршруте отмечены лестницы")
        elif value.get("has_steps") is not False:
            details.append("нет сведений о лестницах")
    if criterion.no_crossings:
        crossings = value.get("crossings")
        if crossings:
            details.append("на маршруте отмечены переходы")
        elif crossings is None or isinstance(crossings, bool):
            details.append("нет сведений о переходах")
    return "Маршрут " + ("без лестниц и переходов" if criterion.no_stairs and criterion.no_crossings else "без лестниц" if criterion.no_stairs else "без переходов") + " не подтверждён: " + "; ".join(details)


def evaluate_fact(fact: Fact, criterion: Criterion, parsed: ParsedQuery, field_name: str) -> Match:
    if not fresh(fact):
        reason = "Данные оспариваются" if fact.status == "disputed" else "Данные неизвестны" if fact.status == "unknown" else "Данные устарели; нужно перепроверить"
        return Match(facts=[fact], reason=reason)
    if not fact.evidence or not fact.supporting_claim_ids:
        return Match(facts=[fact], reason="Нет цепочки подтверждающих источников")
    actual = value_at(fact, field_name)
    value = fact.value if isinstance(fact.value, dict) else {}
    result = _compare(actual, criterion)
    scope = scope_values(fact)
    if fact.scope.month and value.get("month") and fact.scope.month != value["month"]:
        return Match(facts=[fact], reason="Месяц значения расходится с областью действия факта", value=actual)
    if fact.scope.facade and value.get("facade") and fact.scope.facade != value["facade"]:
        return Match(facts=[fact], reason="Сторона в значении расходится с областью действия факта", value=actual)
    if fact.characteristic_id == "climate" and not scope.get("month"):
        return Match(facts=[fact], reason="Не указан месяц климатических данных", value=actual)
    if result is None and value.get("available") is False and criterion.value is not False:
        result = False
    if fact.characteristic_id in MONTHLY and value.get("months") and parsed.month not in value["months"]:
        result = criterion.value is False
    route_result = route_constraints(fact, criterion)
    if route_result is not True:
        if route_result is None and result is not False:
            return Match(facts=[fact], reason=_route_reason(fact, criterion), value=actual)
        if route_result is False:
            return Match("no" if fact.confidence >= TRUSTED_CONFIDENCE else "unknown", [fact],
                         _route_reason(fact, criterion), actual)
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
    if result and criterion.characteristic_id in {"sea_view", "sunset_view", "mountain_view"} and not any(
        scope.get(k) is not None for k in ("facade", "floor_min", "floor_max", "room_type")
    ):
        return Match("partial", [fact], "Общее заявление о виде не подтверждает сторону и этаж конкретного номера", actual)
    if result and (fact.confidence < TRUSTED_CONFIDENCE or incomplete):
        return Match("partial", [fact], "Есть предварительные данные; нужно уточнить ограничения" if incomplete else "Предварительная оценка с низкой уверенностью", actual, condition_met=not incomplete)
    if not result and fact.confidence < TRUSTED_CONFIDENCE:
        return Match("unknown", [fact], "Модель предполагает несоответствие, но уверенности недостаточно для отказа", actual)
    return Match("yes" if result else "no", [fact], "Условие выполнено" if result else "Условие не выполняется", actual, condition_met=result)


def _best_match(facts: list[Fact], criterion: Criterion, context: RoomContext, field_name: str,
                scopes: dict, evaluations: dict, detailed: bool) -> Match:
    scoped = [f for f in facts if _includes(scopes[id(f)], context)]
    if criterion.scope and not _includes(criterion.scope.model_dump(exclude_none=True), context):
        return Match(reason="Этот номер не соответствует запрошенной стороне или этажу")
    if not scoped:
        return Match(reason="Для этой стороны, этажа или месяца нет данных")
    # Scoped room calculations outrank hotel-wide marketing, including positive quotes.
    if criterion.characteristic_id in ROOM_CHARACTERISTICS and detailed:
        scoped = [f for f in scoped if any(scopes[id(f)].get(k) is not None for k in ("facade", "floor_min", "floor_max", "room_type")) or value_at(f, field_name) is not True]
    if not scoped:
        return Match(reason="Общее заявление отеля не подтверждает конкретный номер")
    evaluated = [evaluations[id(f)] for f in scoped]
    known = [m for m in evaluated if m.status != "unknown"]
    if known:
        # Within overlapping evidence, a more specific resolved fact wins.
        def specificity(m):
            return sum(scopes[id(m.facts[0])].get(k) is not None for k in ("facade", "floor_min", "floor_max", "room_type", "month"))
        most = max(map(specificity, known))
        known = [m for m in known if specificity(m) == most]
        if any(m.status == "yes" for m in known) and any(m.status == "no" for m in known):
            return Match("partial", [f for m in known for f in m.facts], "Источники расходятся для одного и того же номера")
        return max(known, key=lambda m: ({"yes": 3, "no": 2, "partial": 1}[m.status], m.confidence))
    return max(evaluated, key=lambda m: m.confidence, default=Match())


def assess_hotel(facts: list[Fact], parsed: ParsedQuery, specs: list[CharacteristicSpec] | None = None) -> Assessment:
    criteria = [*parsed.must, *parsed.nice]
    fields = {s.id: s.search.field for s in specs or []}
    by_id = {c.characteristic_id: [] for c in criteria}
    scopes = {id(f): scope_values(f) for f in facts if f.characteristic_id in by_id}
    for fact in facts:
        if fact.characteristic_id in by_id and scopes[id(fact)].get("month", parsed.month) == parsed.month:
            by_id[fact.characteristic_id].append(fact)
    relevant = [f for values in by_id.values() for f in values]
    criterion_fields = [c.field or fields.get(c.characteristic_id) or DEFAULT_FIELDS.get(c.characteristic_id, "available") for c in criteria]
    evaluations = [{id(f): evaluate_fact(f, c, parsed, name) for f in by_id[c.characteristic_id]}
                   for c, name in zip(criteria, criterion_fields, strict=True)]
    detailed = {cid: any(any(scopes[id(f)].get(k) is not None for k in ("facade", "floor_min", "floor_max", "room_type"))
                         and fresh(f) and f.evidence for f in values) for cid, values in by_id.items()}
    facades = {scopes[id(f)].get("facade") for f in relevant} - {None}
    room_types = {scopes[id(f)].get("room_type") for f in relevant} - {None}
    floors = {parsed.min_floor or 1}
    for fact in relevant:
        scope = scopes[id(fact)]
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
        matches = [_best_match(by_id[c.characteristic_id], c, context, name, scopes, evaluated, detailed[c.characteristic_id])
                   for c, name, evaluated in zip(criteria, criterion_fields, evaluations, strict=True)]
        candidates.append(Assessment(context, matches))
        for index, match in enumerate(matches[:len(parsed.must)]):
            if match.status in {"yes", "partial"}:
                alternatives.setdefault(index, []).extend(match.facts)
    def key(item):
        required = item.matches[:len(parsed.must)]
        yes = sum(m.status == "yes" for m in required)
        progress = sum({"yes": 1, "partial": .5, "unknown": .1, "no": 0}[m.status] for m in required)
        utility = sum(m.utility*c.weight*(2 if i < len(parsed.must) else 1) for i, (c, m) in enumerate(zip(criteria, item.matches, strict=True)))
        return sum(m.supported for m in required), yes, progress, utility, -item.context.floor
    best = max(candidates, key=key, default=Assessment(RoomContext(month=parsed.month), []))
    best.alternatives = {i: list({f.id: f for f in fs}.values()) for i, fs in alternatives.items()}
    best.scope_conflict = len(alternatives) >= 2 and not any(
        all(c.matches[i].status in {"yes", "partial"} for i in alternatives) for c in candidates)
    return best
