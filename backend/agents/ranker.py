"""Confidence-aware utility ranking, with one compatible room for all musts."""
from __future__ import annotations

from collections import defaultdict

from backend.agents.explainer import disappointments_from_assessment, room_from_assessment, verdict
from backend.agents.query_match import assess_hotel, fresh
from backend.contracts import CharacteristicSpec, Fact, Hotel, ParsedQuery, SearchHit


def rank_hotels(hotels: list[Hotel], facts: list[Fact], parsed: ParsedQuery,
                specs: list[CharacteristicSpec], *, explain_limit: int | None = None) -> list[SearchHit]:
    if "region_out_of_scope" in parsed.unmapped:
        return []
    by_hotel = defaultdict(list)
    for fact in facts:
        by_hotel[fact.hotel_id].append(fact)
    if not parsed.must and not parsed.nice:
        return browse_hotels(hotels, by_hotel, specs)
    ranked = []
    criteria = [*parsed.must, *parsed.nice]
    must_total = len(parsed.must)
    for hotel in hotels:
        hotel_facts = by_hotel[hotel.id]
        assessment = assess_hotel(hotel_facts, parsed, specs)
        required = assessment.matches[:must_total]
        satisfied = sum(m.supported for m in required)
        some_support = any(m.status in {"yes", "partial"} for m in required)
        group = "all" if satisfied == must_total else "almost" if some_support or all(m.status == "no" for m in required) else "unknown"
        weighted = [(m.utility, c.weight*(2 if i < must_total else 1)) for i, (c, m) in enumerate(zip(criteria, assessment.matches, strict=True))]
        score = 100 * sum(u*w for u, w in weighted) / sum(w for _, w in weighted) if weighted else 0
        tier = 3 if group == "all" else 2 if some_support else 1 if group == "unknown" else 0
        ranked.append((tier, satisfied, score, hotel.id, hotel, assessment, group))
    ranked.sort(key=lambda row: (-row[0], -row[1], -row[2], row[3]))
    hits = []
    for index, (_, satisfied, score, _, hotel, assessment, group) in enumerate(ranked):
        hit = SearchHit(hotel=hotel, score=round(score, 2), group=group,
                        must_satisfied=satisfied, must_total=must_total)
        hits.append(hit)
        # Scores, ordering and total are independent of the requested page size.
        # The API never serializes explanations for candidates outside its page.
        if explain_limit is not None and index >= explain_limit:
            continue
        required = assessment.matches[:must_total]
        hit.verdicts = [verdict(c, m, i < must_total, parsed,
                            conflict=assessment.scope_conflict and i < must_total and m.status not in {"yes", "partial"},
                            alternatives=assessment.alternatives.get(i)) for i, (c, m) in enumerate(zip(criteria, assessment.matches, strict=True))]
        if not must_total:
            summary = "Оценка по желательным условиям." if criteria else "Задайте условия, чтобы сравнить отели по подтверждённым фактам."
        elif group == "all":
            summary = f"Все обязательные условия выполнены по имеющимся данным для совместимого номера ({satisfied} из {must_total})."
            if any(m.status == "partial" for m in required):
                summary += " Часть условий рассчитана предварительно — проверьте оговорки и уверенность."
        elif assessment.scope_conflict:
            summary = "Есть компромисс: обязательные условия относятся к разным номерам. Одного подходящего номера не подтверждено."
        else:
            summary = f"Подтверждено {satisfied} из {must_total} обязательных условий; остальные не выполнены или требуют уточнения."
        hit.room_recommendation = room_from_assessment(assessment, parsed)
        hit.disappointments = disappointments_from_assessment(by_hotel[hotel.id], parsed, assessment)
        hit.summary = summary
        alternatives = []
        seen_names = set()
        for higher in reversed(hits[:-1]):
            if higher.hotel.name in seen_names:
                continue
            seen_names.add(higher.hotel.name)
            alternatives.append(relative_reason(hit, higher))
            if len(alternatives) == 3:
                break
        hit.why_not_higher = alternatives
    return hits


def relative_reason(hit: SearchHit, higher: SearchHit) -> str:
    """Compare adjacent ranked alternatives using only their actual verdicts."""
    prefix = f"«{higher.hotel.name}»: "
    if higher.must_satisfied > hit.must_satisfied:
        return prefix + f"подтверждено {higher.must_satisfied} из {higher.must_total} обязательных условий, здесь — {hit.must_satisfied}."
    priorities = {"yes": 3, "partial": 2, "unknown": 1, "no": 0}
    mine = {v.characteristic_id: v for v in hit.verdicts}
    for other in higher.verdicts:
        current = mine.get(other.characteristic_id)
        if current and priorities[other.status] > priorities[current.status]:
            return prefix + f"условие «{other.label}» лучше подтверждено; здесь {current.symbol} — {current.text}"
    if higher.score > hit.score:
        return prefix + f"выше совокупная оценка выполнения условий с учётом уверенности ({higher.score:g} против {hit.score:g}); проверьте оговорки в критериях."
    return prefix + "оценки совпадают; имеющиеся данные не дают основания считать этот отель лучше."


BROWSE_SUMMARY = "Условия запроса не распознаны: это не подбор, а обзор отелей региона. Сначала отели с наибольшим числом проверенных характеристик."


def browse_hotels(hotels: list[Hotel], by_hotel: dict, specs: list[CharacteristicSpec]) -> list[SearchHit]:
    """No recognized conditions: never claim a match; order by verified coverage."""
    active = {spec.id for spec in specs}
    rows = []
    for hotel in hotels:
        known = {fact.characteristic_id for fact in by_hotel[hotel.id] if fact.characteristic_id in active and fresh(fact)}
        score = round(100 * len(known) / len(active), 2) if active else 0
        rows.append((-score, hotel.id, SearchHit(hotel=hotel, score=score, group="browse",
                                                 summary=f"{BROWSE_SUMMARY} Проверено характеристик: {len(known)} из {len(active)}.")))
    return [row[-1] for row in sorted(rows, key=lambda row: row[:2])]


def similar_hotels(reference_id: str, hotels: list[Hotel], facts: list[Fact],
                   specs: list[CharacteristicSpec], *, parsed: ParsedQuery | None = None,
                   explain_limit: int | None = None) -> list[SearchHit]:
    """Compatibility export for API clients; implementation stays independent."""
    from backend.agents.similarity import similar_hotels as find_similar
    return find_similar(reference_id, hotels, facts, specs, parsed=parsed, explain_limit=explain_limit)
