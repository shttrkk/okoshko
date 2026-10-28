"""Metadata compatibility is anchored to old bytes and unchanged materialization."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from backend.contracts import Hotel
from scripts import ranking_eval

REPO = ranking_eval.ROOT
NAME = 'data/characteristics/quiet_night.yaml'
GOLD_ROW = json.loads(ranking_eval.GOLD.read_text().splitlines()[0])
ORIGINAL_HASH = GOLD_ROW['input_files_sha256'][NAME]
SNAPSHOT = Path(ranking_eval.SCHEMA_SNAPSHOTS) / (ORIGINAL_HASH + '.yaml')
ORIGINAL = (REPO / SNAPSHOT).read_bytes()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_case(tmp_path, monkeypatch, document=None):
    monkeypatch.setattr(ranking_eval, 'ROOT', tmp_path)
    original = yaml.safe_load(ORIGINAL)
    edited = copy.deepcopy(original) if document is None else document
    edited['display_name'] = 'Тишина для гостя'
    edited['synonyms'] = [*edited['synonyms'], 'спокойный район']
    live = tmp_path / NAME
    live.parent.mkdir(parents=True)
    live.write_text(yaml.safe_dump(edited, allow_unicode=True, sort_keys=False))
    saved = tmp_path / SNAPSHOT
    saved.parent.mkdir(parents=True)
    saved.write_bytes(ORIGINAL)
    return {NAME: ORIGINAL_HASH}, {NAME: sha(live.read_bytes())}, live, saved


def test_actual_quiet_changes_have_original_hash_evidence_and_only_allowed_metadata():
    assert sha(ORIGINAL) == ORIGINAL_HASH
    assert SNAPSHOT.name == ORIGINAL_HASH + '.yaml'
    live = (REPO / NAME).read_bytes()
    current = yaml.safe_load(live)
    assert {'спокойный номер', 'спокойный район', 'тихий район'} <= set(current['synonyms'])
    changes = ranking_eval.input_metadata_changes({NAME: ORIGINAL_HASH}, {NAME: sha(live)})
    assert changes is not None and len(changes) == 1
    [change] = changes
    assert change['original_sha256'] == ORIGINAL_HASH
    assert change['verification'] == 'sha256_verified_original_snapshot'
    assert change['original_snapshot'] == str(SNAPSHOT)
    assert 'synonyms' in change['changed_metadata_paths']
    assert set(change['changed_metadata_paths']) <= set(ranking_eval.ALLOWED_METADATA)


def test_exact_three_metadata_paths_can_vary_without_mutating_the_snapshot(tmp_path, monkeypatch):
    document = yaml.safe_load(ORIGINAL)
    document['value_schema']['x-hotelier'] = {'fields': ['available'], 'answer_type': 'reported_quiet'}
    expected, current, _, saved = write_case(tmp_path, monkeypatch, document)
    [change] = ranking_eval.input_metadata_changes(expected, current)
    assert change['changed_metadata_paths'] == list(ranking_eval.ALLOWED_METADATA)
    assert saved.read_bytes() == ORIGINAL


@pytest.mark.parametrize('section,key,value', [
    ('search', 'field', 'night_db_max'),
    ('search', 'default_op', 'lte'),
    ('search', 'default_value', False),
    ('root', 'scope', 'hotel'),
    ('root', 'ttl_days', 1),
    ('root', 'hotelier_answerable', False),
    ('root', 'methods', [{'tool': 'noise_facade', 'params': {'threshold': 10}, 'rationale': 'changed'}]),
    ('value_schema', 'additionalProperties', False),
    ('value_schema', 'required', ['available']),
    ('value_schema', 'properties', {'available': {'type': 'boolean'}}),
])
def test_search_methods_scope_and_schema_constraints_cannot_hide_in_metadata(tmp_path, monkeypatch, section, key, value):
    document = yaml.safe_load(ORIGINAL)
    (document if section == 'root' else document[section])[key] = value
    expected, current, _, _ = write_case(tmp_path, monkeypatch, document)
    assert not ranking_eval.compatible_input_files(expected, current)


@pytest.mark.parametrize('section', ['root', 'value_schema', 'nested_property'])
def test_unknown_annotations_and_nested_x_hotelier_are_not_ignored(tmp_path, monkeypatch, section):
    document = yaml.safe_load(ORIGINAL)
    if section == 'nested_property':
        document['value_schema']['properties']['available']['x-hotelier'] = {'arbitrary': True}
    else:
        (document if section == 'root' else document['value_schema'])['x-other'] = {'arbitrary': True}
    expected, current, _, _ = write_case(tmp_path, monkeypatch, document)
    assert not ranking_eval.compatible_input_files(expected, current)


def test_source_ledgers_stay_byte_frozen_even_with_a_valid_metadata_snapshot(tmp_path, monkeypatch):
    expected, current, _, _ = write_case(tmp_path, monkeypatch)
    name = ranking_eval.LEDGERS[0]
    source = tmp_path / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'changed source bytes')
    expected[name], current[name] = sha(b'original source bytes'), sha(source.read_bytes())
    assert not ranking_eval.compatible_input_files(expected, current)


def test_reported_current_hash_cannot_cover_different_source_bytes(tmp_path, monkeypatch):
    expected, current, _, _ = write_case(tmp_path, monkeypatch)
    name = ranking_eval.LEDGERS[0]
    source = tmp_path / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'changed source bytes')
    expected[name] = current[name] = sha(b'original source bytes')
    assert not ranking_eval.compatible_input_files(expected, current)


@pytest.mark.parametrize('tamper', ['newline', 'semantic_substitution', 'missing', 'wrong_filename', 'external_symlink'])
def test_snapshot_bytes_location_and_full_hash_are_verified(tmp_path, monkeypatch, tamper):
    expected, current, _, saved = write_case(tmp_path, monkeypatch)
    if tamper == 'newline':
        saved.write_bytes(ORIGINAL + b'\n')
    elif tamper == 'semantic_substitution':
        saved.write_bytes(ORIGINAL.replace(b'default_value: true', b'default_value: false'))
    elif tamper == 'wrong_filename':
        saved.rename(saved.with_name('0' * 64 + '.yaml'))
    elif tamper == 'external_symlink':
        outside = tmp_path.parent / (tmp_path.name + '-outside.yaml')
        outside.write_bytes(ORIGINAL)
        saved.unlink()
        saved.symlink_to(outside)
    else:
        saved.unlink()
    assert not ranking_eval.compatible_input_files(expected, current)


@pytest.mark.parametrize('path', ['data/characteristics/../../quiet_night.yaml',
                                  'data/characteristics/../quiet_night.yaml', '/tmp/quiet_night.yaml'])
def test_input_path_traversal_is_rejected(tmp_path, monkeypatch, path):
    monkeypatch.setattr(ranking_eval, 'ROOT', tmp_path)
    assert not ranking_eval.compatible_input_files({path: ORIGINAL_HASH}, {path: ORIGINAL_HASH})


def test_snapshot_filename_cannot_be_supplied_through_a_hash(tmp_path, monkeypatch):
    expected, current, _, _ = write_case(tmp_path, monkeypatch)
    expected[NAME] = '../../outside'
    assert not ranking_eval.compatible_input_files(expected, current)


def test_duplicate_metadata_keys_are_not_silently_discarded(tmp_path, monkeypatch):
    expected, current, live, _ = write_case(tmp_path, monkeypatch)
    live.write_bytes(live.read_bytes() + b'\nsynonyms: [replacement]\n')
    current[NAME] = sha(live.read_bytes())
    assert not ranking_eval.compatible_input_files(expected, current)


def report_fixture(tmp_path, monkeypatch, materialized_hash):
    raw = (REPO / NAME).read_bytes()
    expected, current = {NAME: ORIGINAL_HASH}, {NAME: sha(raw)}
    frozen = '0' * 64
    hotel = Hotel(id='fixture-hotel', name='Known-answer fixture', lat=43.5, lon=39.7)
    row = {'id': 'fixture', 'split': 'dev', 'parsed': {'text': ''}, 'judgments': {hotel.id: {'grade': None}},
           'input_files_sha256': expected, 'input_facts_sha256': frozen}
    gold = tmp_path / 'gold.jsonl'
    gold.write_text(json.dumps(row) + '\n')
    monkeypatch.setattr(ranking_eval, 'load_inputs', lambda: ([hotel], [], [], {}, current, materialized_hash))
    return gold, frozen


def test_metadata_exception_cannot_publish_ndcg_for_changed_materialization(tmp_path, monkeypatch):
    gold, _ = report_fixture(tmp_path, monkeypatch, '1' * 64)
    with pytest.raises(ValueError, match='Frozen fact materialization changed'):
        ranking_eval.evaluate_gold(gold)


def test_report_names_metadata_exception_and_the_exact_materialization_hash(tmp_path, monkeypatch):
    gold, frozen = report_fixture(tmp_path, monkeypatch, '0' * 64)
    report = ranking_eval.evaluate_gold(gold)
    assert report['input_materialization_matches_frozen'] is True
    assert report['input_facts_sha256'] == frozen
    assert report['input_file_validation'] == 'exact_sha256_or_hash_verified_yaml_metadata_v1'
    assert report['metadata_only_input_changes'][0]['original_sha256'] == ORIGINAL_HASH
    assert report['metadata_only_input_changes'][0]['original_snapshot'] == str(SNAPSHOT)
    assert report['human_labeled'] is False and report['ndcg_at_5'] is None
