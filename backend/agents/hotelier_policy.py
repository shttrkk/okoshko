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


def substantive_value(value, schema: dict | None) -> bool:
    policy = (schema or {}).get("x-hotelier", {})
    return isinstance(value, dict) and any(_present(value.get(key)) for key in policy.get("substantive_fields", []))


def normalize_value(value, schema: dict | None):
    policy = (schema or {}).get("x-hotelier", {})
    rule = policy.get("availability_from", {})
    if (not isinstance(value, dict) or value.get("available") is not None
            or not isinstance(rule, dict) or type(rule.get("value")) is not bool
            or not isinstance(value.get(rule.get("field")), list) or not _present(value.get(rule.get("field")))
            or any(_present(value.get(key)) for key in rule.get("unless", []))):
        return value
    result = deepcopy(value)
    result["available"] = rule["value"]
    return result
