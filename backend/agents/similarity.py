"""Offline weighted fingerprints: no embeddings, downloads, or invented facts."""
from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher

from backend.agents.query_match import fresh, scope_values, value_at
from backend.contracts import CharacteristicSpec, Criterion, Fact, Hotel, ParsedQuery, SearchHit

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
                   specs: list[CharacteristicSpec]) -> list[SearchHit]:
    """Return evidence-backed profile similarity for September, excluding self."""
    if not any(h.id == reference_id for h in hotels):
        return []
    by_hotel = defaultdict(list)
    for fact in facts:
        by_hotel[fact.hotel_id].append(fact)
    reference = _fingerprint(by_hotel[reference_id], specs)
    if not reference:
        return []
    criteria = [Criterion(characteristic_id=s.id, label=s.name_ru, field=s.search.field,
                          op="eq", value=reference[s.id][0], weight=WEIGHTS.get(s.id, 1)) for s in specs if s.id in reference]
    from backend.agents.ranker import rank_hotels
    hits = rank_hotels([h for h in hotels if h.id != reference_id], facts, ParsedQuery(text="Похожий профиль", nice=criteria), specs)
    fields = {s.id: s.search.field for s in specs}
    scored = []
    for hit in hits:
        fingerprint = _fingerprint(by_hotel[hit.hotel.id], specs)
        numerator, denominator, shared = 0, 0, 0
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
        hit.score = round(100*numerator/denominator, 2) if denominator else 0
        hit.group = "almost" if shared else "unknown"
        hit.summary = f"Сходство профиля {hit.score:g}/100: сопоставлены {shared} из {len(reference)} характеристик за сентябрь; учитывается уверенность. Это не гарантия выполнения одного набора условий в одном номере."
        scored.append(hit)
    return sorted(scored, key=lambda hit: (-hit.score, hit.hotel.id))


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
