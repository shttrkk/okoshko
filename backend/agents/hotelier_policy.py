"""Registry annotations for retaining substantive, partial hotelier observations.

The original claim and its quote stay unchanged. Normalisation only derives a
materialised value from an explicit field allowed by the characteristic schema.
"""
from __future__ import annotations

from copy import deepcopy


def _present(value) -> bool:
    if isinstance(value, dict):
        return any(_present(item) for item in value.values())
    if isinstance(value, list):
        return any(_present(item) for item in value)
    return value is not None and value != ""


def _policy(schema) -> dict:
    policy = schema.get("x-hotelier") if isinstance(schema, dict) else None
    return policy if isinstance(policy, dict) else {}


def substantive_value(value, schema: dict | None) -> bool:
    fields = _policy(schema).get("substantive_fields", [])
    return (isinstance(value, dict) and isinstance(fields, list) and all(isinstance(key, str) for key in fields)
            and any(_present(value.get(key)) for key in fields))


def normalize_value(value, schema: dict | None):
    rule = _policy(schema).get("availability_from", {})
    if (not isinstance(value, dict) or value.get("available") is not None
            or not isinstance(rule, dict) or type(rule.get("value")) is not bool
            or not isinstance(rule.get("field"), str)
            or not isinstance(rule.get("unless", []), list)
            or any(not isinstance(key, str) for key in rule.get("unless", []))
            or not isinstance(value.get(rule.get("field")), list) or not _present(value.get(rule.get("field")))
            or any(value.get(key) is True for key in rule.get("unless", []))):
        return value
    result = deepcopy(value)
    result["available"] = rule["value"]
    return result
