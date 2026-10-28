"""Evidence evaluation and finite room-scope intersection shared by search and UI.

A room context is a representative of a partition, not an invented room number.
Floor boundaries from facts make the search exact for closed floor intervals.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import product
from typing import Any

from backend.contracts import CharacteristicSpec, Criterion, Fact, Hotel, ParsedQuery

TRUSTED_CONFIDENCE = .7
ROOM_CHARACTERISTICS = {"sea_view", "sunset_view", "mountain_view", "quiet_night", "soundproofing", "room_orientation",
                        'morning_sun', 'evening_heat', 'railway_noise', 'landmark_view'}
DEFAULT_FIELDS = {"beach_walk": "minutes", "climate": "water_c", "air_quality": "european_aqi", "connectivity": "download_mbps"}
MONTHLY = {"climate", "sunset_view", "morning_swim", 'morning_sun', 'evening_heat', 'climate_days'}
COMPASS_AZIMUTHS = {side: index * 45 for index, side in enumerate(("N", "NE", "E", "SE", "S", "SW", "W", "NW"))}
FLOOR_DOMAIN_UNKNOWN = "Этажность отеля пока не подтверждена; сторону и этаж номера нужно уточнить у отеля"
FLOOR_DOMAIN_CAVEAT = "Этажность отеля/привязка к зданию не подтверждена; этажи геомодели условны для выбранного контура"
ROUTE_FIELDS = {
    ('family_nearby', 'available'): ('walk_playground', 'available'),
    ('family_nearby', 'playground_distance_m'): ('walk_playground', 'distance_m'),
    ('family_nearby', 'hospital_distance_m'): ('walk_hospital', 'distance_m'),
    ('car_access', 'parking_distance_m'): ('walk_parking', 'distance_m'),
    ('car_access', 'ev_distance_m'): ('walk_ev', 'distance_m'),
}


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
    risk_factor: float = 1
    risk_facts: list[Fact] = field(default_factory=list)
    context_confidence: float | None = None

    @property
    def supported(self) -> bool:
        return self.status == "yes" or self.status == "partial" and self.condition_met and self.confidence >= .6

    @property
    def confidence(self) -> float:
        return (self.context_confidence if self.context_confidence is not None
                else min((f.confidence for f in self.facts), default=0))

    @property
    def utility(self) -> float:
        # Reliability of a statement is not P(satisfies). Unknown has a fixed prior.
        utility = {"yes": .6 + .4*self.confidence, "partial": .35 + .4*self.confidence,
                   "unknown": .2, "no": .05*(1-self.confidence)}[self.status]
        # Oblique sunset is useful, but is a weaker fit than a view straight ahead.
        if self.condition_met and any((aspect := sunset_aspect(f)) and aspect.oblique for f in self.facts):
            utility *= .85
        return utility * self.risk_factor


@dataclass
class Assessment:
    context: RoomContext
    matches: list[Match]
    scope_conflict: bool = False
    alternatives: dict[int, list[Fact]] = field(default_factory=dict)
    known_floors: int | None = None
    domain_reason: str | None = None


def fresh(fact: Fact, at: datetime | None = None) -> bool:
    return fact.status in {"computed", "confirmed"} and _not_expired(fact, at)


def _not_expired(fact: Fact, at: datetime | None = None) -> bool:
    at = at or datetime.now(UTC)
    deadline = fact.valid_until
    if deadline and deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline is None or deadline > at


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
             ("floor_max", fact.scope.floor_max), ("room_type", fact.scope.room_type), ("month", fact.scope.month),
             ("landmark_id", fact.scope.landmark_id)) if value is not None}
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


def _scope_contains(outer: dict, inner: dict) -> bool:
    return all(outer.get(key) is None or outer[key] == inner.get(key) for key in ("facade", "room_type", "month", "landmark_id")) and (
        (outer.get("floor_min") or 1) <= (inner.get("floor_min") or 1) and
        (outer.get("floor_max") or 100) >= (inner.get("floor_max") or 100))


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
    if criterion.no_stairs or criterion.field == "has_steps" and criterion.op == "eq" and criterion.value is False:
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
    if criterion.no_stairs or criterion.field == "has_steps" and criterion.op == "eq" and criterion.value is False:
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
    stairs = criterion.no_stairs or criterion.field == "has_steps" and criterion.value is False
    return "Маршрут " + ("без лестниц и переходов" if stairs and criterion.no_crossings else "без лестниц" if stairs else "без переходов") + " не подтверждён: " + "; ".join(details)


def real_room_confirmation(fact: Fact) -> bool:
    """An actual scoped hotel answer can stand independently of the geo model."""
    return (fact.status == "confirmed" and not fact.synthetic and fresh(fact) and bool(fact.supporting_claim_ids)
            and any(e.kind == "hotelier" for e in fact.evidence)
            and not any(e.kind in {"simulation", "mock"} for e in fact.evidence))


def hotel_floor_count(hotel: Hotel | None) -> int | None:
    return hotel.floors if hotel is not None and type(hotel.floors) is int and 1 <= hotel.floors <= 100 else None


def evaluate_fact(fact: Fact, criterion: Criterion, parsed: ParsedQuery, field_name: str,
                  *, hotel: Hotel | None = None) -> Match:
    if not fresh(fact):
        reason = "Данные оспариваются" if fact.status == "disputed" else "Данных нет" if fact.status == "unknown" else "Данные устарели; нужно перепроверить"
        if fact.characteristic_id == 'landmark_view' and isinstance(fact.value, dict) and fact.value.get('record_type') == 'target_metadata':
            reason = str(fact.value.get('unknown_reason') or 'Для цели нет подтверждённых входов расчёта видимости')
        return Match(facts=[fact], reason=reason)
    if not fact.evidence or not fact.supporting_claim_ids:
        return Match(facts=[fact], reason="Нет цепочки подтверждающих источников")
    actual = value_at(fact, field_name)
    value = fact.value if isinstance(fact.value, dict) else {}
    if (fact.characteristic_id == 'accessible_route' and field_name == 'available' and criterion.value is True
            and (value.get('available') is False or value.get('wheelchair_suitable') is False or value.get('has_steps') is True)):
        return Match('no', [fact], 'По сведениям о маршруте есть препятствие для коляски', False)
    if (fact.characteristic_id == 'connectivity' and field_name in {'download_mbps', 'upload_mbps', 'latency_ms'}
            and value.get('kind') in {'mobile', 'fixed'}
            and re.search(r'wi[ -]?fi|вай[ -]?фай|вифи', parsed.text.casefold())):
        present = value.get('wifi_reported') is True
        return Match('partial' if present else 'unknown', [fact],
                     'Wi-Fi есть по карте · скорость неизвестна' if present
                     else 'Тесты интернета в районе не подтверждают Wi-Fi отеля')
    route_field = ROUTE_FIELDS.get((fact.characteristic_id, field_name))
    if route_field and route_field[0] in value:
        actual = value_at(fact, '.'.join(route_field))
    if fact.characteristic_id == 'airport_noise' and field_name == 'noise_db':
        return Match(facts=[fact], reason='Слышимость самолётов не измерялась; по карте известны расстояние до взлётной полосы и направление захода', value=actual)
    if fact.characteristic_id == 'mobile_masts' and field_name == 'available':
        return Match(facts=[fact], reason='Число мачт на карте не подтверждает покрытие 4G/5G или скорость мобильного интернета')
    if fact.characteristic_id == 'building_history' and field_name == 'available':
        # Legacy criteria keep their public shape. History metadata is useful,
        # but an opening date or a Wikipedia link cannot prove historic status.
        if re.search(r'историческ|старинн|особняк|сталинк', parsed.text.casefold()):
            actual = value.get('historic_designation')
        elif 'history_information' in value:
            actual = value['history_information']
    if fact.characteristic_id == 'building_history' and field_name == 'architecture' and actual in {'модерн', 'Art Nouveau', 'art nouveau'}:
        actual = 'art_nouveau'
    result = _compare(actual, criterion)
    scope = scope_values(fact)
    geometric = any(e.kind in {"calculation", "simulation"} for e in fact.evidence)
    scoped_floors = fact.scope.type == "floor_range" or any(scope.get(key) is not None for key in ("floor_min", "floor_max"))
    if (hotel is not None and hotel_floor_count(hotel) is None and not real_room_confirmation(fact)
            and (scoped_floors or geometric and fact.characteristic_id in {"sea_view", "sunset_view", "mountain_view", 'landmark_view'})):
        return Match(facts=[fact], reason=FLOOR_DOMAIN_UNKNOWN, value=actual)
    if fact.scope.month and value.get("month") and fact.scope.month != value["month"]:
        return Match(facts=[fact], reason="Месяц значения расходится с областью действия факта", value=actual)
    if fact.scope.facade and value.get("facade") and fact.scope.facade != value["facade"]:
        return Match(facts=[fact], reason="Сторона в значении расходится с областью действия факта", value=actual)
    if fact.scope.landmark_id and value.get('landmark_id') and fact.scope.landmark_id != value['landmark_id']:
        return Match(facts=[fact], reason='Цель в значении расходится с областью расчёта видимости', value=actual)
    if fact.characteristic_id in {"climate", 'climate_days'} and not scope.get("month"):
        return Match(facts=[fact], reason="Не указан месяц климатических данных", value=actual)
    if result is None and '.' not in field_name and value.get("available") is False and criterion.value is not False:
        result = False
    if fact.characteristic_id in MONTHLY and value.get("months") and parsed.month not in value["months"]:
        result = criterion.value is False
    route_result = route_constraints(fact, criterion)
    if route_result is not True:
        if route_result is None and result is not False:
            return Match(facts=[fact], reason=_route_reason(fact, criterion), value=actual)
        if route_result is False:
            return Match("no", [fact], _route_reason(fact, criterion).replace(" не подтверждён:", " не подходит:"), actual)
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
        if (fact.characteristic_id == "connectivity" and field_name in {"download_mbps", "upload_mbps", "latency_ms"}
                and actual is None and _finite_number(criterion.value) and value.get("wifi_reported") is True):
            return Match("partial", [fact], "Wi-Fi указан, скорость не измерена", actual, condition_met=False)
        if fact.characteristic_id == "soundproofing" and field_name == "available" and any(value.get(k) is not None for k in ("windows", "blackout", "air_conditioning")):
            return Match("partial", [fact], "Есть сведения об окнах, но шумоизоляция не подтверждена", actual)
        return Match(facts=[fact], reason="Нужное поле не подтверждено", value=actual)
    if fact.characteristic_id == 'accessible_route' and result and not real_room_confirmation(fact):
        return Match('partial', [fact], 'По карте есть сведения о маршруте; бордюры, ширина и проезд у входов не проверены. Доступность с коляской уточните у отеля', actual)
    if result and criterion.characteristic_id in {"sea_view", "sunset_view", "mountain_view", 'landmark_view'} and not any(
        scope.get(k) is not None for k in ("facade", "floor_min", "floor_max", "room_type")
    ):
        return Match("partial", [fact], "Общее заявление о виде не подтверждает сторону и этаж конкретного номера", actual)
    if result and (fact.confidence < TRUSTED_CONFIDENCE or incomplete):
        return Match("partial", [fact], "Есть предварительные данные; нужно уточнить ограничения" if incomplete else "Пока неточно — лучше уточнить у отеля", actual, condition_met=not incomplete)
    if result is False and _finite_number(actual) and _finite_number(criterion.value) and criterion.op in {"lte", "gte", "eq"}:
        return Match("no", [fact], "Известное значение не соответствует заданному порогу", actual)
    if not result and fact.confidence < TRUSTED_CONFIDENCE:
        return Match("unknown", [fact], "По расчёту скорее нет, но уверенность низкая", actual)
    return Match("yes" if result else "no", [fact], "Условие выполнено" if result else "Условие не выполняется", actual, condition_met=result)


def _best_match(facts: list[Fact], criterion: Criterion, context: RoomContext, field_name: str,
                scopes: dict, evaluations: dict, detailed: bool) -> Match:
    scoped = [f for f in facts if _includes(scopes[id(f)], context)]
    route = ROUTE_FIELDS.get((criterion.characteristic_id, field_name))
    if route and any(isinstance(f.value, dict) and route[0] in f.value for f in scoped):
        scoped = [f for f in scoped if isinstance(f.value, dict) and route[0] in f.value]
    if (criterion.characteristic_id == 'building_history' and field_name == 'available'
            and any(isinstance(f.value, dict) and 'historic_designation' in f.value for f in scoped)):
        scoped = [f for f in scoped if isinstance(f.value, dict) and 'historic_designation' in f.value]
    landmark = criterion.scope.landmark_id if criterion.scope else None
    if criterion.characteristic_id == 'landmark_view' and not landmark and criterion.value is False:
        return Match(reason='Отсутствие вида на все достопримечательности не подтверждено; укажите конкретный объект')
    if landmark:
        scoped = [f for f in scoped if scopes[id(f)].get('landmark_id') == landmark]
    if criterion.scope and not _includes(criterion.scope.model_dump(exclude_none=True), context):
        return Match(reason="Этот номер не соответствует запрошенной стороне или этажу")
    if not scoped:
        if landmark:
            from backend.agents.query_parser import landmark_patterns
            if landmark == 'fisht':
                return Match(reason='Точный контур Фишта найден, но подтверждённой высоты нет; видимость пока неизвестна')
            if landmark in landmark_patterns():
                return Match(reason='Для этой достопримечательности нет подтверждённого расчёта видимости')
        return Match(reason="Для этой стороны, этажа или месяца нет данных")
    # Scoped room calculations outrank hotel-wide marketing, including positive quotes.
    if criterion.characteristic_id in ROOM_CHARACTERISTICS and detailed:
        scoped = [f for f in scoped if any(scopes[id(f)].get(k) is not None for k in ("facade", "floor_min", "floor_max", "room_type")) or value_at(f, field_name) is not True]
    if not scoped:
        return Match(reason="Общее заявление отеля не подтверждает конкретный номер")
    current = [f for f in scoped if f.status != "stale" and _not_expired(f)]
    scoped = current or scoped
    # Compare actual domains before excluding unknown/disputed evaluations.
    # Counting non-null fields misses a one-floor dispute inside a wide interval.
    scoped = [f for f in scoped if not any(
        _scope_contains(scopes[id(f)], scopes[id(other)]) and not _scope_contains(scopes[id(other)], scopes[id(f)])
        for other in scoped)]
    evaluated = [evaluations[id(f)] for f in scoped]
    disputed = [m for m in evaluated if any(f.status == "disputed" for f in m.facts)]
    if disputed:
        return Match(facts=[f for m in disputed for f in m.facts], reason="Данные оспариваются для этой стороны, этажа или типа номера")
    known = [m for m in evaluated if m.status != "unknown"]
    if known:
        # Different targets may independently be visible or hidden. A request
        # for any landmark is existential, not a vote between those targets.
        if criterion.characteristic_id == 'landmark_view' and not landmark:
            positive = [m for m in known if m.status in {'yes', 'partial'} and m.condition_met]
            if positive:
                return max(positive, key=lambda m: (m.status == 'yes', m.confidence))
            unknown = [m for m in evaluated if m.status == 'unknown']
            if unknown:
                return Match(facts=[f for m in unknown for f in m.facts],
                             reason='Вид на часть достопримечательностей не рассчитан; отсутствие вида на все объекты не подтверждено')
        if any(m.status == "yes" for m in known) and any(m.status == "no" for m in known):
            return Match("partial", [f for m in known for f in m.facts], "Источники расходятся для одного и того же номера")
        return max(known, key=lambda m: ({"yes": 3, "no": 2, "partial": 1}[m.status], m.confidence))
    return max(evaluated, key=lambda m: m.confidence, default=Match())


def _route_accessibility(facts: list[Fact], criterion: Criterion, primary: Match) -> Match:
    """A route can expose barriers, but cannot establish complete wheelchair access.

    The original route remains a context witness with its own CID and ID. Never
    invent an accessible_route Fact or present the route as a hotel's answer.
    """
    if criterion.field not in {None, 'available'} or criterion.value is not True or primary.status in {'yes', 'no'}:
        return primary
    routes = [f for f in facts if f.characteristic_id == 'beach_walk' and fresh(f)
              and not f.synthetic and f.evidence and f.supporting_claim_ids
              and not any(e.kind in {'simulation', 'mock'} or e.inputs.get('synthetic') is True for e in f.evidence)
              and (value_at(f, 'available') is True or _finite_number(value_at(f, 'distance_m'))
                   and value_at(f, 'distance_m') > 0)]
    if not routes:
        return primary
    stairs = [f for f in routes if value_at(f, 'has_steps') is True]
    if stairs:
        route = max(stairs, key=lambda f: f.confidence)
        return Match('no', reason='Для коляски не подходит: на маршруте до пляжа отмечены лестницы',
                     risk_facts=[route], context_confidence=route.confidence)
    usable = [f for f in routes if value_at(f, 'has_steps') is False
              and _finite_number(value_at(f, 'return_ascent_m')) and value_at(f, 'return_ascent_m') >= 0
              and (isinstance(value_at(f, 'crossings'), list)
                   or _finite_number(value_at(f, 'crossings')) and value_at(f, 'crossings') >= 0)]
    if any(f.status == 'disputed' for f in primary.facts):
        return primary
    if not usable:
        if primary.status != 'unknown':
            return primary
        route = max(routes, key=lambda f: f.confidence)
        return Match(reason='Маршрут на карте есть, но не хватает сведений о лестницах, подъёме или переходах. Проезд с коляской нужно уточнить у отеля',
                     facts=primary.facts, risk_facts=[route], context_confidence=route.confidence)
    route = max(usable, key=lambda f: f.confidence)
    from backend.agents.guest_text import number
    crossings = value_at(route, 'crossings')
    count = len(crossings) if isinstance(crossings, list) else crossings
    detail = ('По карте лестниц нет; подъём обратно ' + number(round(value_at(route, 'return_ascent_m')))
              + ' м; ' + (f'переходов: {number(count)}' if count else 'переходов не отмечено'))
    return Match('partial', reason=detail + '. Бордюры, ширина и проезд у входов не проверены; уточните у отеля',
                 risk_facts=[route], context_confidence=route.confidence)


def assess_hotel(facts: list[Fact], parsed: ParsedQuery, specs: list[CharacteristicSpec] | None = None,
                 *, hotel: Hotel | None = None) -> Assessment:
    """Evaluate a room; omitted hotel metadata retains conditional pure-call semantics."""
    criteria = [*parsed.must, *parsed.nice]
    fields = {s.id: s.search.field for s in specs or []}
    by_id = {c.characteristic_id: [] for c in criteria}
    scopes = {id(f): scope_values(f) for f in facts if f.characteristic_id in by_id}
    for fact in facts:
        if fact.characteristic_id in by_id and scopes[id(fact)].get("month", parsed.month) == parsed.month:
            by_id[fact.characteristic_id].append(fact)
    relevant = [f for values in by_id.values() for f in values]
    criterion_fields = [c.field or fields.get(c.characteristic_id) or DEFAULT_FIELDS.get(c.characteristic_id, "available") for c in criteria]
    evaluations = [{id(f): evaluate_fact(f, c, parsed, name, hotel=hotel) for f in by_id[c.characteristic_id]}
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
    known_floors = hotel_floor_count(hotel)
    floors = {f for f in floors if (parsed.min_floor or 1) <= f <= (known_floors or 100)}
    contexts = [RoomContext(side, floor, room_type, parsed.month) for side, floor, room_type in product(
        [parsed.facade] if parsed.facade else sorted(facades) or [None], sorted(floors), sorted(room_types) or [None])]
    requested_contexts = [context for context in contexts if all(
        not c.scope or _includes(c.scope.model_dump(exclude_none=True), context) for c in parsed.must)]
    if requested_contexts:
        contexts = requested_contexts
    if not contexts:
        reason = "Запрошенный этаж выходит за пределы известной этажности отеля; подходящий номер не подтверждён"
        matches = []
        for criterion, name in zip(criteria, criterion_fields, strict=True):
            selected = max(by_id[criterion.characteristic_id], key=lambda fact: fact.confidence, default=None)
            matches.append(Match(facts=[selected] if selected else [], reason=reason,
                                 value=value_at(selected, name) if selected else None))
        return Assessment(RoomContext(month=parsed.month), matches, known_floors=known_floors, domain_reason=reason)
    candidates = []
    alternatives: dict[int, list[Fact]] = {}
    for context in contexts:
        matches = [_best_match(by_id[c.characteristic_id], c, context, name, scopes, evaluated, detailed[c.characteristic_id])
                   for c, name, evaluated in zip(criteria, criterion_fields, evaluations, strict=True)]
        domain_reason = None
        if hotel is not None and known_floors is None:
            # A hotel-wide amenity cannot establish that the requested floor
            # exists. A genuine room answer with matching explicit floors can.
            witnessed = any(real_room_confirmation(fact) and any(scopes[id(fact)].get(key) is not None for key in ("floor_min", "floor_max"))
                            and _includes(scopes[id(fact)], context) and match.supported
                            for match in matches[:len(parsed.must)] for fact in match.facts)
            for index, criterion in enumerate(criteria):
                floor_required = (index < len(parsed.must) and parsed.min_floor is not None
                                  or criterion.scope is not None and any(getattr(criterion.scope, key) is not None
                                                                         for key in ("floor_min", "floor_max")))
                if floor_required and not witnessed:
                    original = matches[index]
                    matches[index] = Match(facts=original.facts, reason=FLOOR_DOMAIN_UNKNOWN, value=original.value)
                    if index < len(parsed.must):
                        domain_reason = FLOOR_DOMAIN_UNKNOWN
            if parsed.min_floor is not None and not witnessed:
                domain_reason = FLOOR_DOMAIN_UNKNOWN
        candidates.append(Assessment(context, matches, known_floors=known_floors, domain_reason=domain_reason))
        for index, match in enumerate(matches[:len(parsed.must)]):
            if match.status in {"yes", "partial"}:
                alternatives.setdefault(index, []).extend(match.facts)
    def key(item):
        required = item.matches[:len(parsed.must)]
        yes = sum(m.status == "yes" for m in required)
        progress = sum({"yes": 1, "partial": .5, "unknown": .1, "no": 0}[m.status] for m in required)
        utility = sum(m.utility*c.weight*(2 if i < len(parsed.must) else 1) for i, (c, m) in enumerate(zip(criteria, item.matches, strict=True)))
        conditional_evidence = sum(m.reason == FLOOR_DOMAIN_UNKNOWN and bool(m.facts) for m in required)
        return sum(m.supported for m in required), yes, progress, utility, conditional_evidence, -item.context.floor
    best = max(candidates, key=key, default=Assessment(RoomContext(month=parsed.month), []))
    best.alternatives = {i: list({f.id: f for f in fs}.values()) for i, fs in alternatives.items()}
    best.scope_conflict = len(alternatives) >= 2 and not any(
        all(c.matches[i].status in {"yes", "partial"} for i in alternatives) for c in candidates)
    for index, criterion in enumerate(criteria):
        if criterion.characteristic_id == 'accessible_route':
            best.matches[index] = _route_accessibility(facts, criterion, best.matches[index])
    risks = [f for f in facts if f.characteristic_id == 'airport_noise' and fresh(f)
             and any(e.kind in {'calculation', 'simulation'} for e in f.evidence)
             and f.supporting_claim_ids and value_at(f, 'available') is True]
    bars = [f for f in facts if f.characteristic_id == 'nightlife' and fresh(f)
            and f.evidence and f.supporting_claim_ids and not f.synthetic
            and not any(e.kind in {'simulation', 'mock'} or e.inputs.get('synthetic') is True for e in f.evidence)
            and _finite_number(value_at(f, 'nearest_bar_m')) and 0 <= value_at(f, 'nearest_bar_m') < 100]
    for criterion, match in zip(criteria, best.matches, strict=True):
        if criterion.characteristic_id == 'quiet_night':
            if risks:
                match.risk_factor = .9
            match.risk_facts = [*risks, *bars]
    return best
