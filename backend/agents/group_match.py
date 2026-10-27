"""Independent model positions in one hotel; never an allocation of real rooms."""

from __future__ import annotations

import json
from collections import defaultdict

from backend.agents.explainer import room_from_assessment, verdict
from backend.agents.query_match import (
    ROOM_CHARACTERISTICS,
    assess_hotel,
    fresh,
    hotel_floor_count,
    scope_values,
)
from backend.agents.query_text import source_phrase
from backend.catalogue import searchable
from backend.contracts import (
    CharacteristicSpec,
    CriterionVerdict,
    Fact,
    Hotel,
    ParsedQuery,
    RoomPlanItem,
    RoomRequest,
    RoomScope,
    Scope,
    SearchHit,
)

INVENTORY_CAVEAT = "Сколько таких номеров свободно, их вместимость и соседство уточнит отель."


def room_query(room: RoomRequest, parsed: ParsedQuery) -> ParsedQuery:
    """A single-room query, retaining global room wishes without crossing guests."""
    scope = room.scope
    groups = {}
    for name in ("must", "nice"):
        items = [
            *getattr(room, name),
            *[c for c in getattr(parsed, name) if c.characteristic_id in ROOM_CHARACTERISTICS],
        ]
        result = []
        for original in items:
            criterion = original.model_copy(deep=True)
            wanted = criterion.scope or Scope()
            values = wanted.model_dump()
            for field in ("facade", "room_type", "month"):
                value = getattr(scope, field)
                if value is not None and values.get(field) not in (None, value):
                    raise ValueError("Противоречивые области действия пожелания номера")
                if value is not None:
                    values[field] = value
            lows = [v for v in (wanted.floor_min, scope.floor_min) if v is not None]
            highs = [v for v in (wanted.floor_max, scope.floor_max) if v is not None]
            values["floor_min"] = max(lows) if lows else None
            values["floor_max"] = min(highs) if highs else None
            criterion.scope = Scope.model_validate(values)
            result.append(criterion)
        groups[name] = result
    return ParsedQuery(
        text=parsed.text,
        must=groups["must"],
        nice=groups["nice"],
        facade=scope.facade,
        min_floor=scope.floor_min,
        month=scope.month or parsed.month,
        check_in=parsed.check_in,
        check_out=parsed.check_out,
        no_data=room.no_data,
        no_data_must=room.no_data_must,
        unmapped=room.unmapped,
        clarifications=room.clarifications,
    )


def _type_witnesses(room: RoomRequest, hotel: Hotel, facts: list[Fact]) -> list[Fact]:
    scope = room.scope
    count = hotel_floor_count(hotel)
    witnesses = []
    for fact in facts:
        values = scope_values(fact)
        if not (fresh(fact) and fact.confidence >= 0.6 and fact.evidence and fact.supporting_claim_ids):
            continue
        if isinstance(fact.value, dict) and fact.value.get("available") is False:
            continue
        if values.get("room_type") != scope.room_type:
            continue
        if scope.facade and values.get("facade") != scope.facade:
            continue
        if values.get("month") not in (None, scope.month):
            continue
        low = max(scope.floor_min or 1, values.get("floor_min") or 1)
        high = min(scope.floor_max or count or 100, values.get("floor_max") or count or 100)
        if low <= high:
            witnesses.append(fact)
    return witnesses


def _domain(room: RoomRequest, hotel: Hotel, facts: list[Fact]) -> str | None:
    scope = room.scope
    count = hotel_floor_count(hotel)
    if scope.floor_min is not None or scope.floor_max is not None:
        if count is None:
            return "Этажность отеля неизвестна; пожелание по этажам требует подтверждения"
        if (scope.floor_min or 1) > count:
            return "Запрошенные этажи выходят за пределы известной этажности"
    if scope.room_type and not _type_witnesses(room, hotel, facts):
        return "Тип номера в запрошенном положении не подтверждён; номерного фонда в прототипе нет"
    if scope.facade and not any(fresh(f) and scope_values(f).get("facade") == scope.facade for f in facts):
        return "Для запрошенной стороны нет подтверждающих данных"
    return None


def _position(
    assessment, room: RoomRequest, hotel: Hotel, month: int, complete: bool, facts: list[Fact]
) -> RoomScope:
    if not complete:
        return room.scope.model_copy(update={"month": room.scope.month or month})
    if room.scope.room_type and not assessment.matches:
        witness = max(_type_witnesses(room, hotel, facts), key=lambda fact: fact.confidence)
        values = scope_values(witness)
        low = max(room.scope.floor_min or 1, values.get("floor_min") or 1)
        return RoomScope(
            facade=room.scope.facade or values.get("facade"),
            floor_min=low,
            floor_max=low,
            room_type=room.scope.room_type,
            month=room.scope.month or month,
        )
    if not assessment.matches:
        low = room.scope.floor_min or 1
        high = min(room.scope.floor_max or hotel_floor_count(hotel) or low, hotel_floor_count(hotel) or low)
        return RoomScope(
            facade=room.scope.facade, floor_min=low, floor_max=high, month=room.scope.month or month
        )
    context = assessment.context
    # The evaluator proves one context, not every floor in its source range.
    # Interior negative slices and criterion-specific bounds may split it.
    floor = context.floor
    return RoomScope(
        facade=context.facade or room.scope.facade,
        floor_min=floor,
        floor_max=floor,
        month=room.scope.month or month,
        room_type=context.room_type or room.scope.room_type,
    )


def evaluate_room(
    hotel: Hotel,
    facts: list[Fact],
    parsed: ParsedQuery,
    room: RoomRequest,
    specs: list[CharacteristicSpec],
    *,
    describe=None,
):
    from backend.agents.ranker import criterion_utility

    try:
        if parsed.facade and room.scope.facade and parsed.facade != room.scope.facade:
            raise ValueError("Общая сторона противоречит стороне этого запроса номера")
        lower = [v for v in (parsed.min_floor, room.scope.floor_min) if v is not None]
        values = room.scope.model_dump()
        values.update(
            month=room.scope.month or parsed.month,
            facade=room.scope.facade or parsed.facade,
            floor_min=max(lower) if lower else None,
        )
        room = room.model_copy(update={"scope": RoomScope.model_validate(values)})
        query = room_query(room, parsed)
    except ValueError as error:
        return (
            RoomPlanItem(
                label=room.label, scope=room.scope, recommendation=str(error), caveats=[INVENTORY_CAVEAT]
            ),
            False,
            0,
            1,
            [],
        )
    from backend.agents.room_profiles import _month_matches

    applicable = [fact for fact in facts if _month_matches(fact, query.month)]
    assessment = assess_hotel(applicable, query, specs, hotel=hotel)
    required = assessment.matches[: len(query.must)]
    reason = _domain(room, hotel, facts) or assessment.domain_reason
    unsupported = len(room.no_data_must) + int(bool(room.unmapped))
    scoped_required = bool(
        any(getattr(room.scope, key) is not None for key in ("facade", "floor_min", "floor_max", "room_type"))
    )
    total = len(required) + unsupported + int(scoped_required)
    satisfied = sum(match.supported for match in required) + int(scoped_required and reason is None)
    complete = satisfied == total and not reason
    if reason or unsupported:
        state = "partial" if any(m.supported for m in required) else "unknown"
    elif required:
        state = (
            ("partial" if any(m.status == "partial" for m in required) else "yes")
            if complete
            else ("no" if any(m.status == "no" for m in required) else "partial" if satisfied else "unknown")
        )
    elif scoped_required:
        state = "partial" if complete else "unknown"
    else:
        state = (
            "unknown"
            if not assessment.matches
            else "yes"
            if all(m.status == "yes" for m in assessment.matches)
            else "partial"
        )
    scope = _position(assessment, room, hotel, parsed.month, complete, facts)
    item = RoomPlanItem(
        label=room.label,
        scope=scope,
        status=state,
        recommendation=reason or room_from_assessment(assessment, query),
        # The inventory caveat is shown once per plan; the parser's own
        # «Не удалось сопоставить» note would repeat the unmapped line below.
        caveats=[
            *[c for c in room.clarifications if not c.startswith("Не удалось сопоставить")],
            *(
                [
                    "Не учли: «"
                    + "», «".join(source_phrase(parsed.text, fragment) for fragment in room.unmapped)
                    + "» — этого пока нет в каталоге, уточните у отеля."
                ]
                if room.unmapped
                else []
            ),
        ],
    )
    criteria = [*query.must, *query.nice]
    item.verdicts = [
        verdict(
            c,
            m,
            i < len(query.must),
            query,
            conflict=assessment.scope_conflict and i < len(query.must) and not m.supported,
            alternatives=assessment.alternatives.get(i),
        )
        for i, (c, m) in enumerate(zip(criteria, assessment.matches, strict=True))
    ]
    missing = list(room.no_data)
    for criterion in room.no_data_must:
        if criterion not in missing:
            missing.append(criterion)
    for criterion in missing:
        item.verdicts.append(
            CriterionVerdict(
                characteristic_id=criterion.characteristic_id,
                label=criterion.label,
                required=criterion in room.no_data_must,
                status="unknown",
                symbol="?",
                confidence=0,
                text="Нет подтверждающих данных для этого пожелания номера",
            )
        )
    if not criteria and not scoped_required:
        item.recommendation = "Особых условий нет — подойдёт любой номер."
        if not room.unmapped:
            item.status = "yes"
        item.scope = RoomScope(month=room.scope.month or parsed.month)
    elif not criteria and complete:
        item.recommendation = (
            "Этажи входят в известный диапазон; наличие номера уточнит отель"
        )
    if describe is not None:
        item.profile = describe(hotel, facts, specs, scope)
    weighted = [
        (criterion_utility(m, c), c.weight * (2 if i < len(query.must) else 1))
        for i, (c, m) in enumerate(zip(criteria, assessment.matches, strict=True))
    ]
    return item, complete, satisfied, total, weighted


def _plan_summary(plan: list[RoomPlanItem], complete: bool) -> str:
    fitting = sum(item.status == "yes" for item in plan)
    if complete:
        return f"В одном отеле нашлось подходящее положение номера для всех {len(plan)}."
    if fitting:
        return f"Подходящее положение нашлось для {fitting} из {len(plan)}; остальное — в плане ниже."
    return "Ни для кого пока нет подтверждённого положения — проверьте план ниже."


def rank_group_hotels(
    hotels: list[Hotel],
    facts: list[Fact],
    parsed: ParsedQuery,
    specs: list[CharacteristicSpec],
    *,
    explain_limit: int | None = None,
) -> list[SearchHit]:
    from backend.agents.ranker import criterion_utility

    if "region_out_of_scope" in parsed.unmapped:
        return []
    by_hotel = defaultdict(list)
    for fact in facts:
        by_hotel[fact.hotel_id].append(fact)
    shared = parsed.model_copy(
        deep=True,
        update={
            "rooms": [],
            "facade": None,
            "min_floor": None,
            "must": [c for c in parsed.must if c.characteristic_id not in ROOM_CHARACTERISTICS],
            "nice": [c for c in parsed.nice if c.characteristic_id not in ROOM_CHARACTERISTICS],
        },
    )
    has_conditions = bool(
        shared.must
        or shared.nice
        or shared.no_data_must
        or any(
            r.must
            or r.nice
            or r.no_data_must
            or r.unmapped
            or any(getattr(r.scope, k) is not None for k in ("facade", "floor_min", "floor_max", "room_type"))
            for r in parsed.rooms
        )
    )
    rows = []
    for hotel in hotels:
        if not searchable(hotel):
            continue
        local = by_hotel[hotel.id]
        assessment = assess_hotel(local, shared, specs, hotel=hotel)
        needed = assessment.matches[: len(shared.must)]
        total = len(needed) + len(shared.no_data_must)
        satisfied = sum(m.supported for m in needed)
        complete = satisfied == total and not assessment.domain_reason
        criteria = [*shared.must, *shared.nice]
        weighted = [
            (criterion_utility(m, c), c.weight * (2 if i < len(shared.must) else 1))
            for i, (c, m) in enumerate(zip(criteria, assessment.matches, strict=True))
        ]
        plan = []
        evaluated = {}
        for request in parsed.rooms:
            key = json.dumps(request.model_dump(mode="json", exclude={"label"}), sort_keys=True)
            if key not in evaluated:
                evaluated[key] = evaluate_room(hotel, local, parsed, request, specs)
            original, ok, got, count, weights = evaluated[key]
            item = original.model_copy(deep=True, update={"label": request.label})
            plan.append(item)
            complete = complete and ok
            satisfied += got
            total += count
            weighted.extend(weights)
        group = (
            "browse" if not has_conditions else "all" if complete else "almost" if satisfied else "unknown"
        )
        score = (
            round(
                100
                * sum(value * weight for value, weight in weighted)
                / sum(weight for _, weight in weighted),
                2,
            )
            if weighted
            else 0
        )
        hit = SearchHit(
            hotel=hotel,
            score=score,
            group=group,
            must_satisfied=satisfied,
            must_total=total,
            room_plan=plan,
            verdicts=[
                verdict(c, m, i < len(shared.must), shared)
                for i, (c, m) in enumerate(zip(criteria, assessment.matches, strict=True))
            ],
            summary=_plan_summary(plan, complete and has_conditions),
            disappointments=[INVENTORY_CAVEAT],
        )
        rows.append(hit)
    rows.sort(
        key=lambda h: (
            {"all": 0, "almost": 1, "unknown": 2, "browse": 3}[h.group],
            -h.must_satisfied,
            -h.score,
            h.hotel.id,
        )
    )
    from backend.agents.room_profiles import describe_room

    for hit in rows[:explain_limit] if explain_limit is not None else rows:
        if describe_room:
            for item in hit.room_plan:
                # Without a side there is no position to describe, only a list of unknowns.
                if item.scope.facade:
                    item.profile = describe_room(hit.hotel, by_hotel[hit.hotel.id], specs, item.scope)
    return rows
