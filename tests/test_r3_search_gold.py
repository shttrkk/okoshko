"""Versioned R11 contract delta: old labels remain byte-for-byte unchanged."""
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'data/eval'
STROLLER_IDS = {'gold-084', 'gold-087', 'gold-197', 'panel-v5-006', 'panel-v5-007', 'panel-v5-008'}
ACCESSIBILITY = {'characteristic_id': 'accessible_route', 'field': 'available', 'op': 'eq', 'value': True,
                 'no_stairs': False, 'no_crossings': False, 'scope': None}


@pytest.mark.parametrize('name,sha256,changed', [
    ('queries_gold', '43eada069b04b811291bbede294160f00d8fb9b17b3a2271380791b6a3125dd6', STROLLER_IDS),
    ('group_gold', '8bd502380f19d621df142b75dca5551479e3ebc3f37c4e77e2a300633dce443e', {'panel-group-v5-002'}),
])
def test_r3_gold_changes_only_the_predeclared_mandatory_accessibility_contract(name, sha256, changed):
    original = ROOT / (name + '.jsonl')
    versioned = ROOT / (name + '_r3.jsonl')
    assert hashlib.sha256(original.read_bytes()).hexdigest() == sha256
    old, new = original.read_text().splitlines(), versioned.read_text().splitlines()
    assert len(old) == len(new)
    found = set()
    for old_raw, new_raw in zip(old, new, strict=True):
        before, after = json.loads(old_raw), json.loads(new_raw)
        if before['id'] not in changed:
            assert new_raw == old_raw
            continue
        found.add(before['id'])
        before['expected'].setdefault('must', []).append(ACCESSIBILITY)
        assert after == before
    assert found == changed
