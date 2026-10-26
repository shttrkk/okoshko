"""Scope-aware materialisation of claims; no LLM and no invented evidence."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime

from backend.contracts import CharacteristicSpec, Claim, Conflict, Fact, Scope

INTERNAL = {"soundproofing", "room_orientation", "pets"}


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


def resolve_claims(hotel_id: str, claims: list[Claim], specs: list[CharacteristicSpec], sources: list[dict], *, at: datetime | None = None) -> tuple[list[Fact], list[Conflict]]:
    at = at or datetime.now(UTC)
    source_priors = {source["id"]: source.get("alpha", 7)/(source.get("alpha", 7)+source.get("beta", 3)) for source in sources}
    spec_by_id = {spec.id: spec for spec in specs}
    superseded = {claim.supersedes for claim in claims if claim.supersedes}
    latest = {}
    for claim in claims:
        if claim.hotel_id != hotel_id or claim.id in superseded:
            continue
        key = (claim.characteristic_id, scope_key(claim.scope), claim.source, claim.method)
        if key not in latest or (claim.observed_at, claim.id) > (latest[key].observed_at, latest[key].id):
            latest[key] = claim
    grouped = defaultdict(list)
    for claim in latest.values():
        grouped[(claim.characteristic_id, scope_key(claim.scope))].append(claim)
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
            partial = max(group, key=lambda claim: claim.observed_at)
            facts.append(Fact(id=id, hotel_id=hotel_id, characteristic_id=characteristic_id, scope=scope,
                              value=partial.value, supporting_claim_ids=[partial.id], evidence=partial.evidence,
                              valid_until=partial.valid_until, synthetic=partial.synthetic))
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
    # General statements and facade-specific observations coexist; they must not overwrite each other.
    for characteristic_id in represented:
        general = [claim for claim in latest.values() if claim.characteristic_id == characteristic_id and claim.scope.type == "hotel" and claim.valid_until > at]
        specific = [claim for claim in latest.values() if claim.characteristic_id == characteristic_id and claim.scope.type != "hotel" and claim.valid_until > at]
        for broad in general:
            for narrow in specific:
                if value_key(broad, spec_by_id.get(characteristic_id)) != value_key(narrow, spec_by_id.get(characteristic_id)):
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


def resolve_hotel(hotel_id: str, repo) -> list[Fact]:
    claims = [Claim.model_validate(value) for value in repo.find("claims", hotel_id=hotel_id)]
    specs = [CharacteristicSpec.model_validate(value) for value in repo.all("characteristics")]
    facts, conflicts = resolve_claims(hotel_id, claims, specs, repo.all("sources"))
    for table, values in (("facts", facts), ("conflicts", conflicts)):
        payloads = [value.model_dump(mode="json") for value in values]
        if hasattr(repo, "upsert_many"):
            repo.upsert_many(table, payloads)
        else:
            for payload in payloads:
                repo.upsert(table, payload)
    return facts


def resolve_all(repo):
    claims_by_hotel = defaultdict(list)
    for value in repo.all("claims"):
        claim = Claim.model_validate(value)
        claims_by_hotel[claim.hotel_id].append(claim)
    specs = [CharacteristicSpec.model_validate(value) for value in repo.all("characteristics")]
    sources = repo.all("sources")
    facts, conflicts = [], []
    for hotel in repo.all("hotels"):
        new_facts, new_conflicts = resolve_claims(hotel["id"], claims_by_hotel[hotel["id"]], specs, sources)
        facts.extend(fact.model_dump(mode="json") for fact in new_facts)
        conflicts.extend(conflict.model_dump(mode="json") for conflict in new_conflicts)
    repo.upsert_many("facts", facts)
    repo.upsert_many("conflicts", conflicts)
    return {"facts": len(facts), "conflicts": len(conflicts)}

