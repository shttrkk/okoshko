"""Known-answer fixtures: synthetic hotels, manually chosen expected outcomes.

These tests measure contract behaviour and regressions, not real-world accuracy.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from backend.agents.explainer import disappointments, recommend_room
from backend.agents.query_parser import parse_query
from backend.agents.ranker import rank_hotels
from backend.agents.similarity import similar_hotels
from backend.contracts import (
    CharacteristicSpec,
    Criterion,
    Evidence,
    Fact,
    Hotel,
    ParsedQuery,
    Scope,
    SearchRequest,
    SearchSpec,
    ToolCall,
)

ROOT = Path(__file__).resolve().parents[2]
CORPUS = yaml.safe_load((ROOT / "data/eval/queries.yaml").read_text())


@pytest.fixture
def catalog():
    return [CharacteristicSpec.model_validate(yaml.safe_load(path.read_text()))
            for path in sorted((ROOT / "data/characteristics").glob("*.yaml"))]


def hotel(id):
    return Hotel(id=id, name="Synthetic " + id, lat=43.58, lon=39.72)


def fact(hotel_id, cid, value, *, side=None, low=None, high=None, room_type=None, month=None, confidence=.9, status="computed", synthetic=False):
    id = f"fixture-{hotel_id}-{cid}-{side}-{low}-{room_type}-{month}"
    return Fact(id=id, hotel_id=hotel_id, characteristic_id=cid,
                scope=Scope(facade=side, floor_min=low, floor_max=high, room_type=room_type, month=month),
                value=value, confidence=confidence, status=status,
                supporting_claim_ids=["claim-" + id],
                evidence=[Evidence(id="evidence-" + id, kind="simulation", label="Known-answer synthetic test fixture",
                                   source_ids=["synthetic_test_fixture"], quote="Synthetic known-answer fixture")],
                valid_until=datetime.now(UTC)+timedelta(days=1), synthetic=synthetic)


def required(*ids):
    return ParsedQuery(text="fixture", must=[Criterion(characteristic_id=cid, label=cid, field="available") for cid in ids])


@pytest.mark.parametrize("row", CORPUS["queries"], ids=lambda row: row["id"])
def test_persona_query_golden_conditions(row, catalog):
    parsed = parse_query(SearchRequest(text=row["text"]), catalog)
    expected = row["expected"]
    for group in ("must", "nice"):
        for condition in expected.get(group, []):
            assert any(c.characteristic_id == condition["id"] and all(getattr(c, key) == value for key, value in condition.items() if key != "id")
                       for c in getattr(parsed, group)), (row["id"], group, condition, parsed)
    for key in ("month", "facade", "min_floor", "pet_weight_kg", "unmapped"):
        if key in expected:
            assert getattr(parsed, key) == expected[key]
    for key in ("check_in", "check_out"):
        if key in expected:
            assert getattr(parsed, key).isoformat() == expected[key]
    if "must_count" in expected:
        assert len(parsed.must) == expected["must_count"]
    for cid in expected.get("absent", []):
        assert cid not in [c.characteristic_id for c in [*parsed.must, *parsed.nice]]
    for term in expected.get("unmapped_contains", []):
        assert any(term in fragment for fragment in parsed.unmapped)
    if expected.get("region_out_of_scope"):
        assert "region_out_of_scope" in parsed.unmapped
        assert any("Сейчас данные собраны для Сочи" in c for c in parsed.clarifications)
    if expected.get("no_region_warning"):
        assert "region_out_of_scope" not in parsed.unmapped
    if "clarification_contains" in expected:
        assert any(expected["clarification_contains"] in q for q in parsed.clarifications)
    assert len(parsed.clarifications) <= 2


def test_corpus_is_explicitly_synthetic_and_has_no_fake_ndcg():
    assert len(CORPUS["queries"]) >= 25
    assert CORPUS["label_type"] == "synthetic_persona_queries"
    assert "не nDCG" in CORPUS["methodology"]
    assert "judgments" not in CORPUS


def test_studio_characteristic_is_searchable_without_code_change(catalog):
    spec = CharacteristicSpec(id="morning_swim", name_ru="Утреннее купание", description="Synthetic studio fixture",
                              synonyms=["утреннее купание", "плавать до завтрака"], value_schema={"type": "object"},
                              methods=[ToolCall(tool="walk_route")])
    result = parse_query(SearchRequest(text="Хочу плавать до завтрака"), [*catalog, spec])
    assert [c.characteristic_id for c in result.must] == ["morning_swim"]
    hits = rank_hotels([hotel("new")], [fact("new", "morning_swim", {"available": True})], result, [spec])
    assert hits[0].group == "all"


def test_dynamic_numeric_field_uses_catalog_default_and_explicit_threshold(catalog):
    spec = CharacteristicSpec(id="shade_hours", name_ru="Тень", description="Synthetic fixture", synonyms=["тень"],
                              value_schema={"type": "object"}, methods=[ToolCall(tool="sun_window")],
                              search=SearchSpec(kind="numeric", field="hours", default_op="gte", default_value=2))
    result = parse_query(SearchRequest(text="тень от 4 часов"), [*catalog, spec])
    assert result.must[0].field == "hours"
    assert result.must[0].op == "gte"
    assert result.must[0].value == 4


@pytest.mark.parametrize("text", ["В Москве тихо", "Саратов", "в Воронеже тихий номер", "в городе Зеленоградске тихо"])
def test_other_city_never_returns_sochi_hotels(text, catalog):
    parsed = parse_query(SearchRequest(text=text), catalog)
    assert "region_out_of_scope" in parsed.unmapped
    assert rank_hotels([hotel("sochi")], [], parsed, catalog) == []


def test_request_dates_override_text_and_set_month(catalog):
    result = parse_query(SearchRequest(text="закат в сентябре", check_in=date(2027, 12, 1), check_out=date(2027, 12, 8)), catalog)
    assert result.month == 12
    assert result.check_in == date(2027, 12, 1)


@pytest.mark.parametrize("text", ["с 14 по 21 сентября 2027 тихо", "14–21.09.2027 тихий номер", "14.09.2027–21.09.2027 тихий номер"])
def test_russian_date_ranges(text, catalog):
    result = parse_query(SearchRequest(text=text), catalog)
    assert result.check_in == date(2027, 9, 14)
    assert result.check_out == date(2027, 9, 21)


def test_floor_interval_and_numeric_noise_threshold(catalog):
    parsed = parse_query(SearchRequest(text="тихий номер не выше 40 дБ, вид на море с 5 по 8 этаж на северо-восток"), catalog)
    quiet = next(c for c in parsed.must if c.characteristic_id == "quiet_night")
    assert quiet.field == "night_db_max" and quiet.op == "lte" and quiet.value == 40
    assert parsed.facade == "NE"
    sea = next(c for c in parsed.must if c.characteristic_id == "sea_view")
    assert sea.scope.floor_min == 5 and sea.scope.floor_max == 8


def test_preferred_side_does_not_become_a_hard_constraint(catalog):
    parsed = parse_query(SearchRequest(text="нужен тихий номер, желательно на запад"), catalog)
    assert parsed.facade is None
    hits = rank_hotels([hotel("h")], [fact("h", "quiet_night", {"available": True}, side="E")], parsed, catalog)
    assert hits[0].group == "all"


def test_stroller_route_can_explicitly_require_no_steps(catalog):
    parsed = parse_query(SearchRequest(text="до пляжа без лестниц"), catalog)
    assert any(c.field == "has_steps" and c.value is False for c in parsed.must)
    hit = rank_hotels([hotel("h")], [fact("h", "beach_walk", {"available": True, "minutes": 7, "has_steps": True})], parsed, catalog)[0]
    assert hit.group == "almost"
    assert next(v for v in hit.verdicts if v.label == "Без лестниц до пляжа").status == "no"


def test_value_and_scope_disagreement_abstains(catalog):
    parsed = ParsedQuery(text="", month=6, must=[Criterion(characteristic_id="climate", label="вода", field="water_c", op="gte", value=20)])
    inconsistent = fact("h", "climate", {"month": 9, "water_c": 26}, month=6)
    assert rank_hotels([hotel("h")], [inconsistent], parsed, catalog)[0].verdicts[0].status == "unknown"


def test_user_edited_chips_preserved_and_region_guard_still_applies(catalog):
    edited = ParsedQuery(text="", nice=[Criterion(characteristic_id="quiet_night", label="quiet")])
    result = parse_query(SearchRequest(text="в Москве", parsed=edited), catalog)
    assert result.nice == edited.nice
    assert "region_out_of_scope" in result.unmapped


def test_live_parser_cannot_create_non_catalog_characteristic(catalog):
    class FakeClient:
        def structured(self, output_model, messages, fallback, **kwargs):
            return ParsedQuery(text="", must=[Criterion(characteristic_id="invented_pool", label="бассейн")]), "live"
    result = parse_query(SearchRequest(text="бассейн"), catalog, llm=FakeClient())
    assert not result.must
    assert "бассейн" in result.unmapped


def test_ranking_prefers_compatible_room_over_scattered_good_facades(catalog):
    parsed = required("sea_view", "sunset_view", "quiet_night")
    facts = [fact("good", "sea_view", {"available": True}, side="W", low=5, high=8),
             fact("good", "sunset_view", {"available": True}, side="W", low=6, high=9, month=9),
             fact("good", "quiet_night", {"available": True}, side="W"),
             fact("split", "sea_view", {"available": True}, side="W", low=5, high=8),
             fact("split", "sunset_view", {"available": True}, side="W", low=6, high=9, month=9),
             fact("split", "quiet_night", {"available": True}, side="E"),
             fact("split", "quiet_night", {"available": False}, side="W")]
    hits = rank_hotels([hotel("split"), hotel("empty"), hotel("good")], facts, parsed, catalog)
    assert hits[0].hotel.id == "good"
    assert hits[0].group == "all"
    assert "6–8" in hits[0].room_recommendation
    split = next(h for h in hits if h.hotel.id == "split")
    assert split.group == "almost"
    assert split.must_satisfied == 2
    assert "Один номер" in split.room_recommendation
    assert "разным" in split.summary
    assert next(v for v in split.verdicts if v.characteristic_id == "quiet_night").status == "no"
    assert next(h for h in hits if h.hotel.id == "empty").group == "unknown"


@pytest.mark.parametrize("facts", [
    [fact("h", "sea_view", {"available": True}, side="W", low=5, high=6), fact("h", "quiet_night", {"available": True}, side="W", low=1, high=4)],
    [fact("h", "sea_view", {"available": True}, room_type="suite"), fact("h", "quiet_night", {"available": True}, room_type="standard")],
])
def test_floor_and_room_type_intersections_are_required(facts, catalog):
    parsed = required("sea_view", "quiet_night")
    hit = rank_hotels([hotel("h")], facts, parsed, catalog)[0]
    assert hit.group != "all"
    assert "Один номер" in hit.room_recommendation
    assert "разных" in " ".join(hit.disappointments)


def test_negative_facades_do_not_erase_one_actual_view(catalog):
    facts = [fact("h", "sea_view", {"available": False}, side=s) for s in ("N", "NE", "E", "SE", "S", "SW", "NW")]
    facts.append(fact("h", "sea_view", {"available": True, "min_floor": 4}, side="W", low=4, high=9))
    hit = rank_hotels([hotel("h")], facts, required("sea_view"), catalog)[0]
    assert hit.group == "all"
    assert hit.verdicts[0].fact_ids == [facts[-1].id]
    assert "западная" in hit.room_recommendation
    assert not any("не выполняется" in note for note in hit.disappointments)


def test_hotel_wide_website_claim_does_not_apply_to_every_room(catalog):
    facts = [fact("h", "sea_view", {"available": True}),
             fact("h", "sea_view", {"available": True}, side="W"),
             fact("h", "sea_view", {"available": False}, side="E"),
             fact("h", "quiet_night", {"available": True}, side="E")]
    hit = rank_hotels([hotel("h")], facts, required("sea_view", "quiet_night"), catalog)[0]
    assert hit.group != "all"
    assert "Один номер" in hit.room_recommendation


@pytest.mark.parametrize("status", ["stale", "disputed", "unknown"])
def test_noncurrent_facts_never_produce_positive_matches(status, catalog):
    hit = rank_hotels([hotel("h")], [fact("h", "pets", {"available": True}, status=status)], required("pets"), catalog)[0]
    assert hit.group == "unknown"
    assert hit.verdicts[0].status == "unknown"


def test_expiration_is_enforced_even_when_status_is_computed(catalog):
    expired = fact("h", "pets", {"available": True})
    expired.valid_until = datetime.now(UTC)-timedelta(seconds=1)
    hit = rank_hotels([hotel("h")], [expired], required("pets"), catalog)[0]
    assert hit.verdicts[0].status == "unknown"
    assert "устарели" in hit.verdicts[0].text


def test_missing_evidence_cannot_support_a_yes(catalog):
    unsupported = fact("h", "pets", {"available": True})
    unsupported.evidence = []
    hit = rank_hotels([hotel("h")], [unsupported], required("pets"), catalog)[0]
    assert hit.group == "unknown"
    assert hit.verdicts[0].status == "unknown"


def test_low_confidence_negative_is_unknown_not_no(catalog):
    facts = [fact("low", "quiet_night", {"available": False, "night_db_min": 38, "night_db_max": 54}, side="E", confidence=.38),
             fact("high", "quiet_night", {"available": False}, side="E", confidence=.9)]
    hits = rank_hotels([hotel("low"), hotel("high")], facts, required("quiet_night"), catalog)
    assert hits[0].hotel.id == "low"
    assert hits[0].verdicts[0].status == "unknown"
    assert hits[1].verdicts[0].status == "no"
    assert "±8 дБ" in hits[0].verdicts[0].caveat


def test_low_confidence_positive_is_partial_and_cannot_guarantee_sleep(catalog):
    value = {"available": True, "night_db_min": 35, "night_db_max": 51}
    hit = rank_hotels([hotel("h")], [fact("h", "quiet_night", value, side="E", confidence=.38)], required("quiet_night"), catalog)[0]
    assert hit.group == "almost"
    assert hit.verdicts[0].status == "partial"
    assert "не гарантирует" in hit.verdicts[0].caveat


def test_month_scope_and_value_month_both_select_trip_month(catalog):
    parsed = ParsedQuery(text="", month=6, must=[Criterion(characteristic_id="climate", label="вода", field="water_c", op="gte", value=20)])
    facts = [fact("h", "climate", {"month": 9, "water_c": 26}, month=9),
             fact("h", "climate", {"month": 6, "water_c": 18}, month=6)]
    hit = rank_hotels([hotel("h")], facts, parsed, catalog)[0]
    assert hit.verdicts[0].status == "no"
    assert hit.verdicts[0].fact_ids == [facts[1].id]
    assert "18" in hit.verdicts[0].text


def test_other_month_negative_does_not_override_current_positive(catalog):
    facts = [fact("h", "sunset_view", {"available": True}, side="W", month=9),
             fact("h", "sunset_view", {"available": False}, side="W", month=12)]
    assert rank_hotels([hotel("h")], facts, required("sunset_view"), catalog)[0].group == "all"


def test_pet_weight_and_synthetic_provenance_are_visible(catalog):
    parsed = parse_query(SearchRequest(text="с собакой 18 кг"), catalog)
    facts = [fact("small", "pets", {"available": True, "species": ["dog"], "max_weight_kg": 10}),
             fact("large", "pets", {"available": True, "species": ["dog"], "max_weight_kg": 20, "fee_rub_night": 1000}, synthetic=True)]
    hits = rank_hotels([hotel("small"), hotel("large")], facts, parsed, catalog)
    assert hits[0].hotel.id == "large"
    assert hits[0].group == "all"
    assert "Синтетические" in hits[0].verdicts[0].caveat
    assert "1000" in hits[0].verdicts[0].text
    assert hits[1].verdicts[0].status == "no"


def test_known_negative_can_satisfy_explicit_negation(catalog):
    parsed = parse_query(SearchRequest(text="без собак"), catalog)
    hit = rank_hotels([hotel("h")], [fact("h", "pets", {"available": False})], parsed, catalog)[0]
    assert hit.group == "all"
    assert hit.verdicts[0].status == "yes"


def test_windows_partial_fields_do_not_prove_soundproofing(catalog):
    item = fact("h", "soundproofing", {"available": None, "windows": "double", "blackout": None})
    hit = rank_hotels([hotel("h")], [item], required("soundproofing"), catalog)[0]
    assert hit.verdicts[0].status == "partial"
    assert "шумоизоляция не подтверждена" in hit.verdicts[0].caveat
    assert "двойные окна" in hit.verdicts[0].text


def test_unknown_route_never_prints_none_minutes(catalog):
    parsed = parse_query(SearchRequest(text="до пляжа"), catalog)
    item = fact("h", "beach_walk", {"available": None, "minutes": None, "return_minutes": None})
    hit = rank_hotels([hotel("h")], [item], parsed, catalog)[0]
    assert hit.verdicts[0].status == "unknown"
    assert "None" not in " ".join([v.text + (v.caveat or "") for v in hit.verdicts] + hit.disappointments)


def test_walk_return_ascent_steps_and_crossings_are_not_hidden(catalog):
    item = fact("h", "beach_walk", {"available": True, "minutes": 8, "return_minutes": 13, "return_ascent_m": 40, "has_steps": True, "crossings": ["railway"]})
    parsed = parse_query(SearchRequest(text="с коляской до пляжа"), catalog)
    hit = rank_hotels([hotel("h")], [item], parsed, catalog)[0]
    text = " ".join([v.text + (v.caveat or "") for v in hit.verdicts] + hit.disappointments)
    assert "13 мин" in text and "40 м" in text
    assert "лестниц" in text and "переход" in text and "Тоблера" in text


def test_every_nonunknown_verdict_has_fact_and_evidence_references(catalog):
    item = fact("h", "sea_view", {"available": True}, side="W", low=3)
    hit = rank_hotels([hotel("h")], [item], required("sea_view"), catalog)[0]
    assert hit.verdicts[0].fact_ids == [item.id]
    assert hit.verdicts[0].evidence_refs == [item.evidence[0].id]
    assert recommend_room([item], required("sea_view")) == hit.room_recommendation
    assert isinstance(disappointments([item]), list)


def test_similarity_uses_weighted_facts_excludes_self_and_handles_missing_data(catalog):
    facts = [fact(id, "beach_walk", {"available": True, "minutes": minutes}) for id, minutes in [("ref", 8), ("near", 9), ("far", 20)]]
    facts += [fact(id, "sea_view", {"available": value}, side="W", low=4) for id, value in [("ref", True), ("near", True), ("far", False)]]
    hits = similar_hotels("ref", [hotel(id) for id in ("far", "missing", "near", "ref")], facts, catalog)
    assert [h.hotel.id for h in hits] == ["near", "far", "missing"]
    assert hits[-1].group == "unknown"
    assert hits[0].score > hits[1].score > hits[2].score
    assert similar_hotels("missing-id", [hotel("ref")], facts, catalog) == []


def test_all_must_accepts_supported_estimate_but_not_unknown_details(catalog):
    query = ParsedQuery(text="known synthetic route", must=[Criterion(characteristic_id="beach_walk", label="Пляж", field="minutes", op="lte", value=10)])
    [hit] = rank_hotels([hotel("a")], [fact("a", "beach_walk", {"available": True, "minutes": 8}, confidence=.66)], query, catalog)
    assert hit.group == "all" and hit.verdicts[0].status == "partial"
    assert hit.verdicts[0].confidence == .66 and "предварительно" in hit.summary
    [low] = rank_hotels([hotel("a")], [fact("a", "beach_walk", {"available": True, "minutes": 8}, confidence=.38)], query, catalog)
    assert low.group != "all"
    pets = ParsedQuery(text="с собакой", must=[Criterion(characteristic_id="pets", label="Питомцы", field="available")], pet_weight_kg=20)
    [incomplete] = rank_hotels([hotel("a")], [fact("a", "pets", {"available": True}, confidence=.85)], pets, catalog)
    assert incomplete.group != "all"  # High confidence cannot fill missing weight/species.
