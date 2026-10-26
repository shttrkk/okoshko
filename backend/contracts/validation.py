"""Offline validation; no implicit HTTP or filesystem schema retrieval."""
from jsonschema import Draft202012Validator
from referencing import Registry

from backend.contracts.schema_safety import check_schema_safety


def validate_value(instance, schema):
    check_schema_safety(schema)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, registry=Registry()).validate(instance)
