"""Compact, offline descriptions of modelled positions grounded in resolved facts.

Positions are facade × contiguous floor range × month, never room inventory.
Each atomic interval is evaluated independently before describing a wider slice.
"""
from __future__ import annotations

import hashlib
import json
import math

from backend.agents.explainer import SIDES, approximate
from backend.agents.query_match import (
    FLOOR_DOMAIN_UNKNOWN,
    Match,
    assess_hotel,
    fresh,
    hotel_floor_count,
    scope_values,
    sunset_aspect,
)
from backend.contracts import (
    CharacteristicSpec,
    Criterion,
    Fact,
    Hotel,
    ParsedQuery,
    RoomClause,
    RoomProfile,
    RoomScope,
    Scope,
)

_LABELS = {
    "sea_view": "Вид на море",
    "sunset_view": "Закат",
    "mountain_view": "Вид на горы",
    "quiet_night": "Тишина ночью",
    "morning_sun": "Утреннее солнце",
    "evening_heat": "Вечерняя солнечная экспозиция",
    "railway_noise": "Железнодорожный шум",
    "soundproofing": "Окна и шумоизоляция",
    "beach_walk": "Путь до пляжа",
}
_SOLAR = {"sunset_view", "morning_sun", "evening_heat"}
_MONTHS_IN = ("январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре", "ноябре", "декабре")
_CORE = {"sea_view", "sunset_view", "quiet_night", "soundproofing", "beach_walk"}
_POSITION_CAVEAT = (
    "Это модельное положение в здании. Существование и доступность конкретного номера, "
    "его тип, кровати и направление реальных окон нужно подтвердить у отеля."
)
_ROUTE_CAVEAT = (
    "Маршрут рассчитан по OSM; теги лестниц и переходов могут быть неполными, "
    "участки у входа и пляжа не проверены. Отсутствие отметок не гарантирует доступность."
)


def _number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _synthetic(fact: Fact) -> bool:
    return fact.synthetic or any(e.kind in {"simulation", "mock"} for e in fact.evidence)


def _month_matches(fact: Fact, month: int) -> bool:
    scope = scope_values(fact)
    value = fact.value if isinstance(fact.value, dict) else {}
    if scope.get("month") is not None and scope["month"] != month:
        return False
    if fact.characteristic_id in _SOLAR:
        months = value.get("months")
        return (scope.get("month") == month or isinstance(months, list) and month in months) and (
            not isinstance(months, list) or month in months)
    return True


def _eligible(fact: Fact, scope: RoomScope) -> bool:
    values = scope_values(fact)
    # An unspecified side/type must not silently select whichever has good news.
    return (not values.get("facade") or values["facade"] == scope.facade) and (
        not values.get("room_type") or values["room_type"] == scope.room_type) and _month_matches(
            fact, scope.month or 9)


def _bounds(scope: RoomScope, hotel: Hotel) -> tuple[int, int]:
    low = scope.floor_min or 1
    return low, scope.floor_max or max(hotel_floor_count(hotel) or low, low)


def _intervals(low: int, high: int, facts: list[Fact]) -> list[tuple[int, int]]:
    boundaries = {low, high + 1}
    for fact in facts:
        values = scope_values(fact)
        for bound in (values.get("floor_min"), (values.get("floor_max") or 100) + 1):
            if bound is not None and low < bound <= high:
                boundaries.add(bound)
    ordered = sorted(boundaries)
    return [(a, b - 1) for a, b in zip(ordered, ordered[1:], strict=False)]


def _presentation_signature(fact: Fact) -> str:
    value = fact.value if isinstance(fact.value, dict) else {"available": fact.value}
    fields = ("available", "night_db_min", "night_db_max", "visible_minutes", "visible_minutes_min",
              "visible_minutes_max", "exposure_index", "exposure_index_min", "exposure_index_max",
              "windows", "blackout", "air_conditioning")
    shown = {k: round(value[k], 2 if "index" in k else 0) if _number(value[k]) else value[k]
             for k in fields if k in value}
    aspect = sunset_aspect(fact)
    return json.dumps([fact.characteristic_id, fact.status, fresh(fact), fact.confidence >= .7,
                       bool(fact.supporting_claim_ids), bool(fact.evidence), _synthetic(fact), shown,
                       vars(aspect) if aspect else None,
                       sorted({e.kind for e in fact.evidence}),
                       sorted({note for e in fact.evidence for note in e.limitations})], sort_keys=True)


def _coalesced_intervals(high: int, facts: list[Fact]) -> list[tuple[int, int]]:
    """Adjacent equivalent source domains may share a card, with all refs kept."""
    result = []
    previous = None
    for low, upper in _intervals(1, high, facts):
        signature = sorted(_presentation_signature(f) for f in facts if (
            scope_values(f).get("floor_min", 1) <= low <= scope_values(f).get("floor_max", 100)))
        if result and signature == previous:
            result[-1] = (result[-1][0], upper)
        else:
            result.append((low, upper))
        previous = signature
    return result


def _criterion(cid: str, scope: RoomScope, floor: int) -> Criterion:
    field, op, value = "available", "eq", True
    if cid in {"beach_walk", "railway_noise"}:
        field, op, value = ("minutes" if cid == "beach_walk" else "night_db_max"), "gte", 0
    elif cid == "evening_heat":
        field, op, value = "exposure_index", "lte", .35
    criterion_scope = None if cid == "beach_walk" else Scope(
        facade=scope.facade, floor_min=floor, floor_max=floor, room_type=scope.room_type, month=scope.month)
    return Criterion(characteristic_id=cid, label=_LABELS[cid], field=field, op=op, value=value,
                     scope=criterion_scope)


def _references(matches: list[Match]) -> list[Fact]:
    return sorted({f.id: f for match in matches for f in match.facts}.values(), key=lambda f: f.id)


def _range(facts: list[Fact], low_field: str, high_field: str | None = None, *, minutes=False) -> str | None:
    values = [f.value if isinstance(f.value, dict) else {} for f in facts]
    lows = [v.get(low_field) for v in values]
    highs = [v.get(high_field or low_field) for v in values]
    if not lows or not all(_number(x) for x in [*lows, *highs]):
        return None
    low, high = min(lows), max(highs)
    a, b = approximate(low, minutes=minutes), approximate(high, minutes=minutes)
    return a if a == b else a + "–" + b.removeprefix("≈ ")


def _solar_minutes(facts: list[Fact]) -> str | None:
    # A mean over several floors cannot be stated as a bound for each floor.
    if all(isinstance(f.value, dict) and _number(f.value.get("visible_minutes_min"))
           and _number(f.value.get("visible_minutes_max")) for f in facts):
        return _range(facts, "visible_minutes_min", "visible_minutes_max")
    if all(scope_values(f).get("floor_min") == scope_values(f).get("floor_max")
           and scope_values(f).get("floor_min") is not None for f in facts):
        return _range(facts, "visible_minutes")
    return None


def _known_text(cid: str, facts: list[Fact], status: str, month: int) -> tuple[str, list[str]]:
    caveats: list[str] = []
    values = [f.value if isinstance(f.value, dict) else {} for f in facts]
    preliminary = "Предварительно: " if status == "partial" else ""
    if cid in {"sea_view", "mountain_view"}:
        target = "моря" if cid == "sea_view" else "гор"
        return preliminary + (f"Для этого положения есть данные о видимости {target}." if status != "no"
                              else f"Для этого положения видимость {target} не подтверждается данными."), caveats
    if cid == "sunset_view":
        caveats.append("Закат оценён для выбранного месяца; облачность и реальные окна неизвестны. Минуты означают сумму видимых интервалов, а не непрерывный вид до заката.")
        if status == "no":
            return f"В {_MONTHS_IN[month - 1]} закат для этого положения не подтверждается данными.", caveats
        text = preliminary + f"В {_MONTHS_IN[month - 1]} есть данные о видимости заката"
        minutes = _solar_minutes(facts)
        if minutes:
            text += f": {minutes} мин перед закатом по модели последних 5° над горизонтом"
        aspects = [sunset_aspect(f) for f in facts]
        if aspects and all(a is not None for a in aspects):
            if all(a.oblique for a in aspects):
                text += "; закат сбоку"
            if all(a.exact_normal for a in aspects):
                angles = [a.angle_deg for a in aspects]
                text += f"; угол к нормали фасада {approximate(min(angles))}"
                if approximate(min(angles)) != approximate(max(angles)):
                    text += "–" + approximate(max(angles)).removeprefix("≈ ")
                text += "°"
            else:
                lower, upper = min(a.minimum_deg for a in aspects), max(a.maximum_deg for a in aspects)
                text += f"; ориентировочный угол к направлению стороны {approximate(lower)}–{math.ceil(upper)}°"
                caveats.append("Точная нормаль окна не известна; угол по компасному сектору фасада ориентировочный.")
        return text + ".", caveats
    if cid in {"quiet_night", "railway_noise"}:
        noise = _range(facts, "night_db_min", "night_db_max")
        if noise:
            source = "железнодорожного " if cid == "railway_noise" else ""
            caveats.append("Наружный шум — приближённый модельный прокси, а не измерение внутри номера; сон и шумоизоляция окон не подтверждены.")
            return preliminary + f"Модельный прокси наружного {source}шума ночью: {noise} дБ.", caveats
        if cid == "railway_noise":
            return "Численная оценка наружного железнодорожного шума в дБ не подтверждена источником.", caveats
        caveats.append("Сведения о тишине без измерения дБ не определяют наружный шум или шумоизоляцию конкретного номера.")
        return preliminary + ("Тишина заявлена в источнике; численного измерения шума нет." if status != "no"
                              else "Тишина для этого положения не подтверждается источником; данных в дБ нет."), caveats
    if cid == "morning_sun":
        minutes = _solar_minutes(facts)
        text = (f"В {_MONTHS_IN[month - 1]} порог утреннего солнца по модели достигнут" if status != "no"
                else f"В {_MONTHS_IN[month - 1]} порог утреннего солнца по модели не достигнут")
        if minutes:
            text += f"; прямое солнце {minutes} мин"
        caveats.append("Утреннее солнце рассчитано на 15-е число месяца в первые три часа после восхода; облачность, окна и шторы неизвестны.")
        return preliminary + text + ".", caveats
    if cid == "evening_heat":
        index = _range(facts, "exposure_index_min", "exposure_index_max")
        if index is None:
            index = _range(facts, "exposure_index")
        # The index has a 0..1 scale; integer rounding would erase its meaning.
        if index:
            low = min(v["exposure_index_min"] if _number(v.get("exposure_index_min")) else v["exposure_index"] for v in values)
            high = max(v["exposure_index_max"] if _number(v.get("exposure_index_max")) else v["exposure_index"] for v in values)
            index = f"{low:.2f}" if round(low, 2) == round(high, 2) else f"{low:.2f}–{high:.2f}"
            return preliminary + f"В {_MONTHS_IN[month - 1]} индекс вечерней солнечной экспозиции: {index.replace('.', ',')} из 1.", ["Индекс вечернего солнца — геометрический прокси, не температура комнаты или прогноз нагрева."]
    if cid == "soundproofing":
        details = []
        for key, yes, no in (("blackout", "плотные шторы указаны", "плотные шторы не указаны как имеющиеся"),
                             ("air_conditioning", "кондиционер указан", "кондиционер не указан как имеющийся")):
            if all(v.get(key) is True for v in values):
                details.append(yes)
            elif all(v.get(key) is False for v in values):
                details.append(no)
        if all(v.get("windows") == "double" for v in values):
            details.insert(0, "в источнике указаны двойные окна")
        if details:
            text = "Для выбранного положения " + "; ".join(details) + "."
            if status == "no":
                text += " Шумоизоляция не подтверждается источником."
            return text, ["Конструкция окон и наличие штор сами по себе не подтверждают звукоизоляцию."]
        if status == "partial" and not all(v.get("available") is True for v in values):
            return "Есть отдельные сведения об окнах; шумоизоляция выбранного положения не подтверждена.", caveats
        return ("Шумоизоляция заявлена в источнике; численного измерения нет." if status != "no"
                else "Шумоизоляция для выбранного положения не подтверждается источником."), caveats
    if cid == "beach_walk":
        if status == "no":
            return "Пеший маршрут до пляжа не подтверждается данными.", [_ROUTE_CAVEAT]
        parts = []
        for name, label, unit, minutes in (("minutes", "до пляжа", "мин пешком", True),
                                           ("return_minutes", "обратно", "мин", True),
                                           ("ascent_m", "подъём к пляжу", "м", False),
                                           ("return_ascent_m", "подъём обратно", "м", False)):
            amount = _range(facts, name, minutes=minutes)
            if amount:
                parts.append(f"{label} {amount} {unit}")
        if all(v.get("has_steps") is True for v in values):
            parts.append("в OSM-маршруте отмечены лестницы")
        elif all(v.get("has_steps") is False for v in values):
            parts.append("в OSM-маршруте лестницы не отмечены")
        counts = [len(v["crossings"]) if isinstance(v.get("crossings"), list) else v.get("crossings") for v in values]
        if all(_number(c) and c >= 0 for c in counts):
            if max(counts) == 0:
                parts.append("в OSM-маршруте переходы не отмечены")
            else:
                parts.append(f"отмечено переходов: {math.ceil(min(counts))}" + (f"–{math.ceil(max(counts))}" if min(counts) != max(counts) else ""))
        if any(v.get("has_steps") is None for v in values) or any(c is None or isinstance(c, bool) for c in counts):
            caveats.append("Часть сведений о лестницах или переходах на пути до пляжа отсутствует.")
        return preliminary + "По OSM-модели: " + "; ".join(parts) + ".", [_ROUTE_CAVEAT, *caveats]
    return _LABELS[cid] + ": подробности не подтверждены.", caveats


def _clause(cid: str, matches: list[Match], month: int) -> tuple[RoomClause, list[str]]:
    facts = _references(matches)
    statuses = {m.status for m in matches}
    caveats = []
    if statuses == {"unknown"}:
        reasons = list(dict.fromkeys(m.reason for m in matches))
        text = _LABELS[cid] + ": не подтверждено. " + "; ".join(reasons) + "."
        status = "unknown"
    elif "unknown" in statuses or len(statuses) > 1:
        status = "partial"
        text = _LABELS[cid] + ": единый результат для всего выбранного диапазона не подтверждён; " + (
            "часть этажей не покрыта свежими подтверждёнными данными." if "unknown" in statuses
            else "результат меняется по этажам; выберите более узкое положение.")
    elif any(len({f.value.get("available") for f in m.facts if isinstance(f.value, dict)
                  and isinstance(f.value.get("available"), bool)}) > 1 for m in matches):
        status = "partial"
        text = _LABELS[cid] + ": источники расходятся для выбранного положения; нужно уточнение."
    elif cid != "soundproofing" and any(m.status == "partial" and not m.condition_met for m in matches):
        status = "partial"
        text = _LABELS[cid] + ": " + "; ".join(dict.fromkeys(m.reason for m in matches)) + "."
    else:
        status = next(iter(statuses))
        text, caveats = _known_text(cid, facts, status, month)
    synthetic = any(_synthetic(f) for f in facts)
    if synthetic:
        text = "Синтетические демонстрационные данные: " + text
    for fact in facts:
        for evidence in fact.evidence:
            caveats.extend(evidence.limitations)
    return RoomClause(text=text, characteristic_id=cid, status=status, synthetic=synthetic,
                      fact_ids=[f.id for f in facts],
                      evidence_refs=list(dict.fromkeys(e.id for f in facts for e in f.evidence))), caveats


def _title(scope: RoomScope, unknown_height: bool) -> str:
    parts = [SIDES[scope.facade] + " сторона" if scope.facade else "сторона не уточнена"]
    low, high = scope.floor_min, scope.floor_max
    if unknown_height and low == high == 1:
        parts.append("условный уровень 1")
    elif low == high:
        parts.append(f"{low}-й этаж")
    else:
        parts.append(f"{low}–{high}-й этажи")
    return "Модельное положение: " + ", ".join(parts)


def describe_room(hotel: Hotel, facts: list[Fact], specs: list[CharacteristicSpec], scope: RoomScope,
                  *, geo: dict | None = None) -> RoomProfile:
    """Describe one requested slice; incomplete coverage never becomes a positive."""
    del geo  # Geo arrays cannot override resolved facts or supply room inventory.
    requested = scope
    if hotel_floor_count(hotel) is None:
        scope = scope.model_copy(update={"floor_min": 1, "floor_max": 1})
    low, high = _bounds(scope, hotel)
    scope = scope.model_copy(update={"floor_min": low, "floor_max": high, "month": scope.month or 9})
    same_hotel = [f for f in facts if f.hotel_id == hotel.id and f.characteristic_id in _LABELS]
    selected = [f for f in same_hotel if _eligible(f, scope)]
    ids = [cid for cid in _LABELS if cid in _CORE or any(f.characteristic_id == cid for f in same_hotel)]
    by_cid: dict[str, list[Match]] = {cid: [] for cid in ids}
    known_height = hotel_floor_count(hotel)
    outside = known_height is not None and high > known_height
    for floor, _ in _intervals(low, high, selected):
        criteria = [_criterion(cid, scope, floor) for cid in ids]
        parsed = ParsedQuery(text="", must=criteria, facade=scope.facade, month=scope.month)
        assessment = assess_hotel(selected, parsed, specs, hotel=hotel)
        for cid, match in zip(ids, assessment.matches, strict=True):
            if cid == "beach_walk" and match.status != "unknown" and any(
                isinstance(f.value, dict) and f.value.get("available") is False for f in match.facts
            ):
                match = Match("no", match.facts, "Пеший маршрут не подтверждён источником")
            if cid != "beach_walk" and (outside or known_height is None and (low != 1 or high != 1)):
                match = Match(facts=match.facts, reason=("Запрошенный диапазон выходит за известную этажность отеля"
                                                       if outside else FLOOR_DOMAIN_UNKNOWN))
            by_cid[cid].append(match)
    clauses, caveats = [], [_POSITION_CAVEAT]
    if known_height is None:
        if requested.floor_min not in (None, 1) or requested.floor_max not in (None, 1):
            caveats.append("Запрошенные этажи не подтверждены; ниже показан только условный уровень 1.")
        caveats.append(FLOOR_DOMAIN_UNKNOWN + ". Для просмотра доступен только условный уровень 1.")
    if outside:
        caveats.append("Существование этажей за пределами известной этажности отеля не подтверждено.")
    for cid, matches in by_cid.items():
        clause, notes = _clause(cid, matches, scope.month)
        clauses.append(clause)
        caveats.extend(notes)
    clauses.append(RoomClause(text="Реальные окна и доступность конкретного номера не подтверждены этим описанием."))
    if any(c.synthetic for c in clauses):
        caveats.append("Часть описания основана на синтетических демонстрационных данных; реальное подтверждение отеля нужно получить отдельно.")
    identity = json.dumps([hotel.id, scope.model_dump(mode="json")], sort_keys=True, separators=(",", ":"))
    return RoomProfile(id="position_" + hashlib.sha256(identity.encode()).hexdigest()[:20], scope=scope,
                       title=_title(scope, known_height is None), description=" ".join(c.text for c in clauses),
                       clauses=clauses, caveats=list(dict.fromkeys(caveats)), conditional=True)


def build_room_profiles(hotel: Hotel, facts: list[Fact], specs: list[CharacteristicSpec], month: int = 9,
                        *, geo: dict | None = None, limit: int = 12) -> list[RoomProfile]:
    """Return a bounded representative set; omitted intervals are not merged."""
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    if limit <= 0:
        return []
    relevant = [f for f in facts if f.hotel_id == hotel.id and f.characteristic_id in _LABELS
                and f.characteristic_id != "beach_walk" and _month_matches(f, month)
                and not scope_values(f).get("room_type")]
    sides = [side for side in SIDES if any(scope_values(f).get("facade") == side for f in relevant)] or [None]
    high = hotel_floor_count(hotel) or 1
    scopes = []
    for side in sides:
        subset = [f for f in relevant if not scope_values(f).get("facade") or scope_values(f)["facade"] == side]
        scopes.extend(RoomScope(facade=side, floor_min=low, floor_max=upper, month=month)
                      for low, upper in _coalesced_intervals(high, subset))
    # Round-robin sides retains geographic diversity when a facade has many cuts.
    levels = {id(s): sum(t.facade == s.facade and t.floor_min < s.floor_min for t in scopes) for s in scopes}
    scopes.sort(key=lambda s: (levels[id(s)],
                               list(SIDES).index(s.facade) if s.facade else 0))
    cap = min(limit, 12)
    profiles = [describe_room(hotel, facts, specs, scope, geo=geo) for scope in scopes[:cap]]
    if len(scopes) > cap:
        for profile in profiles:
            profile.caveats.append(f"Показаны {cap} из {len(scopes)} модельных положений; остальные стороны или диапазоны не объединены с показанными.")
    return profiles
