"""Intersect observation scopes before evaluating composite characteristics."""
from itertools import product

from backend.agents.query_match import _not_expired, _scope_contains, fresh, scope_values
from backend.contracts import Fact, Scope


def compatible_inputs(rows, names, month):
    if not names:
        return []
    groups = {name: [] for name in names}
    observations = {name: [] for name in names}
    for row in rows:
        if row["characteristic_id"] not in groups:
            continue
        fact = Fact.model_validate(row)
        scope = scope_values(fact)
        if scope.get("month") not in {None, month}:
            continue
        if fact.status != "stale" and _not_expired(fact):
            observations[fact.characteristic_id].append((fact, scope))
        if fresh(fact):
            groups[fact.characteristic_id].append(fact)
    count = 1
    for choices in groups.values():
        count *= len(choices)
    if count == 0 or count > 4096:
        return []
    results = {}
    for facts in product(*(groups[name] for name in names)):
        scopes = [scope_values(fact) for fact in facts]
        merged = {}
        valid = True
        for key in ("facade", "room_type", "month"):
            values = {scope[key] for scope in scopes if scope.get(key) is not None}
            if len(values) > 1:
                valid = False
                break
            if values:
                merged[key] = values.pop()
        if not valid:
            continue
        lows = [scope["floor_min"] for scope in scopes if scope.get("floor_min")]
        highs = [scope["floor_max"] for scope in scopes if scope.get("floor_max")]
        if lows:
            merged["floor_min"] = max(lows)
        if highs:
            merged["floor_max"] = min(highs)
        if merged.get("floor_min", 1) > merged.get("floor_max", 100):
            continue
        # A default is valid only outside its more specific overlays. Scope has
        # no 'except suite' representation, so abstain rather than emit a broad
        # claim that would swallow a narrow dispute or a resolved restriction.
        domain = {**merged, "month": month}
        overridden = False
        for selected, selected_scope in zip(facts, scopes, strict=True):
            for other, other_scope in observations[selected.characteristic_id]:
                if other.id == selected.id:
                    continue
                broader = _scope_contains(other_scope, selected_scope) and not _scope_contains(selected_scope, other_scope)
                if broader:
                    continue
                same_region = all(domain.get(key) is None or other_scope.get(key) is None or domain[key] == other_scope[key]
                                  for key in ("facade", "room_type", "month"))
                floors_intersect = max(domain.get("floor_min") or 1, other_scope.get("floor_min") or 1) <= min(
                    domain.get("floor_max") or 100, other_scope.get("floor_max") or 100)
                if same_region and floors_intersect:
                    overridden = True
                    break
            if overridden:
                break
        if overridden:
            continue
        # A general website claim that a view exists somewhere does not prove
        # the same view on a second observation's specific facade or floor.
        if any(
            fact.characteristic_id in {"sea_view", "sunset_view", "mountain_view"}
            and isinstance(fact.value, dict) and fact.value.get("available") is True
            and not any(key in scope for key in ("facade", "room_type", "floor_min", "floor_max"))
            for fact, scope in zip(facts, scopes, strict=True)
        ):
            continue
        merged["type"] = "room_type" if merged.get("room_type") else "floor_range" if merged.get("facade") and (lows or highs) else "facade" if merged.get("facade") else "hotel"
        scope = Scope.model_validate(merged)
        key = scope.model_dump_json()
        if key not in results or min(f.confidence for f in facts) > min(f.confidence for f in results[key][1]):
            results[key] = (scope, facts)
    return list(results.values())
