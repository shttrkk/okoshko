"""Compiled local schemas must still validate every instance and every mutation."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from jsonschema import ValidationError

from backend.contracts.validation import _validator, validate_value


def test_each_instance_is_validated_after_a_cache_hit():
    schema = {'type': 'object', 'properties': {'n': {'type': 'integer', 'minimum': 1}}, 'required': ['n']}
    validate_value({'n': 2}, schema)
    with pytest.raises(ValidationError):
        validate_value({'n': 0}, schema)
    with pytest.raises(ValidationError):
        validate_value({}, schema)
    validate_value({'n': 3}, schema)


def test_mutating_a_nested_schema_never_reuses_old_rules():
    schema = {'type': 'object', 'properties': {'n': {'minimum': 1}}}
    validate_value({'n': 2}, schema)
    schema['properties']['n']['minimum'] = 3
    with pytest.raises(ValidationError):
        validate_value({'n': 2}, schema)


@pytest.mark.parametrize('ref', ['https://example.com/schema', 'file:///etc/passwd', '../schema.json'])
def test_mutating_cached_schema_cannot_enable_external_resolution(ref):
    schema = {'type': 'object', 'properties': {'value': {'type': 'string'}}}
    validate_value({'value': 'safe'}, schema)
    schema['properties']['value'] = {'$ref': ref}
    with pytest.raises(ValueError):
        validate_value({'value': 'unsafe'}, schema)


def test_local_references_and_parallel_calls_keep_per_instance_errors():
    schema = {'type': 'object', '$defs': {'n': {'type': 'integer', 'minimum': 1}},
              'properties': {'n': {'$ref': '#/$defs/n'}}, 'required': ['n']}
    def run(n):
        try:
            validate_value({'n': n}, schema)
            return True
        except ValidationError:
            return False
    values = [1, 0, 2, -1] * 40
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(run, values)) == [n >= 1 for n in values]


def test_equivalent_key_order_shares_compilation_without_caching_results():
    _validator.cache_clear()
    validate_value(2, {'type': 'integer', 'minimum': 1})
    with pytest.raises(ValidationError):
        validate_value(0, {'minimum': 1, 'type': 'integer'})
    assert _validator.cache_info().misses == 1 and _validator.cache_info().hits == 1
