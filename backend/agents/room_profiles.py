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
    FLOOR_DOMAIN_CAVEAT,
    FLOOR_DOMAIN_UNKNOWN,
    Match,
    assess_hotel,
    evaluate_fact,
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
    RoomAdvice,
    RoomAdviceOption,
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
# Accusative names for «Про … данных нет»; only what a guest asks about by default.
_ASK = {"sea_view": "вид на море", "quiet_night": "тишину ночью", "soundproofing": "шумоизоляцию",
        "sunset_view": "закат"}
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
        return _range(facts, "visible_minutes_min", "visible_minutes_max", minutes=True)
    if all(scope_values(f).get("floor_min") == scope_values(f).get("floor_max")
           and scope_values(f).get("floor_min") is not None for f in facts):
        return _range(facts, "visible_minutes", minutes=True)
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


def _brief(cid: str, facts: list[Fact], status: str, month: int) -> str | None:
    """One guest-facing sentence from facts already proven consistent for the slice."""
    values = [f.value if isinstance(f.value, dict) else {} for f in facts]
    month_in = "В " + _MONTHS_IN[month - 1]
    likely = status == "partial"
    if cid == "sea_view":
        return ("Море из окон, вероятно, видно — расчёт предварительный." if likely
                else "По расчёту из окон видно море." if status == "yes" else "По расчёту моря из окон не видно.")
    if cid == "mountain_view" and status != "no":
        return "Горы, вероятно, видны." if likely else "Видны горы."
    if cid == "quiet_night":
        noise = _range(facts, "night_db_min", "night_db_max")
        if noise:
            low = min(v["night_db_min"] for v in values)
            high = max(v["night_db_max"] for v in values)
            # Same bands as the map legend: up to 45, 45–55, above 55 dB.
            hint = ("тихо" if high <= 45 else "шумно" if low >= 55 else "умеренно" if low >= 45 and high <= 55
                    else "тихо или умеренно" if high <= 55 else "может быть шумно")
            return f"Ночью снаружи {noise} дБ по расчёту — {hint}."
        return ("Источник отмечает тишину ночью, но это предварительно." if likely
                else "Тишину ночью отмечает источник." if status == "yes" else "Тишину ночью источник не подтверждает.")
    if cid == "railway_noise":
        noise = _range(facts, "night_db_min", "night_db_max")
        return f"Шум железной дороги ночью — {noise} дБ по расчёту." if noise else None
    if cid == "soundproofing" and status != "no":
        details = [text for key, text in (("blackout", "плотные шторы"), ("air_conditioning", "кондиционер"))
                   if all(v.get(key) is True for v in values)]
        if all(v.get("windows") == "double" for v in values):
            details.insert(0, "двойные окна")
        if details:
            return "В источнике указаны " + " и ".join([", ".join(details[:-1]), details[-1]] if len(details) > 1 else details) + "."
        if likely and not all(v.get("available") is True for v in values):
            return None  # Window details alone do not state insulation.
        return "Источник отмечает шумоизоляцию, но это предварительно." if likely else "Шумоизоляцию отмечает источник."
    if cid == "morning_sun":
        minutes = _solar_minutes(facts)
        if status == "no":
            return f"{month_in} прямого утреннего солнца по расчёту мало."
        return f"{month_in} утром прямое солнце{' ' + minutes + ' мин' if minutes else ''}" + (
            ", расчёт предварительный." if likely else ".")
    if cid == "evening_heat":
        indexes = [v.get(k) for v in values for k in ("exposure_index", "exposure_index_min", "exposure_index_max")
                   if _number(v.get(k))]
        if not indexes:
            return None
        if max(indexes) <= .3:
            return "Вечером солнце в окна почти не попадает."
        if min(indexes) >= .6:
            return "Вечером окна сильно освещает солнце."
        return None
    if cid == "sunset_view" and status != "no":
        minutes = _solar_minutes(facts)
        aspects = [sunset_aspect(f) for f in facts]
        side = aspects and all(a is not None and a.oblique for a in aspects)
        return (f"{month_in} виден закат" + (f", {minutes} мин" if minutes else "") + (", сбоку" if side else "")
                + (" — расчёт предварительный." if likely else "."))
    return None


def _summary(briefs: dict[str, str | None], statuses: dict[str, str]) -> str:
    """Confirmed and likely first, then one «ask the hotel» sentence instead of a list of unknowns."""
    order = ("sea_view", "mountain_view", "quiet_night", "railway_noise", "soundproofing", "morning_sun",
             "evening_heat", "sunset_view")
    sentences = [briefs[cid] for cid in order if briefs.get(cid)]
    if briefs.get("sea_view") == "По расчёту из окон видно море." and briefs.get("mountain_view") == "Видны горы.":
        sentences = ["По расчёту из окон видно море и горы." if text == briefs["sea_view"] else text
                     for text in sentences if text != "Видны горы."]
    unknown = [_ASK[cid] for cid in _ASK if cid in statuses and not briefs.get(cid) and statuses[cid] != "no"]
    if unknown:
        listed = ", ".join(unknown[:-1]) + " и " + unknown[-1] if len(unknown) > 1 else unknown[0]
        sentences.append(f"Про {listed} точных данных нет — это можно уточнить у отеля.")
    return " ".join(sentences) or "Данных об этом положении пока мало — его можно уточнить у отеля."


def _clause(cid: str, matches: list[Match], month: int) -> tuple[RoomClause, list[str], str | None]:
    facts = _references(matches)
    statuses = {m.status for m in matches}
    caveats = []
    brief = None
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
        brief = _brief(cid, facts, status, month)
    synthetic = any(_synthetic(f) for f in facts)
    if synthetic:
        text = "Синтетические демонстрационные данные: " + text
    for fact in facts:
        for evidence in fact.evidence:
            caveats.extend(evidence.limitations)
    return RoomClause(text=text, characteristic_id=cid, status=status, synthetic=synthetic,
                      fact_ids=[f.id for f in facts],
                      evidence_refs=list(dict.fromkeys(e.id for f in facts for e in f.evidence))), caveats, brief


def _title(scope: RoomScope, unknown_height: bool) -> str:
    parts = [SIDES[scope.facade] + " сторона" if scope.facade else "сторона не уточнена"]
    low, high = scope.floor_min, scope.floor_max
    if unknown_height and low == high == 1:
        parts.append("условный уровень 1")
    elif low == high:
        parts.append(f"{low}-й этаж")
    else:
        parts.append(f"{low}–{high}-й этажи")
    return ", ".join(parts).capitalize()


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
        caveats.append(FLOOR_DOMAIN_CAVEAT + ".")
    if outside:
        caveats.append("Существование этажей за пределами известной этажности отеля не подтверждено.")
    briefs = {}
    for cid, matches in by_cid.items():
        clause, notes, briefs[cid] = _clause(cid, matches, scope.month)
        clauses.append(clause)
        caveats.extend(notes)
    if any(c.synthetic for c in clauses):
        caveats.append("Часть описания основана на синтетических демонстрационных данных; реальное подтверждение отеля нужно получить отдельно.")
    identity = json.dumps([hotel.id, scope.model_dump(mode="json")], sort_keys=True, separators=(",", ":"))
    return RoomProfile(id="position_" + hashlib.sha256(identity.encode()).hexdigest()[:20], scope=scope,
                       title=_title(scope, known_height is None),
                       description=_summary(briefs, {c.characteristic_id: c.status for c in clauses}),
                       clauses=clauses, caveats=list(dict.fromkeys(caveats)), conditional=True)


def _room_scopes(hotel: Hotel, facts: list[Fact], month: int,
                 characteristic_id: str | None = None, *, coalesce: bool = True) -> list[RoomScope]:
    """Partition the complete known domain before selecting or limiting positions."""
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    relevant = [f for f in facts if f.hotel_id == hotel.id and f.characteristic_id in _LABELS
                and f.characteristic_id != "beach_walk" and _month_matches(f, month)
                and not scope_values(f).get("room_type")
                and (characteristic_id is None or f.characteristic_id == characteristic_id)]
    sides = [side for side in SIDES if any(scope_values(f).get("facade") == side for f in relevant)] or [None]
    high = hotel_floor_count(hotel) or 1
    scopes = []
    for side in sides:
        subset = [f for f in relevant if not scope_values(f).get("facade") or scope_values(f)["facade"] == side]
        intervals = _coalesced_intervals(high, subset) if coalesce else _intervals(1, high, subset)
        scopes.extend(RoomScope(facade=side, floor_min=low, floor_max=upper, month=month)
                      for low, upper in intervals)
    # Round-robin sides retains geographic diversity when a facade has many cuts.
    levels = {id(s): sum(t.facade == s.facade and t.floor_min < s.floor_min for t in scopes) for s in scopes}
    scopes.sort(key=lambda s: (levels[id(s)],
                               list(SIDES).index(s.facade) if s.facade else 0))
    return scopes


def _profile_usefulness(profile: RoomProfile, facts_by_id: dict[str, Fact]) -> float:
    weights = {"sea_view": 8, "sunset_view": 6, "mountain_view": 4, "quiet_night": 3,
               "morning_sun": 2, "soundproofing": 1}
    total = 0
    for clause in profile.clauses:
        if clause.characteristic_id not in weights or clause.status not in {"yes", "partial"}:
            continue
        facts = [facts_by_id[fid] for fid in clause.fact_ids]
        # A mixed range or an unresolved disagreement is not a useful positive.
        if not facts or not all(fresh(f) and f.evidence and f.supporting_claim_ids
                                and isinstance(f.value, dict) and f.value.get("available") is True for f in facts):
            continue
        if clause.status == "partial" and min(f.confidence for f in facts) < .6:
            continue
        total += weights[clause.characteristic_id] * (1 if clause.status == "yes" else .5)
    return total


def build_room_profiles(hotel: Hotel, facts: list[Fact], specs: list[CharacteristicSpec], month: int = 9,
                        *, geo: dict | None = None, limit: int = 12) -> list[RoomProfile]:
    """Rank the complete partition, then return a bounded representative set."""
    scopes = _room_scopes(hotel, facts, month)
    if limit <= 0:
        return []
    profiles = [describe_room(hotel, facts, specs, scope, geo=geo) for scope in scopes]
    by_id = {f.id: f for f in facts}
    # Stable geographic round-robin order breaks ties without losing upper-floor
    # strengths simply because twelve lower-floor positions came first.
    profiles.sort(key=lambda profile: -_profile_usefulness(profile, by_id))
    cap = min(limit, 12)
    selected = profiles[:cap]
    if len(profiles) > cap:
        for profile in selected:
            profile.caveats.append(f"Показаны {cap} из {len(profiles)} положений; остальные стороны или диапазоны не объединены с показанными.")
    return selected


_SIDE_NAMES = {"N": "север", "NE": "северо-восток", "E": "восток", "SE": "юго-восток",
               "S": "юг", "SW": "юго-запад", "W": "запад", "NW": "северо-запад"}
_ADVICE_IDS = ("sea_view", "quiet_night", "morning_sun", "sunset_view")


def _join_words(parts: list[str]) -> str:
    return ", ".join(parts[:-1]) + " и " + parts[-1] if len(parts) > 1 else parts[0]


def _covers_floors(fact: Fact, low: int, high: int) -> bool:
    values = scope_values(fact)
    return values.get("floor_min", 1) <= low and values.get("floor_max", 100) >= high


def _noise_numeric(fact: Fact) -> bool:
    value = fact.value if isinstance(fact.value, dict) else {}
    return (_number(value.get("night_db_min")) and _number(value.get("night_db_max"))
            and 0 <= value["night_db_min"] <= value["night_db_max"])


def _advice_numeric_facts(options: list[RoomAdviceOption], by_id: dict[str, Fact], predicate) -> list[Fact]:
    """Numbers may describe the grouped advice only if they cover every slice."""
    result = {}
    for option in options:
        referenced = [by_id[fid] for fid in option.fact_ids]
        numeric = [f for f in referenced if predicate(f)]
        if any(not any(_covers_floors(f, low, high) for f in numeric)
               for low, high in _intervals(option.scope.floor_min, option.scope.floor_max, referenced)):
            return []
        result.update((f.id, f) for f in numeric)
    return sorted(result.values(), key=lambda f: f.id)


def _advice_options(hotel: Hotel, facts: list[Fact], specs: list[CharacteristicSpec], cid: str,
                    month: int) -> list[RoomAdviceOption]:
    relevant = sorted((f for f in facts if f.hotel_id == hotel.id and f.characteristic_id == cid
                       and _month_matches(f, month) and not scope_values(f).get("room_type")), key=lambda f: f.id)
    options = []
    for scope in _room_scopes(hotel, relevant, month, cid, coalesce=False):
        if not scope.facade:
            continue
        boolean_criterion = _criterion(cid, scope, scope.floor_min)
        criterion = boolean_criterion
        if cid == "quiet_night":
            # Compare numeric outdoor estimates, without changing available or
            # claiming that a preliminary estimate confirms quiet in a room.
            criterion = criterion.model_copy(update={"field": "night_db_max", "op": "gte", "value": 0})
        selected = [f for f in relevant if _eligible(f, scope)]
        parsed = ParsedQuery(text="", must=[criterion], facade=scope.facade, month=month)
        match = assess_hotel(selected, parsed, specs, hotel=hotel).matches[0]
        if cid == "quiet_night" and not (match.condition_met and match.status in {"yes", "partial"}):
            # A narrower agreeing answer can omit dB. It still has to pass the
            # resolver's freshness/conflict/unknown rules before numeric proof
            # from a wider, applicable domain is considered below.
            boolean_query = parsed.model_copy(update={"must": [boolean_criterion]})
            match = assess_hotel(selected, boolean_query, specs, hotel=hotel).matches[0]
        if not match.condition_met or match.status not in {"yes", "partial"}:
            continue
        # Keep applicable corroboration across source domains, including a
        # boolean room answer inside a wider numeric calculation. Contrary
        # observations must not disappear behind a stronger or narrower yes.
        witnesses = [f for f in selected if _covers_floors(f, scope.floor_min, scope.floor_max) and fresh(f)
                     and f.evidence and f.supporting_claim_ids]
        matches = []
        for f in witnesses:
            chosen = criterion
            if cid == "quiet_night" and not _noise_numeric(f):
                value = f.value if isinstance(f.value, dict) else {}
                if any(key in value for key in ("night_db_min", "night_db_max")):
                    break  # Incomplete/malformed numeric ranges are not corroboration.
                chosen = boolean_criterion
            matches.append(evaluate_fact(f, chosen, parsed, chosen.field, hotel=hotel))
        if len(matches) != len(witnesses):
            continue
        if not matches or not all(m.condition_met and m.status in {"yes", "partial"} for m in matches):
            continue
        if cid in {"sea_view", "sunset_view"} and not any(m.supported for m in matches):
            continue
        # Every named side needs an explicit witness in the selected evidence.
        if not any(scope_values(f).get("facade") == scope.facade for f in witnesses):
            continue
        numeric = [f for f in witnesses if _noise_numeric(f)] if cid == "quiet_night" else []
        if cid == "quiet_night" and not numeric:
            continue
        if cid == "quiet_night" and (max(f.value["night_db_min"] for f in numeric)
                                     > min(f.value["night_db_max"] for f in numeric)):
            continue  # Disjoint estimates for the same slice need reconciliation.
        options.append(RoomAdviceOption(scope=scope, status="partial" if any(
                       m.status == "partial" for m in matches) else "yes",
                       fact_ids=sorted(f.id for f in witnesses),
                       evidence_refs=sorted({e.id for f in witnesses for e in f.evidence}),
                       synthetic=any(_synthetic(f) for f in witnesses)))
    return options


def _merge_advice_options(options: list[RoomAdviceOption]) -> list[RoomAdviceOption]:
    merged: list[RoomAdviceOption] = []
    for option in sorted(options, key=lambda o: (list(SIDES).index(o.scope.facade), o.scope.floor_min)):
        if merged and merged[-1].scope.facade == option.scope.facade and (
                merged[-1].scope.floor_max + 1 == option.scope.floor_min):
            previous = merged[-1]
            previous.scope = previous.scope.model_copy(update={"floor_max": option.scope.floor_max})
            previous.fact_ids = sorted(set(previous.fact_ids + option.fact_ids))
            previous.evidence_refs = sorted(set(previous.evidence_refs + option.evidence_refs))
            previous.status = "partial" if "partial" in {previous.status, option.status} else "yes"
            previous.synthetic |= option.synthetic
        else:
            merged.append(option.model_copy(deep=True))
    return merged


def _advice_positions(options: list[RoomAdviceOption], hotel: Hotel) -> str:
    grouped: dict[tuple[int, int], list[str]] = {}
    for option in options:
        scope = option.scope
        grouped.setdefault((scope.floor_min, scope.floor_max), []).append(_SIDE_NAMES[scope.facade])
    phrases = []
    for (low, high), sides in grouped.items():
        if low > 1 and high == hotel_floor_count(hotel):
            floors = f"с {low}-го этажа"
        elif low == high:
            floors = f"{low}-й этаж"
        else:
            floors = f"{low}–{high}-й этажи"
        phrases.append(_join_words(sides) + " " + floors)
    return "; ".join(phrases)



def _named_rooms(rooms: list[str]) -> str:
    """Compress only explicitly enumerated adjacent room identifiers, never floors."""
    ordered = sorted(set(rooms), key=lambda room: (0, int(room), room) if room.isascii() and room.isdecimal() else (1, 0, room))
    result = []
    first = previous = None
    for room in ordered:
        adjacent = (previous is not None and first is not None and room.isascii() and room.isdecimal()
                    and previous.isascii() and previous.isdecimal()
                    and str(int(room)) == room and str(int(previous)) == previous
                    and int(room) == int(previous) + 1)
        if not adjacent and first is not None:
            result.append(first if first == previous else f"{first}–{previous}")
            first = None
        if first is None:
            first = room
        previous = room
    if first is not None:
        result.append(first if first == previous else f"{first}–{previous}")
    return ", ".join(result)


def _named_room_advice(hotel: Hotel, facts: list[Fact], month: int) -> RoomAdvice | None:
    """Confirmed room labels are separate from modelled facade/floor positions."""
    groups = []
    traits = ("sea_view", "balcony", "courtyard")
    for fact in sorted(facts, key=lambda fact: fact.id):
        if (fact.hotel_id != hotel.id or fact.characteristic_id != "room_orientation"
                or fact.status != "confirmed" or not fresh(fact) or not _month_matches(fact, month)
                or not fact.supporting_claim_ids or fact.conflicting_claim_ids or not isinstance(fact.value, dict)):
            continue
        quoted = [e for e in fact.evidence if e.quote and e.quote.strip()
                  and e.kind in {"hotelier", "simulation", "mock"}]
        if not quoted or not isinstance(fact.value.get("groups"), list):
            continue
        synthetic = _synthetic(fact) or any("hotelier_simulator" in e.source_ids for e in quoted)
        for group in fact.value["groups"]:
            if not isinstance(group, dict) or not isinstance(group.get("rooms"), list):
                continue
            rooms = [room.strip() for room in group["rooms"] if isinstance(room, str) and room.strip()]
            properties = {key: group[key] for key in traits if type(group.get(key)) is bool}
            if rooms and properties:
                groups.append((fact, rooms, properties, quoted, synthetic))
    # Even accidentally separate confirmed records must not create opposing
    # promises about the same named room. Keep only unambiguous room labels.
    observed = {}
    for _fact, rooms, properties, _quoted, _synthetic_group in groups:
        for room in rooms:
            for trait, value in properties.items():
                observed.setdefault((room, trait), set()).add(value)
    conflicting = {room for (room, _trait), values in observed.items() if len(values) > 1}
    phrases, used, evidence_refs = [], {}, set()
    demo = False
    labels = {"sea_view": ("без вида на море", "на море"),
              "balcony": ("без балкона", "с балконом"),
              "courtyard": ("окна не во двор", "окна во двор")}
    for fact, rooms, properties, quoted, synthetic in groups:
        rooms = [room for room in rooms if room not in conflicting]
        if not rooms:
            continue
        description = ", ".join(labels[trait][value] for trait, value in properties.items())
        when = f"В {_MONTHS_IN[fact.scope.month - 1]}: " if fact.scope.month else ""
        phrases.append(f"{when}Номера {_named_rooms(rooms)}: {description}.")
        used[fact.id] = fact
        evidence_refs.update(e.id for e in quoted)
        demo |= synthetic
    if not phrases:
        return None
    phrases = list(dict.fromkeys(phrases))
    caveats = ["Подтверждены только перечисленные группы и свойства. Сторону и этаж не выводим из номера комнаты. "
               "Наличие свободных номеров, цена, вместимость и размещение рядом требуют отдельного согласования."]
    if conflicting:
        caveats.append("По части номеров ответы расходятся; эти номера не включены в совет.")
    if demo:
        caveats.append("Ответ отеля · демо: это пример ответа, реальное подтверждение нужно получить отдельно.")
    identity = json.dumps([hotel.id, month, sorted(used), phrases, sorted(evidence_refs), demo], ensure_ascii=False)
    option = RoomAdviceOption(scope=RoomScope(), fact_ids=sorted(used), evidence_refs=sorted(evidence_refs),
                              status="partial" if demo else "yes", synthetic=demo)
    return RoomAdvice(id="advice_named_" + hashlib.sha256(identity.encode()).hexdigest()[:20],
                      characteristic_id="room_orientation", title="Номера по ответу отеля" if demo
                      else "Номера по подтверждённому ответу отеля", text=" ".join(phrases),
                      options=[option], fact_ids=option.fact_ids, evidence_refs=option.evidence_refs,
                      status=option.status, synthetic=demo, caveats=caveats)

def build_room_advice(hotel: Hotel, facts: list[Fact], specs: list[CharacteristicSpec], month: int = 9
                      ) -> list[RoomAdvice]:
    """Named hotel room groups plus four independently proven geographic goals."""
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    named = _named_room_advice(hotel, facts, month)
    if hotel_floor_count(hotel) is None:
        return [named] if named else []  # Named rooms never establish hotel floors.
    by_id = {f.id: f for f in facts}
    advice = [named] if named else []
    for cid in _ADVICE_IDS:
        options = _advice_options(hotel, facts, specs, cid, month)
        if cid == "quiet_night" and options:
            # Keep estimates in the lowest rounded upper-noise band. This is a
            # comparison among covered numeric slices, not all hotel windows.
            def upper(option):
                return max(by_id[fid].value["night_db_max"] for fid in option.fact_ids if _noise_numeric(by_id[fid]))
            best_band = min(math.floor(upper(o) + .5) for o in options)
            options = [o for o in options if math.floor(upper(o) + .5) == best_band]
        options = _merge_advice_options(options)
        if not options:
            continue
        fact_ids = sorted({fid for option in options for fid in option.fact_ids})
        selected_facts = [by_id[fid] for fid in fact_ids]
        status = "partial" if any(o.status == "partial" for o in options) else "yes"
        title = "Меньше уличного шума" if cid == "quiet_night" else _LABELS[cid]
        positions = _advice_positions(options, hotel)
        text = positions[0].upper() + positions[1:]
        caveats = [_POSITION_CAVEAT]
        if cid == "quiet_night":
            numeric_facts = _advice_numeric_facts(options, by_id, _noise_numeric)
            text += "; ночью " + _range(numeric_facts, "night_db_min", "night_db_max") + " дБ по расчёту"
            caveats.append("Сравниваются только стороны и этажи с численной оценкой наружного шума; для остальных сторон данных может не быть. Близкие округлённые оценки не означают достоверную разницу в шуме.")
        elif cid in _SOLAR:
            text += f" в {_MONTHS_IN[month - 1]}"
            numeric_facts = _advice_numeric_facts(options, by_id, lambda f: _solar_minutes([f]) is not None)
            minutes = _solar_minutes(numeric_facts) if numeric_facts else None
            if minutes:
                text += "; " + minutes + " мин" + (" утром" if cid == "morning_sun" else " перед закатом")
            if cid == "sunset_view":
                aspects = [sunset_aspect(f) for f in selected_facts]
                if all(a is not None and a.minimum_deg > 45 for a in aspects):
                    text += ", закат сбоку"
                elif all(a is not None and a.maximum_deg <= 45 for a in aspects):
                    text += ", закат перед окнами"
        text += "."
        _, notes = _known_text(cid, selected_facts, status, month)
        caveats.extend(notes)
        caveats.extend(note for f in selected_facts for e in f.evidence for note in e.limitations)
        if any(o.synthetic for o in options):
            caveats.append("Часть совета основана на демонстрационных данных; реальное подтверждение отеля нужно получить отдельно.")
        identity = json.dumps([hotel.id, cid, month, [o.model_dump(mode="json") for o in options]], sort_keys=True)
        advice.append(RoomAdvice(id="advice_" + hashlib.sha256(identity.encode()).hexdigest()[:20],
                      characteristic_id=cid, title=title, text=text, options=options, fact_ids=fact_ids,
                      evidence_refs=sorted({ref for option in options for ref in option.evidence_refs}),
                      status=status, synthetic=any(o.synthetic for o in options), caveats=list(dict.fromkeys(caveats))))
    return advice
