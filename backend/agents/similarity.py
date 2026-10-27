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
UNITS = {"minutes": "мин", "return_minutes": "мин", "download_mbps": "Мбит/с", "upload_mbps": "Мбит/с",
         "water_c": "°C", "air_c": "°C", "european_aqi": "AQI", "venues_500m": "заведений"}


def _profile_value(value, field: str, unit: str | None) -> str:
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, int | float):
        suffix = unit or UNITS.get(field)
        return f"{value:g}".replace(".", ",") + (" " + suffix if suffix else "")
    return str(value)


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
    if parsed is not None and (parsed.must or parsed.nice or parsed.no_data_must or parsed.rooms):
        from backend.agents.ranker import rank_hotels
        # Explicit musts remain hard eligibility gates in similarity mode.
        # Nice conditions can move a candidate, without rescuing a failed must.
        constrained = {hit.hotel.id: hit for hit in rank_hotels(candidates, facts, parsed, specs)}
        candidates = [hotel for hotel in candidates if parsed.rooms or not parsed.must or constrained[hotel.id].must_satisfied == len(parsed.must)]
    fields = {s.id: s.search.field for s in specs}
    labels = {s.id: s.name_ru for s in specs}
    units = {s.id: s.search.unit for s in specs}
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
                        group=constraint.group if constraint and (parsed.must or parsed.no_data_must or parsed.rooms) else "almost" if shared else "unknown",
                        must_satisfied=constraint.must_satisfied if constraint else 0,
                        must_total=constraint.must_total if constraint else 0)
        hit.summary = f"Сходство профиля {hit.score:g}/100: сопоставлены {shared} из {len(reference)} характеристик за сентябрь; учитывается уверенность. Это не гарантия выполнения одного набора условий в одном номере."
        if constraint and (parsed.must or parsed.no_data_must or parsed.rooms):
            hit.summary += f" Дополнительные обязательные условия: {hit.must_satisfied} из {hit.must_total}."
        if any(item[2].synthetic for item in reference.values()) or any(row[5].synthetic for row in comparisons):
            hit.disappointments.append("Часть сравнения основана на демо-данных; подтвердите эти условия у отеля.")
        scored.append((hit, comparisons, constraint))
    scored.sort(key=lambda item: (-item[0].score, item[0].hotel.id))
    for index, (hit, comparisons, constraint) in enumerate(scored):
        if explain_limit is not None and index >= explain_limit:
            continue
        if constraint:
            hit.verdicts = constraint.verdicts
            hit.room_recommendation = constraint.room_recommendation
            hit.room_plan = constraint.room_plan
            hit.room_profile = constraint.room_profile
            hit.disappointments = list(dict.fromkeys([*hit.disappointments, *constraint.disappointments]))
        else:
            hit.room_recommendation = "Сходство относится к профилю отеля; сторону, этаж и доступность номера уточните отдельно."
        # Profile comparisons are independent characteristics, not an invented
        # conjunction of views, noise and routes in one room.
        matches = sorted((row for row in comparisons if row[3] == row[4]), key=lambda row: (-row[0], row[1]))[:3]
        differences = sorted((row for row in comparisons if row[3] != row[4]
                              and _profile_value(row[3], fields[row[1]], units[row[1]]) != _profile_value(row[4], fields[row[1]], units[row[1]])),
                             key=lambda row: (-WEIGHTS.get(row[1], 1)*min(reference[row[1]][1], row[5].confidence)*(1-row[2]), row[1]))[:2]
        for _, cid, closeness, left, right, fact in [*matches, *differences]:
            left_text = _profile_value(left, fields[cid], units[cid])
            right_text = _profile_value(right, fields[cid], units[cid])
            if left == right:
                text = f"Совпадает «{labels[cid]}»: {right_text} у обоих"
            else:
                text = f"Отличается «{labels[cid]}»: здесь {right_text}, у выбранного отеля {left_text}"
            text += f"; уверенность фактов {fact.confidence:.0%} / {reference[cid][1]:.0%}."
            synthetic = fact.synthetic or reference[cid][2].synthetic
            hit.similarity_reasons.append(text)
            if not any(v.characteristic_id == cid for v in hit.verdicts):
                hit.verdicts.append(CriterionVerdict(characteristic_id=cid, label=labels[cid],
                                                    status="yes" if closeness == 1 else "partial", symbol="✓" if closeness == 1 else "≈",
                                                    text=text, confidence=fact.confidence, fact_ids=[fact.id],
                                                    evidence_refs=[e.id for e in fact.evidence], synthetic=synthetic))
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
    """Resolve a similarity request to catalogue identity; never invent IDs."""
    english = re.match(
        r'^\s*(?:please\s+)?(?:i\s+(?:want|need)\s+)?'
        r'(?:something\s+like|similar\s+to|(?:find|show)\s+(?:(?:me|us)\s+)?'
        r'(?:(?:a|an|some)\s+)?hotels?\s+(?:like|similar\s+to))\s+(?P<rest>.+)',
        text.casefold().replace('ё', 'е'))
    if english:
        rest = english['rest']
        words = list(re.finditer(r'[\w+-]+', rest))
        # Full names must occupy the beginning of the reference operand. Keep
        # punctuation until the boundary check: a comma can introduce a wish.
        candidates = []
        for hotel in hotels:
            name = re.findall(r'[\w+-]+', _norm(hotel.name))
            if not name or len(words) < len(name) or [m[0] for m in words[:len(name)]] != name:
                continue
            suffix = rest[words[len(name)-1].end():].lstrip(' \t\"\'»”')
            if suffix and not re.match(r'[,;.!?]|(?:but|with|and|where|in|for|from|only|without|except|on)\b', suffix):
                continue
            candidates.append((len(name), len(_norm(hotel.name)), hotel))
        if not candidates:
            return None, []
        longest = max((words, length) for words, length, _ in candidates)
        top = sorted((hotel for words, length, hotel in candidates if (words, length) == longest), key=lambda hotel: hotel.id)
        if len({hotel.id for hotel in top}) > 1:
            return None, list(dict.fromkeys(hotel.name for hotel in top))[:5]
        return top[0], []
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
    english = re.fullmatch(
        r'(?:please\s+)?(?:i\s+(?:want|need)\s+)?'
        r'(?:something\s+like|similar\s+to|(?:find|show)\s+(?:(?:me|us)\s+)?'
        r'(?:(?:a|an|some)\s+)?(?:(?:hotels?|отел\w*)\s+)?(?:like|similar\s+to))\s+(?P<rest>.+)',
        normalized)
    if english:
        # The caller already verified this name. The parser may have removed
        # hotel/article words and numbers from an unmapped fragment, but any
        # remaining amenity/price term must keep the whole fragment visible.
        rest = re.sub(r'\s+(?:but|and|with)$', '', english['rest'])
        tokens = _tokens(rest)
        names = _tokens(_norm(name)) + _tokens(_translit(_norm(name)))
        return bool(tokens) and all(token in names or _translit(token) in names for token in tokens)
    match = SIMILAR_PATTERN.fullmatch(normalized) or LIKE_PATTERN.fullmatch(normalized)
    if match is None:
        return False
    tokens = _tokens(match["rest"])
    names = _tokens(_norm(name)) + _tokens(_translit(_norm(name)))
    return bool(tokens) and all(_token_present(token, names) or _token_present(_translit(token), names) for token in tokens)
