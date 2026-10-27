"""Panel regressions: honest provenance and consistent guest-facing numbers."""
from datetime import timedelta

import pytest

from backend.agents.explainer import approximate, disappointments, supporting_basis
from backend.agents.guest_text import beach_name, criterion_label
from backend.agents.ranker import rank_hotels, relative_reason
from backend.agents.similarity import similar_hotels
from backend.contracts import (
    CharacteristicSpec,
    Criterion,
    CriterionVerdict,
    Evidence,
    Fact,
    Hotel,
    ParsedQuery,
    Scope,
    SearchHit,
    SearchSpec,
    ToolCall,
    now,
)


def witness(*, kind="calculation", quote="Подтверждаем размещение с питомцем", **updates):
    return Fact(id="fact", hotel_id="hotel", characteristic_id="pets", value={"available": True},
                status="confirmed", confidence=.9, supporting_claim_ids=["claim"],
                valid_until=now() + timedelta(days=1),
                evidence=[Evidence(id="proof", kind=kind, label="Источник", source_ids=["source"],
                                   quote=quote, artifact="/tests/known-answer.json")]).model_copy(update=updates)


def evaluate(facts, criterion=None):
    criterion = criterion or Criterion(characteristic_id="pets", label="С питомцем", field="available")
    return rank_hotels([Hotel(id="hotel", name="Отель", lat=43.58, lon=39.72, floors=5)], facts,
                       ParsedQuery(text="", must=[criterion]), [])[0]


def test_calculation_yes_keeps_rank_support_but_is_not_hotelier_confirmation():
    [v] = evaluate([witness()]).verdicts
    assert v.status == "yes" and v.basis == "calculation" and not v.hotelier_confirmed
    [real] = evaluate([witness(kind="hotelier")]).verdicts
    assert real.status == "yes" and real.basis == "hotelier" and real.hotelier_confirmed


def test_synthetic_geometry_is_not_described_as_a_reply_from_the_hotel():
    [v] = evaluate([witness(synthetic=True)]).verdicts
    assert v.synthetic and v.basis == "calculation" and not v.hotelier_confirmed
    assert "Демо-данные" in v.caveat and "Ответ отеля" not in v.caveat
    lower = comparison_hit("ниже", comparison_verdict(status="unknown", confidence=0), 20)
    higher = comparison_hit("выше", v.model_copy(update={"characteristic_id": "quiet_night"}), 90)
    assert "отель ответил" not in relative_reason(lower, higher)


@pytest.mark.parametrize("updates", [
    {"synthetic": True}, {"status": "disputed"}, {"status": "stale"},
    {"valid_until": now() - timedelta(seconds=1)}, {"supporting_claim_ids": []},
    {"confidence": .65}, {"value": {"available": False}},
])
def test_only_current_positive_real_hotel_reply_counts(updates):
    assert not evaluate([witness(kind="hotelier", **updates)]).verdicts[0].hotelier_confirmed


@pytest.mark.parametrize("kind,quote", [("hotelier", None), ("hotelier", "  "),
                                        ("quote", "Да"), ("simulation", "Да"), ("mock", "Да")])
def test_a_reference_or_simulation_is_not_an_exact_real_hotel_reply(kind, quote):
    assert not evaluate([witness(kind=kind, quote=quote)]).verdicts[0].hotelier_confirmed


@pytest.mark.parametrize("kind", ["simulation", "mock"])
def test_mixed_demo_and_real_witness_cannot_receive_confirmation(kind):
    item = witness(kind="hotelier")
    item.evidence.append(item.evidence[0].model_copy(update={"id": "second-proof", "kind": kind}))
    result = evaluate([item]).verdicts[0]
    assert result.status == "yes" and not result.hotelier_confirmed
    assert result.basis == "mixed"


@pytest.mark.parametrize("updates", [{"inputs": {"synthetic": True}}, {"source_ids": ["hotelier_simulator"]}])
def test_demo_metadata_cannot_masquerade_as_real_hotelier_evidence(updates):
    item = witness(kind="hotelier")
    item.evidence[0] = item.evidence[0].model_copy(update=updates)
    assert not evaluate([item]).verdicts[0].hotelier_confirmed


def test_hotel_reply_for_another_scope_does_not_confirm_the_requested_room():
    item = witness(kind="hotelier", scope=Scope(facade="E", month=9))
    criterion = Criterion(characteristic_id="pets", label="С питомцем", field="available",
                          scope=Scope(facade="W", month=9))
    v = evaluate([item], criterion).verdicts[0]
    assert v.status == "unknown" and not v.hotelier_confirmed and v.basis == "unknown"


def test_provenance_uses_current_supporting_evidence_and_preserves_mixed_origins():
    calculation = witness()
    opened = witness(kind="quote", id="opened")
    real = witness(kind="hotelier", id="real")
    assert supporting_basis([opened]) == "open_data"
    assert supporting_basis([calculation, opened, real]) == "mixed"
    assert supporting_basis([real, witness(kind="simulation")]) == "mixed"
    assert supporting_basis([witness(kind="simulation")]) == "unknown"
    assert supporting_basis([witness(kind="hotelier", supporting_claim_ids=[])]) == "unknown"
    assert supporting_basis([witness(kind="hotelier", status="stale")]) == "unknown"


def test_guest_caveat_has_demo_label_but_raw_evidence_keeps_the_technical_detail():
    item = witness(kind="simulation", synthetic=True)
    raw = ["hotelier_simulator: вымышленная demo truth; confidence 85%",
           "Confidence относится к синтетической фикстуре", "Надёжность 85%", "85,5% надёжности", "Уточните конкретный номер у отеля"]
    item.evidence[0].limitations = raw.copy()
    before = item.model_dump()
    result = evaluate([item]).verdicts[0]
    assert "Ответ отеля · демо" in result.caveat
    assert "Уточните конкретный номер" in result.caveat
    assert not any(term in (result.text + result.caveat).lower() for term in ["simulator", "truth", "фикстур", "85%"])
    assert item.model_dump() == before


@pytest.mark.parametrize("value,expected", [(0, 0), (.01, 1), (.49, 1), (1.49, 1), (1.5, 2), (2.5, 3), (2.42, 2), (7.17, 7)])
def test_minutes_match_frontend_half_up_and_positive_minimum(value, expected):
    assert approximate(value, minutes=True) == f"≈ {expected}"


def test_positive_fraction_of_a_sunset_minute_is_not_displayed_as_zero():
    item = witness(characteristic_id="sunset_view", scope=Scope(facade="W", floor_min=1, floor_max=5, month=9),
                   value={"available": True, "visible_minutes": .2})
    v = evaluate([item], Criterion(characteristic_id="sunset_view", label="Закат", field="available")).verdicts[0]
    assert "солнце видно ≈ 1 мин" in v.text


@pytest.mark.parametrize("outbound,inbound,warning", [(2.42, 2.44, False), (2.5, 3.49, False), (2.49, 2.5, True)])
def test_return_warning_requires_a_difference_in_displayed_whole_minutes(outbound, inbound, warning):
    item = witness(characteristic_id="beach_walk", value={"minutes": outbound, "return_minutes": inbound})
    query = ParsedQuery(text="", must=[Criterion(characteristic_id="beach_walk", label="Пляж", field="minutes", op="lte", value=10)])
    notes = disappointments([item], query)
    assert any("Обратно с пляжа дольше" in text for text in notes) is warning
    if warning:
        assert "≈ 3 мин вместо ≈ 2 мин" in " ".join(notes)


@pytest.mark.parametrize("name", [None, "", "OSM osm-way-53086451", "osm-node-123", "way/123"])
def test_unnamed_beach_never_exposes_an_osm_identifier(name):
    assert beach_name(name) == "ближайший пляж"
    item = witness(characteristic_id="beach_walk", value={"minutes": 2.42, "beach_name": name})
    v = evaluate([item], Criterion(characteristic_id="beach_walk", label="Пляж", field="minutes", op="lte", value=10)).verdicts[0]
    assert "до ближайшего пляжа ≈ 2 мин" in v.text
    assert "osm" not in v.text.casefold()
    assert beach_name("Маяк") == "Маяк"


def test_stairs_are_factual_without_an_unsolicited_stroller_scenario():
    item = witness(characteristic_id="beach_walk", value={"minutes": 2, "has_steps": True})
    notes = disappointments([item], ParsedQuery(text="едем вдвоём"))
    assert "На пути к пляжу есть лестницы." in notes
    assert not any("коляск" in text for text in notes)


@pytest.mark.parametrize("cid,field,old,value,expected", [
    ("beach_walk", "minutes", "До пляжа ≤ 10 мин пешком, без лестниц", 7, "До пляжа ≤ 7 мин пешком, без лестниц"),
    ("nightlife", "venues_500m", "В гуще событий ≥ 30 заведений в 500 м", 60, "В гуще событий ≥ 60 заведений в 500 м"),
])
def test_edited_threshold_labels_are_derived_without_mutating_the_criterion(cid, field, old, value, expected):
    criterion = Criterion(characteristic_id=cid, label=old, field=field, op="lte" if cid == "beach_walk" else "gte",
                          value=value, no_stairs=cid == "beach_walk")
    before = criterion.model_dump()
    assert criterion_label(criterion) == expected
    assert evaluate([witness(characteristic_id=cid, value={field: value, "has_steps": False})], criterion).verdicts[0].label == expected
    assert criterion.model_dump() == before


def test_similarity_uses_units_rounds_minutes_and_never_exposes_reliability_percentages():
    hotels = [Hotel(id=identifier, name=identifier, lat=43.58, lon=39.72) for identifier in ["reference", "candidate"]]
    specs = [CharacteristicSpec(id=cid, name_ru=cid, description="Тест", value_schema={"type": "object"},
                                methods=[ToolCall(tool="poi_nearby")],
                                search=SearchSpec(kind="numeric", field=field, default_value=10))
             for cid, field in [("car_access", "parking_distance_m"), ("beach_walk", "minutes"), ("evening_heat", "exposure_index")]]
    facts = [witness(id=identifier + cid, hotel_id=identifier, characteristic_id=cid, value={field: value})
             for identifier, values in [("reference", [91, 2.42, .35]), ("candidate", [35, 3.5, .2])]
             for (cid, field), value in zip([(s.id, s.search.field) for s in specs], values, strict=True)]
    [hit] = similar_hotels("reference", hotels, facts, specs)
    text = " ".join(hit.similarity_reasons)
    assert "35 м" in text and "91 м" in text
    assert "%" not in text and "2,42" not in text
    # Only two differences are shown; every displayed numeric value has its unit.
    assert all(any(unit in v.text for unit in [" м", " мин", "индекс 0–1"]) for v in hit.verdicts)
    assert all(len(v.fact_ids) == 2 and not v.hotelier_confirmed for v in hit.verdicts)


def comparison_verdict(*, synthetic=False, confirmed=False, status="yes", confidence=.9):
    return CriterionVerdict(characteristic_id="quiet_night", label="Тишина ночью", required=True,
                            status=status, symbol={"yes": "✓", "partial": "≈", "unknown": "?"}[status], confidence=confidence,
                            text="По имеющимся данным", synthetic=synthetic, hotelier_confirmed=confirmed)


def comparison_hit(identifier, v, score):
    return SearchHit(hotel=Hotel(id=identifier, name=identifier, lat=43.58, lon=39.72),
                     score=score, group="all", must_total=1, verdicts=[v])


def test_relative_reason_explicitly_names_a_demo_reply_without_counting_it_as_confirmed():
    higher = comparison_hit("выше", comparison_verdict(synthetic=True), 90)
    current = comparison_hit("здесь", comparison_verdict(status="unknown", confidence=0), 20)
    reason = relative_reason(current, higher)
    assert "отель ответил на вопрос о тишине (демо-ответ)" in reason
    assert "подтверждено" not in reason


def test_only_real_hotel_flag_counts_in_relative_reason():
    calculation = comparison_hit("расчёт", comparison_verdict(), 90)
    unknown = comparison_hit("неизвестно", comparison_verdict(status="unknown", confidence=0), 20)
    assert "подтверждено" not in relative_reason(unknown, calculation)
    hotel = comparison_hit("отель", comparison_verdict(confirmed=True), 90)
    assert "подтверждено 1 из 1" in relative_reason(unknown, hotel)


def test_same_status_score_advantage_from_demo_does_not_masquerade_as_more_reliable_data():
    higher = comparison_hit("выше", comparison_verdict(synthetic=True, status="partial", confidence=.69), 64)
    current = comparison_hit("здесь", comparison_verdict(status="partial", confidence=.61), 60)
    reason = relative_reason(current, higher)
    assert "отель ответил на вопрос о тишине (демо-ответ)" in reason
    assert "надёжнее" not in reason and "подтверждено" not in reason
