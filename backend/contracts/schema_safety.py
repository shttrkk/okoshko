"""Pure schema boundary: specifications may reference only their own document."""

import re


def _hotelier_policy(schema):
    if not isinstance(schema, dict) or "x-hotelier" not in schema:
        return
    policy = schema["x-hotelier"]
    if not isinstance(policy, dict) or set(policy) - {"substantive_fields", "availability_from"}:
        raise ValueError("Invalid x-hotelier policy")

    def field_name(value):
        return isinstance(value, str) and re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}", value)

    def fields(value):
        return isinstance(value, list) and len(value) <= 40 and all(field_name(item) for item in value)

    if "substantive_fields" in policy and not fields(policy["substantive_fields"]):
        raise ValueError("x-hotelier substantive_fields must be bounded field names")
    if "availability_from" in policy:
        rule = policy["availability_from"]
        if (not isinstance(rule, dict) or set(rule) - {"field", "value", "unless"}
                or not field_name(rule.get("field")) or type(rule.get("value")) is not bool
                or not fields(rule.get("unless", []))):
            raise ValueError("x-hotelier availability_from must be an explicit Boolean field rule")


def check_schema_safety(schema):
    _hotelier_policy(schema)
    count = 0
    active = set()

    def visit(value, depth=0):
        nonlocal count
        count += 1
        if depth > 24 or count > 4000:
            raise ValueError("JSON Schema exceeds the supported size")
        if not isinstance(value, dict | list):
            return
        if id(value) in active:
            raise ValueError("Cyclic JSON Schema object")
        active.add(id(value))
        if isinstance(value, dict):
            if "$id" in value:
                raise ValueError("JSON Schema $id is forbidden")
            for key in ("$ref", "$dynamicRef"):
                if key in value and (not isinstance(value[key], str) or not value[key].startswith("#/")):
                    raise ValueError("Only local JSON Schema references are allowed")
            children = value.values()
        else:
            children = value
        for child in children:
            visit(child, depth + 1)
        active.remove(id(value))

    visit(schema)
