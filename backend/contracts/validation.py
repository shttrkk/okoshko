"""Offline validation; no implicit HTTP or filesystem schema retrieval."""
import json
from functools import lru_cache

from jsonschema import Draft202012Validator
from referencing import Registry

from backend.contracts.schema_safety import check_schema_safety


@lru_cache(maxsize=128)
def _validator(encoded_schema: str):
    # The canonical JSON is an immutable cache key: a caller mutating its
    # schema cannot reuse a validator built for a different definition.
    schema = json.loads(encoded_schema)
    check_schema_safety(schema)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=Registry())


def validate_value(instance, schema):
    encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    _validator(encoded).validate(instance)
