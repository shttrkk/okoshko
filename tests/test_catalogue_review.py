"""Reviewed identity holds must precede scoring and never rewrite raw hotels."""
from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from functools import partial

import pytest
import yaml

import backend.agents.ranker as ranker
from backend.catalogue import (
    EVIDENCE_URL,
    HOLD,
    CatalogueReviewError,
    annotate,
    load_reviews,
    review_for,
    searchable,
)
from backend.contracts import CharacteristicSpec, Criterion, Hotel, ParsedQuery
from backend.settings import DATA
from backend.tests.test_search import fact

LEDGER = DATA / "seed/catalogue_reviews.json"
HELD_IDS = {
    "overture-129b6d6a-76ce-4140-8495-3eca01fa691d",
    "overture-6f163d7e-6eea-4cf3-bce8-79dc3e6e7f5c",
    "overture-43748b4b-4201-473e-aa6a-c8e34bcaede4",
}


@pytest.fixture
def document():
    return json.loads(LEDGER.read_text())


@pytest.fixture
def local_ledger(tmp_path, document):
    path = tmp_path / "seed/catalogue_reviews.json"
    path.parent.mkdir()
    path.write_text(json.dumps(document))
    return tmp_path, path


@pytest.fixture
def beach_spec():
    return CharacteristicSpec.model_validate(yaml.safe_load((DATA / "characteristics/beach_walk.yaml").read_text()))


def _hotel(hotel_id, **extra):
    return Hotel(id=hotel_id, name="Synthetic catalogue review fixture", lat=43.58, lon=39.72, **extra)


def test_shipped_reviews_have_exactly_the_three_evidence_backed_holds():
    rows = load_reviews()
    assert set(rows) == HELD_IDS
    for hotel_id in HELD_IDS:
        assert not searchable(_hotel(hotel_id))
        review = rows[hotel_id]
        assert review["confidence"]["kind"] == "expert_uncalibrated"
        assert review["evidence"] and review["limitations_ru"]
    assert not any("Tortuga" in row.get("name_at_review", "") for row in rows.values())


def test_unknown_ids_and_unrelated_falsy_tags_are_searchable():
    for hotel_id in ["synthetic-unit", "osm-way-218836911", "unknown", next(iter(HELD_IDS)).upper()]:
        for tags in [None, False, {}, {"searchable": False}, {"catalogue_review": {"disposition": HOLD}}]:
            assert searchable({"id": hotel_id, "tags": tags})
    for hotel_id in HELD_IDS:
        assert review_for(hotel_id + " ") is None


def test_masha_anchors_and_real_hotels_without_floors_survive_filtering():
    acceptance = json.loads((DATA / "eval/persona_acceptance.json").read_text())
    anchors = {row["hotel_id"] for row in acceptance["synthetic_hotels"]}
    raw = json.loads((DATA / "seed/hotels.json").read_text())
    hotels = [Hotel.model_validate(hotel) for hotel in raw]
    retained = {hit.hotel.id for hit in ranker.rank_hotels(hotels, [], ParsedQuery(text=""), [])}
    assert anchors <= retained
    assert HELD_IDS.isdisjoint(retained)
    assert len(retained) == len(hotels) - 3
    dolphin = next(hotel for hotel in hotels if hotel.id == "osm-way-218836911")
    assert dolphin.floors is None and dolphin.id in retained


def test_a_high_scoring_held_beach_never_wins_numeric_or_browse_search(beach_spec):
    held = _hotel("overture-129b6d6a-76ce-4140-8495-3eca01fa691d")
    good = _hotel("ordinary-synthetic")
    facts = [fact(held.id, "beach_walk", {"minutes": .57}, confidence=.99),
             fact(good.id, "beach_walk", {"minutes": 4}, confidence=.66)]
    parsed = ParsedQuery(text="пляж до 5 минут", must=[
        Criterion(characteristic_id="beach_walk", label="Пляж", field="minutes", op="lte", value=5)])
    hits = ranker.rank_hotels([held, good], facts, parsed, [beach_spec])
    assert [hit.hotel.id for hit in hits] == [good.id]
    assert hits[0].group == "all" and hits[0].must_satisfied == 1
    assert [hit.hotel.id for hit in ranker.rank_hotels([held, good], facts, ParsedQuery(text=""), [beach_spec])] == [good.id]


def test_held_reference_is_not_used_even_if_its_facts_exist(beach_spec):
    held = _hotel(next(iter(HELD_IDS)))
    ordinary = _hotel("synthetic-reference")
    other = _hotel("synthetic-candidate")
    hotels = [held, ordinary, other]
    facts = [fact(hotel.id, "beach_walk", {"minutes": 2}) for hotel in hotels]
    assert ranker.similar_hotels(held.id, hotels, facts, [beach_spec]) == []
    assert ranker.rank_hotels(hotels, facts, ParsedQuery(text="similar", similar_to=held.id), [beach_spec]) == []
    hits = ranker.similar_hotels(ordinary.id, hotels, facts, [beach_spec])
    assert [hit.hotel.id for hit in hits] == [other.id]


def test_expired_review_remains_a_hold(local_ledger, document):
    data_dir, path = local_ledger
    row = document["reviews"][0]
    row["observed_at"] = "2020-01-01T00:00:00Z"
    row["valid_until"] = "2020-02-01T00:00:00Z"
    path.write_text(json.dumps(document))
    assert not searchable({"id": row["hotel_id"]}, data_dir)
    assert review_for(row["hotel_id"], data_dir)["valid_until"] == row["valid_until"]


@pytest.mark.parametrize("as_model", [False, True])
def test_annotation_preserves_type_and_does_not_mutate_inputs_or_cached_evidence(as_model):
    hotel_id = next(iter(HELD_IDS))
    original = {"id": hotel_id, "name": "Synthetic annotation fixture", "lat": 43.58, "lon": 39.72,
                "tags": {"nested": {"original": True}, "searchable": True}}
    hotel = Hotel.model_validate(original) if as_model else deepcopy(original)
    before = hotel.model_dump() if as_model else deepcopy(hotel)
    annotated = annotate(hotel)
    assert type(annotated) is type(hotel)
    assert annotated is not hotel
    tags = annotated.tags if as_model else annotated["tags"]
    assert tags["nested"] == {"original": True}
    assert set(tags["catalogue_review"]) == {
        "disposition", "reason_ru", "source", "method", "confidence", "observed_at", "valid_until", "evidence_url"}
    assert tags["catalogue_review"]["evidence_url"] == EVIDENCE_URL
    tags["nested"]["original"] = False
    tags["catalogue_review"]["confidence"]["value"] = 0
    assert (hotel.model_dump() if as_model else hotel) == before
    assert review_for(hotel_id)["confidence"]["value"] > 0


def test_annotation_removes_old_untrusted_review_without_using_it_as_eligibility():
    original = {"id": "unreviewed", "tags": {"catalogue_review": {"disposition": HOLD}, "keep": 1}}
    assert searchable(original)
    assert annotate(original) == {"id": "unreviewed", "tags": {"keep": 1}}
    assert "catalogue_review" in original["tags"]


def test_mutating_a_returned_full_review_cannot_change_the_cached_policy():
    rows = load_reviews()
    hotel_id = next(iter(HELD_IDS))
    rows[hotel_id]["disposition"] = "allow"
    review = review_for(hotel_id)
    review["evidence"].clear()
    assert not searchable({"id": hotel_id})
    assert review_for(hotel_id)["evidence"]


def test_empty_valid_ledger_is_supported_for_another_region(local_ledger, document):
    data_dir, path = local_ledger
    document["reviews"] = []
    path.write_text(json.dumps(document))
    assert load_reviews(data_dir) == {}
    assert searchable({"id": next(iter(HELD_IDS))}, data_dir)


def test_atomic_file_replacement_invalidates_cache_even_with_equal_size_and_mtime(local_ledger, document):
    data_dir, path = local_ledger
    first = document["reviews"][0]
    first["reason_ru"] = "first reason"
    path.write_text(json.dumps(document))
    assert review_for(first["hotel_id"], data_dir)["reason_ru"] == "first reason"
    old_stat = path.stat()
    first["reason_ru"] = "other reason"
    replacement = path.with_suffix(".new")
    replacement.write_text(json.dumps(document))
    assert replacement.stat().st_size == old_stat.st_size
    os.utime(replacement, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    replacement.replace(path)
    assert review_for(first["hotel_id"], data_dir)["reason_ru"] == "other reason"


def _corrupt(document, case):
    if case == "missing":
        return None
    if case == "broken_json":
        return '{"reviews": [broken'
    if case == "duplicate_key":
        return '{"schema":"hotel_identity_reviews_v1","schema":"other"}'
    if case == "broken_row":
        document["reviews"].append("broken row")
    elif case == "duplicate_id":
        document["reviews"].append(deepcopy(document["reviews"][0]))
    elif case == "substitution":
        document = {"schema": "unrelated", "reviews": []}
    elif case == "unsupported_disposition":
        document["reviews"][0]["disposition"] = "allow"
    elif case == "confidence_not_expert":
        document["reviews"][0]["confidence"]["kind"] = "measured"
    elif case == "nonfinite_confidence":
        document["reviews"][0]["confidence"]["value"] = float("nan")
    elif case == "missing_evidence":
        document["reviews"][0]["evidence"] = []
    elif case == "date_without_timezone":
        document["reviews"][0]["observed_at"] = "2020-01-01"
    return json.dumps(document)


@pytest.mark.parametrize("case", ["missing", "broken_json", "broken_row", "duplicate_id", "duplicate_key",
                                 "substitution", "unsupported_disposition", "confidence_not_expert",
                                 "nonfinite_confidence", "missing_evidence", "date_without_timezone"])
@pytest.mark.parametrize("warm", [False, True])
def test_invalid_required_ledger_raises_cold_and_preserves_holds_when_warm(
        local_ledger, document, caplog, case, warm):
    data_dir, path = local_ledger
    if warm:
        assert set(load_reviews(data_dir)) == HELD_IDS
    broken = _corrupt(document, case)
    if broken is None:
        path.unlink()
    else:
        path.write_text(broken)
    if not warm:
        with pytest.raises(CatalogueReviewError):
            searchable({"id": "unknown-unit"}, data_dir)
    else:
        with caplog.at_level(logging.WARNING, logger="backend.catalogue"):
            for hotel_id in HELD_IDS:
                assert not searchable({"id": hotel_id}, data_dir)
            assert searchable({"id": "unknown-unit"}, data_dir)
        assert sum("retaining the last validated" in message for message in caplog.messages) == 1


def test_repaired_ledger_reloads_after_a_failed_hot_read(local_ledger, document):
    data_dir, path = local_ledger
    assert len(load_reviews(data_dir)) == 3
    path.write_text("invalid")
    assert len(load_reviews(data_dir)) == 3
    document["reviews"] = []
    path.write_text(json.dumps(document))
    assert load_reviews(data_dir) == {}


def test_ranker_does_not_silently_swallow_cold_configuration_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(ranker, "searchable", partial(searchable, data_dir=tmp_path))
    with pytest.raises(CatalogueReviewError):
        ranker.rank_hotels([_hotel("synthetic")], [], ParsedQuery(text=""), [])
