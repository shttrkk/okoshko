"""Offline weighted fingerprints: no embeddings, downloads, or invented facts."""
from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher

from backend.agents.query_match import fresh, scope_values, value_at
from backend.contracts import CharacteristicSpec, CriterionVerdict, Fact, Hotel, ParsedQuery, SearchHit

WEIGHTS = {"sea_view": 2, "sunset_view": 2, "quiet_night": 2, "beach_walk": 2,
           "soundproofing": 1.5, "pets": 1.5, "connectivity": 1.5, "nightlife": 1.5}
SCALES = {"minutes": 20, "download_mbps": 100, "water_c": 15, "european_aqi": 80, "venues_500m": 60}


def _fingerprint(facts: list[Fact], specs: list[CharacteristicSpec]) -> dict:
    result = {}
    for spec in specs:
        choices = [f for f in facts if f.characteristic_id == spec.id and fresh(f) and f.evidence and f.supporting_claim_ids
                   and scope_values(f).get("month", 9) == 9 and value_at(f, spec.search.field) is not None]
        if not choices:
            continue
        # Existence of a view is a feature; eight non-view facades do not negate it.
        if spec.search.kind == "boolean":
            selected = max(choices, key=lambda f: (value_at(f, spec.search.field) is True, f.confidence))
        else:
            selected = max(choices, key=lambda f: f.confidence)
        result[spec.id] = (value_at(selected, spec.search.field), selected.confidence, selected)
    return result


def similar_hotels(reference_id: str, hotels: list[Hotel], facts: list[Fact],
                   specs: list[CharacteristicSpec], *, parsed: ParsedQuery | None = None,
                   explain_limit: int | None = None) -> list[SearchHit]:
    """Return evidence-backed profile similarity for September, excluding self."""
    if not any(h.id == reference_id for h in hotels):
        return []
    by_hotel = defaultdict(list)
    for fact in facts:
        by_hotel[fact.hotel_id].append(fact)
    reference = _fingerprint(by_hotel[reference_id], specs)
    if not reference:
        return []
    candidates = [h for h in hotels if h.id != reference_id]
    constrained = {}
    if parsed is not None and (parsed.must or parsed.nice):
        from backend.agents.ranker import rank_hotels
        # Explicit musts remain hard eligibility gates in similarity mode.
        # Nice conditions can move a candidate, without rescuing a failed must.
        constrained = {hit.hotel.id: hit for hit in rank_hotels(candidates, facts, parsed, specs)}
        candidates = [hotel for hotel in candidates if not parsed.must or constrained[hotel.id].group == "all"]
    fields = {s.id: s.search.field for s in specs}
    labels = {s.id: s.name_ru for s in specs}
    scored = []
    for hotel in candidates:
        fingerprint = _fingerprint(by_hotel[hotel.id], specs)
        numerator, denominator, shared = 0, 0, 0
        comparisons = []
        for cid, (left, left_conf, _) in reference.items():
            weight = WEIGHTS.get(cid, 1)*left_conf
            denominator += weight
            if cid not in fingerprint:
                continue
            right, right_conf, _ = fingerprint[cid]
            if isinstance(left, int | float) and not isinstance(left, bool) and isinstance(right, int | float) and not isinstance(right, bool):
                closeness = max(0, 1-abs(left-right)/SCALES.get(fields[cid], max(abs(left), 1)))
            else:
                closeness = float(left == right)
            numerator += weight*closeness*right_conf
            shared += 1
            comparisons.append((weight*closeness*right_conf, cid, closeness, left, right, fingerprint[cid][2]))
        score = 100*numerator/denominator if denominator else 0
        constraint = constrained.get(hotel.id)
        if parsed is not None and parsed.nice and constraint:
            score = .8*score + .2*constraint.score
        hit = SearchHit(hotel=hotel, score=round(score, 2),
                        group=constraint.group if constraint and parsed.must else "almost" if shared else "unknown",
                        must_satisfied=constraint.must_satisfied if constraint else 0,
                        must_total=constraint.must_total if constraint else 0)
        hit.summary = f"Сходство профиля {hit.score:g}/100: сопоставлены {shared} из {len(reference)} характеристик за сентябрь; учитывается уверенность. Это не гарантия выполнения одного набора условий в одном номере."
        if constraint and parsed.must:
            hit.summary += f" Дополнительные обязательные условия: {hit.must_satisfied} из {hit.must_total}."
        scored.append((hit, comparisons, constraint))
    scored.sort(key=lambda item: (-item[0].score, item[0].hotel.id))
    for index, (hit, comparisons, constraint) in enumerate(scored):
        if explain_limit is not None and index >= explain_limit:
            continue
        if constraint:
            hit.verdicts = constraint.verdicts
            hit.room_recommendation = constraint.room_recommendation
            hit.disappointments = constraint.disappointments
        else:
            hit.room_recommendation = "Сходство относится к профилю отеля; сторону, этаж и доступность номера уточните отдельно."
        # Profile comparisons are independent characteristics, not an invented
        # conjunction of views, noise and routes in one room.
        for _, cid, closeness, left, right, fact in sorted(comparisons, key=lambda row: (-row[0], row[1]))[:3]:
            text = f"{labels[cid]}: {'совпадает' if closeness == 1 else 'близкое значение' if closeness >= .7 else 'различается'} с выбранным отелем"
            if not isinstance(left, bool):
                text += f" ({right:g} против {left:g})" if isinstance(left, int | float) and isinstance(right, int | float) else f" ({right} против {left})"
            text += f"; уверенность {fact.confidence:.0%}."
            hit.similarity_reasons.append(text)
            if not any(v.characteristic_id == cid for v in hit.verdicts):
                hit.verdicts.append(CriterionVerdict(characteristic_id=cid, label=labels[cid],
                                                    status="yes" if closeness == 1 else "partial", symbol="✓" if closeness == 1 else "≈",
                                                    text=text, confidence=fact.confidence, fact_ids=[fact.id],
                                                    evidence_refs=[e.id for e in fact.evidence], synthetic=fact.synthetic))
        from backend.agents.ranker import relative_reason
        hit.why_not_higher = [relative_reason(hit, other[0]) for other in scored[max(0, index-3):index]]
    return [item[0] for item in scored]


# «похожий на <отель>»: deterministic name matching, no LLM and no network.
SIMILAR_PATTERN = re.compile(r"(?:похож\w*|аналог\w*|наподобие|вроде)\s+(?:на\s+)?(?P<rest>.+)")
LIKE_PATTERN = re.compile(r"(?:^|\s)как\s+(?:в\s+|у\s+)?(?P<rest>.+)")
NAME_STOPWORDS = {"отель", "отеля", "отелю", "гостиница", "гостиницу", "гостиницы", "hotel", "мини", "the"}
_TRANSLIT = (("sch", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("kh", "х"), ("ya", "я"), ("yu", "ю"), ("yo", "е"),
             ("ts", "ц"), ("a", "а"), ("b", "б"), ("c", "к"), ("d", "д"), ("e", "е"), ("f", "ф"), ("g", "г"), ("h", "х"),
             ("i", "и"), ("j", "дж"), ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("q", "к"),
             ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"), ("v", "в"), ("w", "в"), ("x", "кс"), ("y", "и"), ("z", "з"))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s+-]", " ", text.casefold().replace("ё", "е"))).strip()


def _translit(text: str) -> str:
    for latin, cyrillic in _TRANSLIT:
        text = text.replace(latin, cyrillic)
    return text


def _tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[\w+]+", text) if token not in NAME_STOPWORDS]


def _token_present(token: str, candidates: list[str]) -> bool:
    return any(token == other or len(token) >= 4 and SequenceMatcher(None, token, other).ratio() >= .8 for other in candidates)


def _name_score(rest: list[str], name: str) -> float:
    """1.0 exact name; .95 every name word in the query; .9 every query word in the name."""
    spelled = [*rest, *(_translit(token) for token in rest)]
    best = 0.0
    for variant in {_norm(name), _translit(_norm(name))}:
        tokens = _tokens(variant)
        if not tokens:
            continue
        if rest == tokens:
            return 1.0
        if all(_token_present(token, spelled) for token in tokens):
            best = max(best, .95)
        elif any(len(token) >= 4 for token in rest) and all(
                _token_present(token, tokens) or _token_present(_translit(token), tokens) for token in rest):
            best = max(best, .9)
    return best


def reference_hotel(text: str, hotels: list[Hotel]) -> tuple[Hotel | None, list[str]]:
    """Find «похожий на X» / «как X» in a query. Returns (hotel, ambiguous names)."""
    norm = _norm(text)
    match, exact_only = SIMILAR_PATTERN.search(norm), False
    if not match:
        match, exact_only = LIKE_PATTERN.search(norm), True
    if not match:
        return None, []
    rest = _tokens(re.split(r"\s(?:но|а|и|с|чтобы|только)\s|[,;.!?]", " " + match["rest"] + " ")[0])
    if not rest:
        return None, []
    scored = sorted(((_name_score(rest, hotel.name), hotel) for hotel in hotels), key=lambda item: (-item[0], item[1].id))
    best = scored[0][0] if scored else 0
    if best < (.95 if exact_only else .9):
        return None, []
    top = [hotel for score, hotel in scored if score == best]
    names = list(dict.fromkeys(hotel.name for hotel in top))
    if len(names) > 1:
        return None, names[:5]
    return top[0], []


def only_reference_phrase(text: str, name: str) -> bool:
    """Remove a recognized name fragment, never unrelated unknown conditions."""
    normalized = _norm(text)
    match = SIMILAR_PATTERN.fullmatch(normalized) or LIKE_PATTERN.fullmatch(normalized)
    if match is None:
        return False
    tokens = _tokens(match["rest"])
    names = _tokens(_norm(name)) + _tokens(_translit(_norm(name)))
    return bool(tokens) and all(_token_present(token, names) or _token_present(_translit(token), names) for token in tokens)
