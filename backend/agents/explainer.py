"""Russian explanations assembled only from resolved facts and their evidence."""
from __future__ import annotations

from backend.agents.query_match import Assessment, Match, assess_hotel, fresh, scope_values
from backend.contracts import Criterion, CriterionVerdict, Fact, ParsedQuery

SIDES = {"N": "северная", "NE": "северо-восточная", "E": "восточная", "SE": "юго-восточная", "S": "южная", "SW": "юго-западная", "W": "западная", "NW": "северо-западная"}


def number(value) -> str:
    return f"{value:g}" if isinstance(value, int | float) else str(value)


def scope_text(fact: Fact) -> str:
    scope = scope_values(fact)
    parts = []
    if scope.get("facade"):
        parts.append(SIDES[scope["facade"]] + " сторона")
    low, high = scope.get("floor_min"), scope.get("floor_max")
    if low and high:
        parts.append(f"{low}-й этаж" if low == high else f"{low}–{high}-й этажи")
    elif low:
        parts.append(f"с {low}-го этажа")
    elif high:
        parts.append(f"до {high}-го этажа")
    if scope.get("room_type"):
        parts.append("тип номера «" + scope["room_type"] + "»")
    return ", ".join(parts)


def _details(fact: Fact, criterion: Criterion) -> str:
    value = fact.value if isinstance(fact.value, dict) else {}
    cid = fact.characteristic_id
    details = []
    if cid in {"sea_view", "sunset_view", "mountain_view", "room_orientation", "quiet_night", "soundproofing"}:
        place = scope_text(fact)
        if place:
            details.append(place)
    if cid == "quiet_night":
        low, high = value.get("night_db_min"), value.get("night_db_max")
        if low is not None and high is not None:
            details.append(f"модель наружного шума {number(low)}–{number(high)} дБ ночью")
    elif cid == "beach_walk":
        if value.get("minutes") is not None:
            details.append(f"до пляжа {number(value['minutes'])} мин пешком")
        if value.get("return_minutes") is not None:
            details.append(f"обратно {number(value['return_minutes'])} мин")
        if value.get("return_ascent_m") is not None:
            details.append(f"подъём обратно {number(value['return_ascent_m'])} м")
        if value.get("has_steps"):
            details.append("на маршруте есть лестницы")
        crossings = value.get("crossings")
        if crossings:
            details.append(f"переходов на маршруте: {len(crossings) if isinstance(crossings, list) else number(crossings)}")
    elif cid == "sunset_view":
        if value.get("visible_minutes") is not None:
            details.append(f"расчётная видимость заката {number(value['visible_minutes'])} мин")
    elif cid == "climate":
        month = fact.scope.month or value.get("month")
        if month:
            details.append(f"месяц {month}")
        if value.get("water_c") is not None:
            details.append(f"вода {number(value['water_c'])} °C по климатическим данным")
        if value.get("air_c") is not None:
            details.append(f"воздух {number(value['air_c'])} °C")
    elif cid == "connectivity" and value.get("download_mbps") is not None:
        details.append(f"загрузка {number(value['download_mbps'])} Мбит/с по тестам в районе")
    elif cid == "pets":
        if value.get("max_weight_kg") is not None:
            details.append(f"питомцы до {number(value['max_weight_kg'])} кг")
        if value.get("fee_rub_night") is not None:
            details.append(f"доплата {number(value['fee_rub_night'])} ₽/ночь")
    elif cid == "soundproofing":
        if value.get("windows"):
            details.append("двойные окна" if value["windows"] == "double" else "окна: " + str(value["windows"]))
        if value.get("blackout") is not None:
            details.append("плотные шторы есть" if value["blackout"] else "плотных штор нет")
        if value.get("air_conditioning") is not None:
            details.append("кондиционер есть" if value["air_conditioning"] else "кондиционера нет")
    elif cid == "air_quality" and value.get("european_aqi") is not None:
        details.append(f"European AQI {number(value['european_aqi'])}")
    elif cid == "night_pharmacy" and value.get("minutes") is not None:
        details.append(f"до аптеки {number(value['minutes'])} мин")
    if not details and criterion.field and value.get(criterion.field) is not None:
        actual = value[criterion.field]
        if not isinstance(actual, dict | list | bool):
            details.append(f"{criterion.label}: {number(actual)}")
    return "; ".join(details)


def verdict(criterion: Criterion, match: Match, required: bool, parsed: ParsedQuery, *, conflict: bool = False, alternatives: list[Fact] | None = None) -> CriterionVerdict:
    facts = list({f.id: f for f in [*match.facts, *(alternatives or [] if conflict else [])]}.values())
    selected = match.facts[0] if match.facts else None
    detail = _details(selected, criterion) if selected else ""
    prefixes = {"yes": "Да, но учитывайте условия", "partial": "Предварительно", "no": "Нет", "unknown": "Не подтверждено"}
    text = prefixes[match.status] + ": " + (detail or match.reason)
    caveats = []
    if match.status in {"unknown", "partial"}:
        caveats.append(match.reason)
    if selected and selected.characteristic_id == "quiet_night":
        caveats.append("Приближённая модель наружного шума с погрешностью около ±8 дБ; сон и шумоизоляцию окон она не гарантирует. Уточните окна у отеля")
    if selected and selected.characteristic_id == "connectivity":
        caveats.append("Это агрегированные тесты в районе, а не проверка Wi-Fi конкретного номера")
    if selected and selected.characteristic_id == "climate":
        caveats.append("Климатические данные за прошлые периоды, не прогноз погоды на даты поездки")
    if selected and selected.characteristic_id == "beach_walk":
        caveats.append("Расчёт времени ходьбы по модели Тоблера; лестницы и переходы могут замедлить путь")
    if any(f.synthetic or any(e.kind == "simulation" for e in f.evidence) for f in facts):
        caveats.append("Синтетические данные демонстрационного симулятора; реальный ответ отеля ещё нужно получить")
    if conflict:
        caveats.append("Подходящие сведения относятся к разным сторонам, этажам или типам номера. Один номер со всеми обязательными условиями не подтверждён")
    for fact in facts:
        for evidence in fact.evidence:
            caveats.extend(evidence.limitations)
    return CriterionVerdict(characteristic_id=criterion.characteristic_id, label=criterion.label,
                            required=required, status=match.status, symbol={"yes": "✓", "partial": "≈", "no": "✗", "unknown": "?"}[match.status],
                            text=text, confidence=match.confidence, fact_ids=[f.id for f in facts],
                            evidence_refs=list(dict.fromkeys(e.id for f in facts for e in f.evidence)),
                            caveat="; ".join(dict.fromkeys(caveats)) or None,
                            disputed=any(f.status == "disputed" for f in facts),
                            synthetic=any(f.synthetic or any(e.kind == "simulation" for e in f.evidence) for f in facts))


def _default_query(facts: list[Fact]) -> ParsedQuery:
    # Detail pages may request a room without a search. Only propose views that
    # actually have some positive evidence, rather than asking for every view.
    labels = {"sea_view": "Вид на море", "sunset_view": "Закат из окна", "mountain_view": "Вид на горы"}
    criteria = [Criterion(characteristic_id=cid, label=label, field="available") for cid, label in labels.items()
                if any(f.characteristic_id == cid and fresh(f) and isinstance(f.value, dict) and f.value.get("available") is True for f in facts)]
    return ParsedQuery(text="", must=criteria)


def room_from_assessment(assessment: Assessment, parsed: ParsedQuery) -> str:
    if assessment.scope_conflict:
        return "Один номер со всеми обязательными условиями не подтверждён: подходящие данные относятся к разным сторонам, этажам или типам номера. Уточните компромисс у отеля."
    if any(m.status == "no" for m in assessment.matches[:len(parsed.must)]):
        return "Номер со всеми обязательными условиями не найден в имеющихся данных. Уточните у отеля альтернативный номер или измените условие."
    relevant = assessment.matches[:len(parsed.must)] or assessment.matches
    supported = [f for m in relevant if m.status in {"yes", "partial"} for f in m.facts]
    scoped = [scope_values(f) for f in supported if any(scope_values(f).get(k) is not None for k in ("facade", "floor_min", "floor_max", "room_type"))]
    if not scoped:
        return "Уточните сторону, этаж и доступность конкретного номера у отеля."
    context = assessment.context
    requested = [c.scope.model_dump(exclude_none=True) for c in parsed.must if c.scope]
    low = max([s.get("floor_min", 1) for s in [*scoped, *requested]] + [parsed.min_floor or 1])
    highs = [s["floor_max"] for s in [*scoped, *requested] if s.get("floor_max")]
    high = min(highs) if highs else None
    parts = []
    if context.facade:
        parts.append(SIDES[context.facade] + " сторона")
    if high:
        parts.append(f"{low}-й этаж" if low == high else f"{low}–{high}-й этажи")
    elif low > 1:
        parts.append(f"от {low}-го этажа")
    if context.room_type:
        parts.append("тип «" + context.room_type + "»")
    if not parts:
        return "Уточните сторону и этаж конкретного номера у отеля."
    complete = bool(relevant) and all(m.status == "yes" for m in relevant)
    prefix = "Просите номер: " if complete else "Можно уточнить номер: "
    result = prefix + ", ".join(parts) + "."
    if not complete:
        result += " Выполнение всех обязательных условий пока не подтверждено."
    return result


def recommend_room(facts: list[Fact], parsed: ParsedQuery | None = None) -> str:
    parsed = parsed or _default_query(facts)
    return room_from_assessment(assess_hotel(facts, parsed), parsed)


def disappointments_from_assessment(facts: list[Fact], parsed: ParsedQuery, assessment: Assessment) -> list[str]:
    notes = []
    if assessment.scope_conflict:
        notes.append("Обязательные условия найдены в разных номерах; общего сочетания стороны, этажа и типа номера нет в подтверждённых данных.")
    for criterion, match in zip([*parsed.must, *parsed.nice], assessment.matches, strict=True):
        if match.status == "no":
            notes.append(criterion.label + ": условие не выполняется для выбранного номера.")
        elif match.status in {"partial", "unknown"}:
            notes.append(criterion.label + ": " + match.reason.lower() + ".")
    selected = {f.id: f for m in assessment.matches for f in m.facts}
    # Route disadvantages concern the hotel, so they remain useful on detail pages.
    selected.update({f.id: f for f in facts if f.characteristic_id == "beach_walk" and fresh(f)})
    for fact in selected.values():
        value = fact.value if isinstance(fact.value, dict) else {}
        if fact.characteristic_id == "quiet_night":
            notes.append("Наружный шум рассчитан приближённо (около ±8 дБ); это не гарантия сна. Шумоизоляцию номера уточните отдельно.")
        if fact.characteristic_id == "beach_walk":
            if isinstance(value.get("return_minutes"), int | float) and isinstance(value.get("minutes"), int | float) and value["return_minutes"] > value["minutes"]:
                notes.append(f"Обратно с пляжа дольше: {number(value['return_minutes'])} мин вместо {number(value['minutes'])} мин.")
            if value.get("return_ascent_m"):
                notes.append(f"На обратном пути подъём {number(value['return_ascent_m'])} м.")
            if value.get("has_steps"):
                notes.append("На пути к пляжу есть лестницы — проверьте маршрут, если едете с коляской.")
            if value.get("crossings"):
                notes.append("На пути к пляжу есть переходы; откройте маршрут перед выбором отеля.")
        if fact.synthetic:
            notes.append("Часть условий взята из синтетического ответа симулятора, а не подтверждена реальным отелем.")
    return list(dict.fromkeys(notes))[:8]


def disappointments(facts: list[Fact], parsed: ParsedQuery | None = None) -> list[str]:
    parsed = parsed or _default_query(facts)
    return disappointments_from_assessment(facts, parsed, assess_hotel(facts, parsed))
