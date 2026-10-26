"""Intersect observation scopes before evaluating composite characteristics."""
from itertools import product

from backend.agents.query_match import fresh, scope_values
from backend.contracts import Fact, Scope


def compatible_inputs(rows, names, month):
    groups = {name: [] for name in names}
    for row in rows:
        if row["characteristic_id"] not in groups:
            continue
        fact = Fact.model_validate(row)
        if fresh(fact) and scope_values(fact).get("month") in {None, month}:
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
        # A general website claim that a view exists somewhere does not prove
        # the same view on a second observation's specific facade or floor.
        if any(key in merged for key in ("facade", "room_type", "floor_min", "floor_max")) and any(
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
