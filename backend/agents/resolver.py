"""Scope-aware materialisation of claims; no LLM and no invented evidence."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import UTC, datetime

from backend.contracts import CharacteristicSpec, Claim, Conflict, Fact, Scope

INTERNAL = {"soundproofing", "room_orientation", "pets"}
VIEWS = {"sea_view", "sunset_view", "mountain_view"}
CATEGORIES = ("facade", "month", "room_type")
# A hotel-wide "yes" contradicts the model only within a known complete floor
# domain, with enough explicitly modeled facades and this confidence.
CONTRADICTION_CONFIDENCE = .6
CONTRADICTION_MIN_FACADES = 3
CONTRADICTION_PREFIX = "contradiction-"
VIEW_LABELS = {"sea_view": ("вид на море", "море"), "sunset_view": ("закат из окна", "закат"), "mountain_view": ("вид на горы", "горы")}


def stable_id(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:32]


def scope_key(scope: Scope) -> str:
    return json.dumps(scope.model_dump(exclude_none=True), sort_keys=True)


def value_key(claim: Claim, spec: CharacteristicSpec | None) -> str:
    if isinstance(claim.value, dict):
        if "available" in claim.value:
            return json.dumps(claim.value["available"])
        field = spec.search.field if spec else None
        if field in claim.value:
            value = claim.value[field]
            if isinstance(value, int | float):
                # A tolerance bin prevents trivial float noise being called a conflict.
                return json.dumps(round(float(value), 1))
        return json.dumps(claim.value, sort_keys=True, ensure_ascii=False)
    return json.dumps(claim.value, sort_keys=True, ensure_ascii=False)


def existential_view(claim: Claim) -> bool:
    """'Some rooms have a view' cannot vote on every room in the hotel."""
    positive = claim.value is True or isinstance(claim.value, dict) and claim.value.get("available") is True
    qualified = any(getattr(claim.scope, key) is not None for key in ("facade", "floor_min", "floor_max", "room_type"))
    universal = any(e.inputs.get("universal_scope") is True for e in claim.evidence)
    return claim.characteristic_id in VIEWS and positive and not qualified and not universal


def scopes_overlap(left: Scope, right: Scope) -> bool:
    if any(getattr(left, key) is not None and getattr(right, key) is not None
           and getattr(left, key) != getattr(right, key) for key in CATEGORIES):
        return False
    return max(left.floor_min or 1, right.floor_min or 1) <= min(left.floor_max or 100, right.floor_max or 100)


def scope_contains(outer: Scope, inner: Scope) -> bool:
    return all(getattr(outer, key) is None or getattr(outer, key) == getattr(inner, key) for key in CATEGORIES) and (
        (outer.floor_min or 1) <= (inner.floor_min or 1) and (outer.floor_max or 100) >= (inner.floor_max or 100))


def _partition_component(groups: list[list[Claim]]) -> list[tuple[Scope, list[Claim]]]:
    """Partition floors; categorical wildcards remain defaults with narrow overlays.

    Scope cannot express 'all room types except suite'. Keeping the default and
    its resolved suite overlay preserves that remainder without inventing rooms.
    Redundant overlays with the same supporting claims are removed below.
    """
    claims = [claim for group in groups for claim in group]
    patterns = {tuple(getattr(claim.scope, key) for key in CATEGORIES) for claim in claims}
    while True:
        added = set()
        for left in patterns:
            for right in patterns:
                if all(a is None or b is None or a == b for a, b in zip(left, right, strict=True)):
                    added.add(tuple(a if a is not None else b for a, b in zip(left, right, strict=True)))
        if added <= patterns:
            break
        patterns.update(added)
    cells = []
    for pattern in sorted(patterns, key=lambda row: tuple(str(value) for value in row)):
        covering = [claim for claim in claims if all(getattr(claim.scope, key) is None or getattr(claim.scope, key) == value
                                                    for key, value in zip(CATEGORIES, pattern, strict=True))]
        boundaries = sorted({bound for claim in covering for bound in (claim.scope.floor_min or 1, (claim.scope.floor_max or 100) + 1)})
        for low, stop in zip(boundaries, boundaries[1:], strict=False):
            active = [claim for claim in covering if (claim.scope.floor_min or 1) <= low and (claim.scope.floor_max or 100) >= stop - 1]
            if not active:
                continue
            fields = dict(zip(CATEGORIES, pattern, strict=True))
            fields["floor_min"] = low if low != 1 or any(claim.scope.floor_min is not None for claim in active) else None
            fields["floor_max"] = stop - 1 if stop != 101 or any(claim.scope.floor_max is not None for claim in active) else None
            fields["type"] = "room_type" if fields["room_type"] else "floor_range" if fields["facade"] and (
                fields["floor_min"] is not None or fields["floor_max"] is not None) else "facade" if fields["facade"] else "hotel"
            scope = Scope(**fields)
            original = next((claim.scope for claim in active if scope_contains(scope, claim.scope) and scope_contains(claim.scope, scope)), None)
            scope = original or scope
            cells.append((scope, active))
    result = []
    for index, (scope, active) in enumerate(cells):
        ids = {claim.id for claim in active}
        redundant = any(index != other_index and ids == {claim.id for claim in other_active}
                        and scope_contains(other_scope, scope)
                        and (not scope_contains(scope, other_scope) or other_index < index)
                        for other_index, (other_scope, other_active) in enumerate(cells))
        if not redundant:
            result.append((scope, active))
    return result


def partition_claims(claims: list[Claim], *, at: datetime) -> dict[tuple[str, str], list[Claim]]:
    """Resolve real intersections using narrowed copies, never ledger mutations."""
    grouped = defaultdict(list)
    candidates = defaultdict(lambda: defaultdict(list))
    for claim in claims:
        key = claim.characteristic_id, scope_key(claim.scope)
        grouped[key].append(claim)
        if claim.valid_until > at and not existential_view(claim):
            candidates[claim.characteristic_id][key].append(claim)
    for characteristic_id, by_scope in candidates.items():
        groups = list(by_scope.values())
        remaining = set(range(len(groups)))
        while remaining:
            component = {remaining.pop()}
            pending = list(component)
            while pending:
                current = pending.pop()
                neighbours = {index for index in remaining if scopes_overlap(groups[current][0].scope, groups[index][0].scope)}
                remaining.difference_update(neighbours)
                component.update(neighbours)
                pending.extend(neighbours)
            if len(component) == 1:
                continue  # Preserve exact scopes and Fact IDs when nothing overlaps.
            affected = [groups[index] for index in sorted(component)]
            for group in affected:
                key = characteristic_id, scope_key(group[0].scope)
                ids = {claim.id for claim in group}
                grouped[key] = [claim for claim in grouped[key] if claim.id not in ids]
            for scope, active in _partition_component(affected):
                grouped[(characteristic_id, scope_key(scope))].extend(claim.model_copy(update={"scope": scope}) for claim in active)
    return {key: group for key, group in grouped.items() if group}


def _number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _covers_floors(claims: list[Claim], known_floors: int) -> bool:
    """Require explicit continuous observations, not wildcard/room-type scopes."""
    intervals = sorted((claim.scope.floor_min, claim.scope.floor_max) for claim in claims
                       if claim.scope.floor_min is not None and claim.scope.floor_max is not None
                       and claim.scope.room_type is None)
    next_floor = 1
    for low, high in intervals:
        if low > next_floor:
            break
        next_floor = max(next_floor, high + 1)
    return next_floor > known_floors


def _view_months(claim: Claim, *, seasonal: bool, general: bool = False) -> set[int]:
    value = claim.value if isinstance(claim.value, dict) else {}
    domains = []
    if claim.scope.month is not None:
        domains.append({claim.scope.month})
    for key in ("month", "months"):
        if value.get(key) is None:
            continue
        months = value[key] if key == "months" else [value[key]]
        if not isinstance(months, list) or not months or any(type(month) is not int or not 1 <= month <= 12 for month in months):
            return set()
        domains.append(set(months))
    if domains:
        return set.intersection(*domains)
    # A seasonal model without month metadata cannot refute a year-round claim.
    return set(range(1, 13)) if general or not seasonal else set()


def view_contradictions(hotel_id: str, latest: list[Claim], at: datetime, *,
                        known_floors: int | None = None) -> list[tuple[str, list[Claim], list[Claim]]]:
    """Hotel-wide website/hotelier 'yes' against an all-'no' facade model.

    Returns (characteristic, general claims, computed claims). Once the hotel has
    answered with its own claims for the view, ordinary resolution applies.
    Coverage is checked on the explicitly modeled facade domain (not an assumed
    four-sided building); it is still a model/source disagreement, not proof
    about every possible window. Missing hotel floors never inherit a modeled
    first level as the building's full height.
    """
    if type(known_floors) is not int or not 1 <= known_floors <= 100:
        return []
    found = []
    for characteristic_id in sorted(VIEWS):
        related = [claim for claim in latest if claim.characteristic_id == characteristic_id and claim.valid_until > at]
        general = [claim for claim in related if existential_view(claim)
                   and (claim.source == "hotel_website" or claim.source.startswith("hotelier"))]
        if not general or any(claim.source.startswith("hotelier") and claim not in general for claim in related):
            continue
        computed = [claim for claim in related if claim.source == "geo_engine" and claim.scope.type in {"facade", "floor_range"}
                    and claim.scope.facade is not None]
        facades = {claim.scope.facade for claim in computed}
        if len(facades) < CONTRADICTION_MIN_FACADES:
            continue
        seasonal = characteristic_id == "sunset_view"
        months_by_claim = {claim.id: _view_months(claim, seasonal=seasonal) for claim in computed}
        if any(not months for months in months_by_claim.values()):
            continue
        contradicted, against = [], {}
        for broad in general:
            months = _view_months(broad, seasonal=seasonal, general=True)
            relevant = [claim for claim in computed if months_by_claim[claim.id] & months]
            if not months or not all(isinstance(claim.value, dict) and claim.value.get("available") is False
                                     and claim.confidence >= CONTRADICTION_CONFIDENCE for claim in relevant):
                continue
            if all(_covers_floors([claim for claim in relevant if claim.scope.facade == facade
                                   and month in months_by_claim[claim.id]], known_floors)
                   for facade in facades for month in months):
                contradicted.append(broad)
                against.update((claim.id, claim) for claim in relevant)
        if contradicted:
            found.append((characteristic_id, contradicted, list(against.values())))
    return found


def _quoted_metres(claim: Claim) -> float | None:
    quoted = claim.value.get("quoted_distance") if isinstance(claim.value, dict) else None
    if isinstance(quoted, dict) and quoted.get("relation") in {"stated", "less_than"} and isinstance(quoted.get("meters"), int | float):
        return float(quoted["meters"])
    return None


def route_contradictions(latest: list[Claim], at: datetime) -> list[tuple[str, list[Claim], list[Claim]]]:
    """A quoted distance to the beach far shorter than the computed walking route.

    Straight-line marketing distances are expected to be shorter than a real
    route; only a gap of more than 2x and more than 300 m is treated as a
    contradiction worth asking the hotel about.
    """
    related = [claim for claim in latest if claim.characteristic_id == "beach_walk" and claim.valid_until > at]
    if any(claim.source.startswith("hotelier") for claim in related):
        return []
    quoted = [claim for claim in related if claim.source == "hotel_website" and _quoted_metres(claim) is not None]
    routes = [claim for claim in related if claim.source == "geo_engine" and claim.confidence >= CONTRADICTION_CONFIDENCE
              and isinstance(claim.value, dict) and isinstance(claim.value.get("distance_m"), int | float)]
    found = []
    for claim in quoted:
        metres = _quoted_metres(claim)
        against = [route for route in routes if route.value["distance_m"] > max(2*metres, metres + 300)]
        if against and len(against) == len(routes):
            found.append(("beach_walk", [claim], against))
    return found


def contradiction_note(characteristic_id: str, general: list[Claim], computed: list[Claim]) -> str:
    if characteristic_id == "beach_walk":
        quotes = [evidence.quote for claim in general for evidence in claim.evidence if evidence.quote]
        route = max(computed, key=lambda claim: claim.confidence)
        minutes = route.value.get("minutes")
        walk = f"{round(route.value['distance_m'])} м" + (f", ≈ {math.ceil(minutes)} мин" if isinstance(minutes, int | float) else "")
        return (f"Противоречие источников. Сайт отеля: «{quotes[0] if quotes else 'расстояние до пляжа'}» "
                f"(уверенность {_number(max(c.confidence for c in general))}). Пешеходный маршрут по графу OSM с учётом рельефа: "
                f"{walk} (уверенность {_number(route.confidence)}). Расхождение больше чем вдвое: сайт мог указать расстояние по прямой, "
                "или в графе нет короткого прохода. Время пути не принято как подтверждённое; отельеру отправлен вопрос о реальном пешем пути.")
    label, target = VIEW_LABELS[characteristic_id]
    quotes = [evidence.quote for claim in general for evidence in claim.evidence if evidence.quote]
    who = "Сайт отеля" if all(claim.source == "hotel_website" for claim in general) else "Отельер"
    claimed = f"{who}: «{quotes[0]}»" if quotes else f"{who} заявляет {label}"
    floors = [(claim.scope.floor_min, claim.scope.floor_max) for claim in computed if claim.scope.floor_min]
    if floors:
        low, high = min(lo for lo, _ in floors), max(hi or lo for lo, hi in floors)
        span = f"на {low}-м этаже" if low == high else f"на этажах {low}–{high}"
    else:
        span = "на рассчитанных этажах"
    confidences = [claim.confidence for claim in computed]
    return (f"Противоречие источников. {claimed} (уверенность {_number(max(c.confidence for c in general))}). "
            f"Расчёт прямой видимости по рельефу и высотам зданий: {target} не видно ни с одного из "
            f"{len({claim.scope.facade for claim in computed})} фасадов {span} (уверенность {_number(min(confidences))}–{_number(max(confidences))}). "
            "Ни одна сторона не принята как подтверждённая: в поиске условие считается неподтверждённым, "
            "отельеру отправлен вопрос, какие номера и с какого этажа его видят.")


def resolve_claims(hotel_id: str, claims: list[Claim], specs: list[CharacteristicSpec], sources: list[dict], *, at: datetime | None = None,
                   known_floors: int | None = None) -> tuple[list[Fact], list[Conflict]]:
    at = at or datetime.now(UTC)
    source_priors = {source["id"]: source.get("alpha", 7)/(source.get("alpha", 7)+source.get("beta", 3)) for source in sources}
    spec_by_id = {spec.id: spec for spec in specs}
    superseded = {id for claim in claims for id in ([claim.supersedes] if claim.supersedes else [])+claim.supersedes_ids}
    latest = {}
    for claim in claims:
        if claim.hotel_id != hotel_id or claim.id in superseded:
            continue
        if claim.characteristic_id in spec_by_id and claim.spec_version!=spec_by_id[claim.characteristic_id].version:
            continue
        key = (claim.characteristic_id, scope_key(claim.scope), claim.source, claim.method)
        if key not in latest or (claim.observed_at, claim.id) > (latest[key].observed_at, latest[key].id):
            latest[key] = claim
    grouped = partition_claims(list(latest.values()), at=at)
    facts, conflicts = [], []
    for (characteristic_id, scope_json), group in grouped.items():
        valid = [claim for claim in group if claim.valid_until > at]
        scope = Scope.model_validate_json(scope_json)
        id = stable_id("fact", hotel_id, characteristic_id, scope_json)
        if not valid:
            freshest = max(group, key=lambda claim: claim.observed_at)
            facts.append(Fact(id=id, hotel_id=hotel_id, characteristic_id=characteristic_id, scope=scope,
                              value=freshest.value, status="stale", confidence=0, resolution="single",
                              supporting_claim_ids=[freshest.id], evidence=freshest.evidence,
                              valid_until=freshest.valid_until, synthetic=freshest.synthetic))
            continue
        spec = spec_by_id.get(characteristic_id)
        # An explicit null availability is a data gap, not a vote for false.
        valid = [claim for claim in valid if claim.value is not None and not (isinstance(claim.value, dict) and "available" in claim.value and claim.value["available"] is None)]
        if not valid:
            partial = max(group, key=lambda claim: (claim.observed_at, claim.confidence, claim.id))
            observed_fields = {"car_access": {"parking_distance_m", "ev_distance_m"},
                               "family_nearby": {"playground_distance_m", "hospital_distance_m"},
                               "night_pharmacy": {"distance_m"}}.get(characteristic_id, set())
            has_observation = isinstance(partial.value, dict) and any(
                isinstance(partial.value.get(key), int | float) and not isinstance(partial.value[key], bool)
                for key in observed_fields)
            facts.append(Fact(id=id, hotel_id=hotel_id, characteristic_id=characteristic_id, scope=scope,
                              value=partial.value, supporting_claim_ids=[partial.id], evidence=partial.evidence,
                              valid_until=partial.valid_until, synthetic=partial.synthetic,
                              status="computed" if has_observation else "unknown", confidence=partial.confidence if has_observation else 0))
            continue
        buckets = defaultdict(list)
        for claim in valid:
            buckets[value_key(claim, spec)].append(claim)
        def weight(claim):
            reliability = source_priors.get(claim.source, .7)
            # Local fake hotelier is never treated as independent real confirmation.
            if claim.synthetic:
                reliability = min(reliability, .6)
            return reliability*claim.confidence
        def bucket_score(values):
            # Repeated methods over a source do not manufacture independent votes.
            by_source = defaultdict(float)
            for claim in values:
                by_source[claim.source] = max(by_source[claim.source], weight(claim))
            return sum(by_source.values())
        ordered = sorted(buckets.values(), key=bucket_score, reverse=True)
        winner = ordered[0]
        opposing = [claim for bucket in ordered[1:] for claim in bucket]
        is_internal = characteristic_id in INTERNAL
        partner_claims = [claim for claim in valid if claim.source.startswith("hotelier") and not claim.synthetic]
        resolution = "weighted_vote" if len(valid)>1 else "single"
        if is_internal and partner_claims:
            best_partner = max(partner_claims, key=lambda claim: claim.observed_at)
            winner = buckets[value_key(best_partner, spec)]
            opposing = [claim for claim in valid if claim not in winner]
            resolution = "hotelier_override"
        best = max(winner, key=weight)
        winning_score = bucket_score(winner)
        total_score = sum(bucket_score(bucket) for bucket in ordered)
        margin = (winning_score - max([bucket_score(bucket) for bucket in ordered if bucket is not winner], default=0))/total_score if total_score else 0
        status = "computed"
        if any(claim.source.startswith("hotelier") and not claim.synthetic for claim in winner):
            status = "confirmed"
        if opposing:
            resolved = resolution == "hotelier_override" or margin > .3 and best.confidence >= .6
            status = status if resolved else "disputed"
            conflicts.append(Conflict(id=stable_id("conflict",hotel_id,characteristic_id,scope_json,sorted(c.id for c in valid)),
                hotel_id=hotel_id, characteristic_id=characteristic_id, claim_ids=[c.id for c in valid], kind="value",
                status="auto_resolved" if resolved else "open", synthetic=any(c.synthetic for c in valid),
                resolution_note="Внутреннее свойство подтверждено отельером с цитатой" if resolution == "hotelier_override" else
                    "Выбрано значение с большим весом источника и confidence" if resolved else "Источники расходятся; требуется уточнить номер и доказательства"))
        reliability = sum(claim.confidence*weight(claim) for claim in winner)/sum(weight(claim) for claim in winner) if sum(weight(claim) for claim in winner) else 0
        confidence = min(best.confidence, reliability)*(winning_score/total_score if total_score and opposing else 1)
        evidence = {evidence.id: evidence for claim in winner for evidence in claim.evidence}
        facts.append(Fact(id=id, hotel_id=hotel_id, characteristic_id=characteristic_id, scope=scope, value=best.value,
            confidence=confidence, status=status, resolution=resolution, supporting_claim_ids=[c.id for c in winner],
            conflicting_claim_ids=[c.id for c in opposing], evidence=list(evidence.values()),
            valid_until=min(c.valid_until for c in winner), synthetic=any(c.synthetic for c in winner)))
    represented = {fact.characteristic_id for fact in facts}
    for spec in specs:
        if spec.id not in represented:
            facts.append(Fact(id=stable_id("fact",hotel_id,spec.id,"hotel"), hotel_id=hotel_id, characteristic_id=spec.id))
    contradicted = set()
    for characteristic_id, general, computed in [*view_contradictions(hotel_id, list(latest.values()), at, known_floors=known_floors),
                                                 *route_contradictions(list(latest.values()), at)]:
        contradicted.add(characteristic_id)
        general_ids, computed_ids = {claim.id for claim in general}, {claim.id for claim in computed}
        for fact in facts:
            supporting = set(fact.supporting_claim_ids)
            if fact.characteristic_id == characteristic_id and supporting & (general_ids | computed_ids):
                fact.status = "disputed"
                opposite = computed_ids if supporting & general_ids else general_ids
                fact.conflicting_claim_ids = sorted(set(fact.conflicting_claim_ids) | opposite)
        ids = sorted(general_ids | computed_ids)
        # Stable per hotel and characteristic: re-collecting the same evidence keeps one conflict and one question.
        conflicts.append(Conflict(id=CONTRADICTION_PREFIX + stable_id("contradiction", hotel_id, characteristic_id),
                                  hotel_id=hotel_id, characteristic_id=characteristic_id, claim_ids=ids, kind="value", status="open",
                                  synthetic=any(claim.synthetic for claim in [*general, *computed]),
                                  resolution_note=contradiction_note(characteristic_id, general, computed)))
    # General statements and facade-specific observations coexist; they must not overwrite each other.
    for characteristic_id in represented - contradicted:
        general = [claim for claim in latest.values() if claim.characteristic_id == characteristic_id and existential_view(claim) and claim.valid_until > at]
        specific = [claim for claim in latest.values() if claim.characteristic_id == characteristic_id and claim.scope.type != "hotel" and claim.valid_until > at]
        for broad in general:
            for narrow in specific:
                if scopes_overlap(broad.scope, narrow.scope) and value_key(broad, spec_by_id.get(characteristic_id)) != value_key(narrow, spec_by_id.get(characteristic_id)):
                    # A split is a qualification; only an explicit universal assertion is a genuine contradiction.
                    universal = any(e.inputs.get("universal_scope") for e in broad.evidence)
                    conflicts.append(Conflict(id=stable_id("scope",broad.id,narrow.id),hotel_id=hotel_id,characteristic_id=characteristic_id,
                        claim_ids=[broad.id,narrow.id],kind="scope",status="asked" if universal else "auto_resolved",
                        synthetic=broad.synthetic or narrow.synthetic,
                        resolution_note="Утверждение обо всех номерах расходится с расчётом конкретного фасада; уточнить номер" if universal else
                        "Общее описание уточнено по фасаду и этажу; наличие вида в части номеров не означает вид во всех номерах"))
                    for fact in facts:
                        if fact.characteristic_id == characteristic_id:
                            fact.resolution = "scope_split"
    return facts, conflicts


def reconcile_conflicts(repo, conflicts: list[Conflict], hotel_ids: set[str]) -> None:
    """Close obsolete materialized disputes while preserving their claim history."""
    active_ids = {conflict.id for conflict in conflicts}
    previous = repo.find("conflicts", hotel_id=next(iter(hotel_ids))) if len(hotel_ids) == 1 else repo.all("conflicts")
    previous_by_id = {c["id"]: c for c in previous if c.get("hotel_id") in hotel_ids}
    payloads = []
    for previous_conflict in previous:
        if (previous_conflict.get("hotel_id") in hotel_ids and previous_conflict.get("status") in {"open", "asked"}
                and previous_conflict["id"] not in active_ids):
            closed = Conflict.model_validate(previous_conflict).model_copy(update={
                "status": "resolved",
                "resolution_note": "Спор закрыт при пересчёте: прежние наблюдения заменены, истекли или их пересечение больше не активно. История и ссылки на claims сохранены.",
            })
            payloads.append(closed.model_dump(mode="json"))
    for conflict in conflicts:
        old = previous_by_id.get(conflict.id)
        if old:
            updates = {"question_id": conflict.question_id or old.get("question_id")}
            if old.get("status") == "asked" and conflict.status == "open":
                updates["status"] = "asked"
            conflict = conflict.model_copy(update=updates)
        payloads.append(conflict.model_dump(mode="json"))
    if hasattr(repo, "upsert_many"):
        repo.upsert_many("conflicts", payloads)
    else:
        for payload in payloads:
            repo.upsert("conflicts", payload)


def resolve_hotel(hotel_id: str, repo) -> list[Fact]:
    claims = [Claim.model_validate(value) for value in repo.find("claims", hotel_id=hotel_id)]
    specs = [CharacteristicSpec.model_validate(value) for value in repo.all("characteristics")]
    hotel = repo.get("hotels", hotel_id) or {}
    facts, conflicts = resolve_claims(hotel_id, claims, specs, repo.all("sources"), known_floors=hotel.get("floors"))
    if hasattr(repo,"replace_facts"):
        repo.replace_facts(hotel_id,[fact.model_dump(mode="json") for fact in facts])
    else:
        payloads = [fact.model_dump(mode="json") for fact in facts]
        if hasattr(repo, "upsert_many"):
            repo.upsert_many("facts", payloads)
        else:
            for payload in payloads:
                repo.upsert("facts", payload)
    from backend.agents.interviewer import ask_contradictions
    reconcile_conflicts(repo, ask_contradictions(repo, conflicts), {hotel_id})
    return facts


def resolve_all(repo):
    claims_by_hotel = defaultdict(list)
    for value in repo.all("claims"):
        claim = Claim.model_validate(value)
        claims_by_hotel[claim.hotel_id].append(claim)
    specs = [CharacteristicSpec.model_validate(value) for value in repo.all("characteristics")]
    sources = repo.all("sources")
    facts, conflicts = [], []
    hotels = repo.all("hotels")
    for hotel in hotels:
        new_facts, new_conflicts = resolve_claims(hotel["id"], claims_by_hotel[hotel["id"]], specs, sources, known_floors=hotel.get("floors"))
        facts.extend(fact.model_dump(mode="json") for fact in new_facts)
        conflicts.extend(conflict.model_dump(mode="json") for conflict in new_conflicts)
    if hasattr(repo, "replace_materialized_facts"):
        repo.replace_materialized_facts([hotel["id"] for hotel in hotels], facts)
    else:
        repo.upsert_many("facts", facts)
    from backend.agents.interviewer import ask_contradictions
    typed = ask_contradictions(repo, [Conflict.model_validate(conflict) for conflict in conflicts])
    reconcile_conflicts(repo, typed, {hotel["id"] for hotel in hotels})
    return {"facts": len(facts), "conflicts": len(conflicts),
            "contradictions": sum(conflict.id.startswith(CONTRADICTION_PREFIX) for conflict in typed)}

