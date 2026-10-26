from unittest.mock import patch

import pytest

from backend.contracts.schema_safety import check_schema_safety
from backend.contracts.validation import validate_value


@pytest.mark.parametrize("schema", [
    {"$ref": "http://127.0.0.1:1/private"},
    {"properties": {"x": {"$ref": "file:///etc/passwd"}}},
    {"$dynamicRef": "https://example.org/schema"},
    {"$id": "https://example.org/base", "type": "object"},
])
def test_external_schema_reference_never_retrieved(schema):
    with patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden")):
        with pytest.raises(ValueError):
            check_schema_safety(schema)
        with pytest.raises(ValueError):
            validate_value({}, schema)


def test_local_reference_still_validates():
    schema = {"$defs": {"flag": {"type": "boolean"}}, "$ref": "#/$defs/flag"}
    validate_value(True, schema)
