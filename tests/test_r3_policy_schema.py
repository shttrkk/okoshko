"""Declarative partial-answer policies have no executable or unbounded fields."""
import copy

import pytest
import yaml
from pydantic import ValidationError

from backend.contracts import CharacteristicSpec
from backend.settings import DATA


@pytest.mark.parametrize('policy', [None, [], 'species', {'arbitrary': True},
    {'substantive_fields': 'species'}, {'substantive_fields': [['species']]},
    {'availability_from': {'field': ['species'], 'value': True}},
    {'availability_from': {'field': 'species', 'value': 1}},
    {'availability_from': {'field': 'species', 'value': True, 'unless': 'flag'}},
    {'availability_from': {'field': 'species', 'value': True, 'expr': 'unsafe'}},
    {'substantive_fields': ['x'*65]}])
def test_reject_malformed_hotelier_annotations(policy):
    spec = copy.deepcopy(yaml.safe_load((DATA/'characteristics/pets.yaml').read_text()))
    spec['value_schema']['x-hotelier'] = policy
    with pytest.raises(ValidationError):
        CharacteristicSpec.model_validate(spec)


def test_explicit_non_executable_hotelier_policy():
    spec = yaml.safe_load((DATA/'characteristics/pets.yaml').read_text())
    CharacteristicSpec.model_validate(spec)
