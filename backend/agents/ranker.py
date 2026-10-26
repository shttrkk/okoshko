"""Confidence-aware utility ranking, with one compatible room for all musts."""
from __future__ import annotations

from collections import defaultdict

from backend.agents.explainer import disappointments_from_assessment, room_from_assessment, verdict
from backend.agents.query_match import assess_hotel
from backend.contracts import CharacteristicSpec, Fact, Hotel, ParsedQuery, SearchHit


def rank_hotels(hotels: list[Hotel], facts: list[Fact], parsed: ParsedQuery,
                specs: list[CharacteristicSpec]) -> list[SearchHit]:
    if "region_out_of_scope" in parsed.unmapped:
        return []
    by_hotel = defaultdict(list)
    for fact in facts:
        by_hotel[fact.hotel_id].append(fact)
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
        verdicts = [verdict(c, m, i < must_total, parsed,
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
        hit = SearchHit(hotel=hotel, score=round(score, 2), group=group,
                        must_satisfied=satisfied, must_total=must_total, verdicts=verdicts,
                        room_recommendation=room_from_assessment(assessment, parsed),
                        disappointments=disappointments_from_assessment(hotel_facts, parsed, assessment), summary=summary)
        tier = 3 if group == "all" else 2 if some_support else 1 if group == "unknown" else 0
        ranked.append((tier, satisfied, score, hotel.id, hit))
    ranked.sort(key=lambda row: (-row[0], -row[1], -row[2], row[3]))
    return [row[-1] for row in ranked]


def similar_hotels(reference_id: str, hotels: list[Hotel], facts: list[Fact],
                   specs: list[CharacteristicSpec]) -> list[SearchHit]:
    """Compatibility export for API clients; implementation stays independent."""
    from backend.agents.similarity import similar_hotels as find_similar
    return find_similar(reference_id, hotels, facts, specs)
