"""Versioned, append-only retirement of observations whose actual inputs changed."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from backend.contracts import CharacteristicSpec, Claim, Evidence, Fact

NAMESPACE = "okno.geometry_dependencies"
VERSION = 1
GEO_CHARACTERISTICS = {"sea_view", "sunset_view", "mountain_view", "quiet_night", "beach_walk"}
GEOMETRY_DEPENDENTS = {"morning_sun", "evening_heat", "railway_noise"}


def metadata(**values) -> dict:
    return {"version": VERSION, **values}


def fact_fingerprint(fact: Fact) -> str:
    # An artifact link is not a new route measurement. Dates of materialization
    # likewise do not version the actual input to an expression.
    value = fact.value
    if fact.characteristic_id == "beach_walk" and isinstance(value, dict):
        value = {key: item for key, item in value.items() if key != "route_ref"}
    state = [fact.characteristic_id, fact.scope.model_dump(exclude_none=True), value,
             fact.status, fact.confidence]
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def retirement_sources(hotel_id: str, claims: list[Claim]) -> dict[str, Claim]:
    """Only canonical local calculations can retire their own hotel's geo claims.

    Read superseded anchors too: a later change must not resurrect earlier
    observations. The ledger itself, rather than mutable facts, carries history.
    """
    local = {claim.id: claim for claim in claims if claim.hotel_id == hotel_id and claim.source == "geo_engine"}
    retired = {}
    for anchor in local.values():
        if anchor.characteristic_id not in GEO_CHARACTERISTICS:
            continue
        for evidence in anchor.evidence:
            artifact = evidence.artifact or ""
            record = evidence.inputs.get(NAMESPACE)
            if (evidence.kind not in {"calculation", "simulation"}
                    or not artifact.startswith(("/data/geo/", "/data/runtime/geo/"))
                    or not isinstance(record, dict) or record.get("version") != VERSION
                    or record.get("hotel_id") != hotel_id or record.get("reason") != "geometry_changed"):
                continue
            ids = record.get("invalidated_claim_ids")
            if not isinstance(ids, list):
                continue
            for identity in ids:
                if not isinstance(identity, str) or identity not in local or identity == anchor.id:
                    continue
                previous = retired.get(identity)
                if previous is None or (anchor.observed_at, anchor.id) > (previous.observed_at, previous.id):
                    retired[identity] = anchor
    return retired


def input_characteristics(claim: Claim, spec: CharacteristicSpec | None) -> set[str]:
    """New explicit provenance plus the shipped legacy declarative methods."""
    names = set()
    for evidence in claim.evidence:
        record = evidence.inputs.get(NAMESPACE, {})
        if isinstance(record, dict) and record.get("version") == VERSION:
            names.update(name for name in record.get("input_characteristic_ids", []) if isinstance(name, str))
        if claim.method == "safe_expression@1":
            names.update(evidence.inputs.get("values", {}))
    for call in spec.methods if spec else []:
        canonical = {"sun_window": "sunset_view", "noise_facade": "quiet_night", "walk_route": "beach_walk",
                     "climate": "climate", "air_quality": "air_quality", "connectivity": "connectivity"}.get(call.tool)
        if call.tool == "viewshed_facade":
            canonical = "mountain_view" if call.params.get("target") == "mountains" else "sea_view"
        if canonical:
            names.add(canonical)
        if call.tool == "derive":
            try:
                names.update(node.id for node in ast.walk(ast.parse(call.params.get("expr", ""), mode="eval"))
                             if isinstance(node, ast.Name))
            except (SyntaxError, ValueError):
                pass
    return names - {claim.characteristic_id}


def changed_dependencies(claims: list[Claim], specs: list[CharacteristicSpec], before: list[Fact], after: list[Fact],
                         artifact: str) -> set[str]:
    from backend.agents.resolver import scope_key, scopes_overlap

    superseded = {identity for claim in claims for identity in [claim.supersedes, *claim.supersedes_ids] if identity}
    retired = retirement_sources(claims[0].hotel_id, claims) if claims else {}
    spec_by_id = {spec.id: spec for spec in specs}
    latest = {}
    for claim in claims:
        if claim.source != "geo_engine" or claim.id in superseded or claim.id in retired:
            continue
        if claim.characteristic_id in spec_by_id and claim.spec_version != spec_by_id[claim.characteristic_id].version:
            continue
        key = (claim.characteristic_id, claim.source, claim.method, scope_key(claim.scope))
        if key not in latest or (claim.observed_at, claim.id) > (latest[key].observed_at, latest[key].id):
            latest[key] = claim
    changed = set()
    for claim in latest.values():
        if claim.characteristic_id in GEO_CHARACTERISTICS | GEOMETRY_DEPENDENTS:
            continue  # The canonical replacement/tombstone path owns these.
        spec = spec_by_id.get(claim.characteristic_id)
        geometry_sensitive = any(call.tool == "geo_trait" and call.params.get("kind") in GEOMETRY_DEPENDENTS
                                 for call in spec.methods) if spec else False
        geometry_sensitive = geometry_sensitive or any(
            isinstance(e.inputs.get(NAMESPACE), dict) and e.inputs[NAMESPACE].get("version") == VERSION
            and e.inputs[NAMESPACE].get("geometry_sensitive") is True for e in claim.evidence)
        if geometry_sensitive and not any(Path(e.artifact or "").name == Path(artifact).name for e in claim.evidence):
            changed.add(claim.id)
            continue
        for cid in input_characteristics(claim, spec):
            old = sorted(fact_fingerprint(fact) for fact in before
                         if fact.characteristic_id == cid and scopes_overlap(claim.scope, fact.scope))
            new = sorted(fact_fingerprint(fact) for fact in after
                         if fact.characteristic_id == cid and scopes_overlap(claim.scope, fact.scope))
            if old != new:
                changed.add(claim.id)
                break
    return changed


def unknown_dependencies(hotel_id: str, claims: list[Claim], facts: list[Fact], retired: dict[str, Claim]) -> list[Fact]:
    from backend.agents.resolver import scope_contains, scope_key, stable_id

    groups = {}
    for claim in claims:
        if claim.id in retired:
            groups.setdefault((claim.characteristic_id, scope_key(claim.scope)), []).append(claim)
    for (cid, scope_json), previous in groups.items():
        scope = previous[0].scope
        # Independent evidence or a genuinely re-run tool may replace the old
        # scope. A narrower replacement leaves the uncalculated remainder unknown.
        if any(fact.characteristic_id == cid and fact.supporting_claim_ids
               and scope_contains(fact.scope, scope) for fact in facts):
            continue
        facts = [fact for fact in facts if fact.characteristic_id != cid or fact.supporting_claim_ids or fact.evidence]
        anchor = max((retired[claim.id] for claim in previous), key=lambda claim: (claim.observed_at, claim.id))
        original = next(e for e in anchor.evidence if NAMESPACE in e.inputs)
        ids = sorted(claim.id for claim in previous)
        evidence = Evidence(id="ev-dependency-" + stable_id(anchor.id, ids), kind=original.kind,
                            label="Входы зависимого расчёта изменились; нужен новый расчёт",
                            source_ids=original.source_ids, artifact=original.artifact,
                            inputs={NAMESPACE: metadata(hotel_id=hotel_id, reason="geometry_changed",
                                                       invalidated_claim_ids=ids, anchor_claim_id=anchor.id)},
                            limitations=["Прежнее значение сохранено в истории claims и не относится к текущим входам",
                                         "Неизвестное значение не является отрицательным результатом расчёта"])
        facts.append(Fact(id=stable_id("fact", hotel_id, cid, scope_json), hotel_id=hotel_id, characteristic_id=cid,
                          scope=scope, status="unknown", confidence=0, value=None, evidence=[evidence],
                          supporting_claim_ids=[anchor.id], updated_at=anchor.observed_at,
                          synthetic=anchor.synthetic or any(claim.synthetic for claim in previous)))
    return facts
