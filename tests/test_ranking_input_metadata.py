"""Frozen labels accept a display-only addition, never altered evidence or thresholds."""
import hashlib

import pytest

from scripts import ranking_eval


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("label", [b"display_name: Guest label\n", "display_name: Вечернее солнце\n".encode()])
def test_frozen_yaml_accepts_only_the_added_single_display_name_line(tmp_path, monkeypatch, label):
    monkeypatch.setattr(ranking_eval, "ROOT", tmp_path)
    name = "data/characteristics/test.yaml"
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    original = b"id: test\nname_ru: Parser name\nsearch:\n  default_value: 10\n"
    edited = original.replace(b"search:\n", label + b"search:\n")
    path.write_bytes(edited)
    assert ranking_eval.compatible_input_files({name: sha(original)}, {name: sha(edited)})
    changed = edited.replace(b"default_value: 10", b"default_value: 20")
    path.write_bytes(changed)
    assert not ranking_eval.compatible_input_files({name: sha(original)}, {name: sha(changed)})


@pytest.mark.parametrize("name,changed", [
    ("data/seed/hotels.csv.gz", b"display_name: Guest label\noriginal\n"),
    ("data/characteristics/test.yaml", b"display_name: A\ndisplay_name: B\noriginal\n"),
    ("data/characteristics/test.yaml", b"display_name: |\n  multiple lines\noriginal\n"),
])
def test_metadata_exception_cannot_hide_other_files_or_duplicate_and_multiline_keys(tmp_path, monkeypatch, name, changed):
    monkeypatch.setattr(ranking_eval, "ROOT", tmp_path)
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    path.write_bytes(changed)
    assert not ranking_eval.compatible_input_files({name: sha(b"original\n")}, {name: sha(changed)})


def test_file_set_changes_remain_incompatible():
    assert not ranking_eval.compatible_input_files({"a": "old"}, {"b": "old"})
