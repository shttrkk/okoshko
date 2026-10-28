"""Round-three operator statements, with memory-only API writes."""
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.agents.answer_parser import parse_answer
from backend.agents.interviewer import create_question, generate_questions
from backend.agents.query_match import assess_hotel
from backend.agents.resolver import resolve_claims
from backend.api.hotelier import confirm_answer, router
from backend.contracts import (
    CharacteristicSpec,
    Claim,
    Criterion,
    Evidence,
    Fact,
    FieldUpdate,
    ParsedQuery,
    Question,
    Scope,
    now,
)
from backend.db.repository import get_repo
from backend.security import DEMO_ADMIN
from backend.tests.test_hotelier import MemoryRepo

ROOMS = "Номера 201–210 и 301–310 смотрят на море, у всех балкон. 211–220 во двор, без балкона. На первом этаже номеров нет."
WINDOWS = "Окна пластиковые, двойные, во всех номерах. Есть ли официальная шумоизоляция — не знаю. Кондиционеры есть везде."
ROUTE = "Да. До пляжа по набережной без ступенек, с коляской проходят свободно, у входа в отель пандус."
PETS = "Собак до 5 кг принимаем, кошек нет. Доплата 500 руб в сутки, в июле и августе с животными не заселяем."


def question(cid):
    return Question(hotel_id="test-hotel", characteristic_ids=[cid], text="Уточните")


def merged(answer, cid):
    return {key: value for update in answer.updates if update.characteristic_id == cid for key, value in update.value.items()}


@pytest.fixture
def api():
    repo = MemoryRepo()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_repo] = lambda: repo
    client = TestClient(app)
    token = client.post("/api/hotelier/login", json={"username": "hotelier", "password": "hotelier123"}).json()["token"]
    return repo, client, {"Authorization": "Bearer " + token}


def confirm_text(repo, cid, text, *, synthetic=False):
    q = create_question("test-hotel", cid, repo)
    answer = parse_answer(q, text, synthetic=synthetic)
    repo.insert("answers", answer.model_dump(mode="json"))
    return q, confirm_answer(answer.id, repo, principal=DEMO_ADMIN)


def test_marina_n6_window_adjective_and_partial_uncertainty():
    answer = parse_answer(question("soundproofing"), WINDOWS)
    value = merged(answer, "soundproofing")
    assert value == {"available": None, "windows": "double", "air_conditioning": True}
    assert answer.disposition == "parsed"
    assert answer.unknown_fields == ["soundproofing.available"]
    assert "blackout" not in value


def test_marina_n6_simulator_text_keeps_all_four_fields():
    answer = parse_answer(question("soundproofing"), "Окна двойные. Blackout нет, кондиционер есть. Шумоизоляция неизвестна.", synthetic=True)
    assert merged(answer, "soundproofing") == {"available": None, "windows": "double", "blackout": False, "air_conditioning": True}
    assert answer.synthetic
    assert answer.unknown_fields == ["soundproofing.available"]


def test_marina_n6_short_room_statement_keeps_sea_and_balcony_without_floor():
    answer = parse_answer(question("room_orientation"), "номера 201–210 на море, с балконом")
    value = merged(answer, "room_orientation")
    assert value["sea_view"] is True and value["balcony"] is True
    assert value["rooms"] == list(map(str, range(201, 211)))
    assert "min_floor" not in value
    assert answer.updates[0].scope == Scope()


def test_marina_n6_pets_month_exceptions_in_one_answer_and_nightly_fee():
    answer = parse_answer(question("pets"), PETS)
    pets = [update for update in answer.updates if update.characteristic_id == "pets"]
    assert {update.scope.month for update in pets} == set(range(1, 13))
    assert all(update.value["available"] is (update.scope.month not in {7, 8}) for update in pets)
    assert all(update.value["fee_rub_night"] == 500 for update in pets)
    assert all(update.value["species"] == ["dog"] and update.value["excluded_species"] == ["cat"] for update in pets)
    assert not answer.needs_clarification
    assert not answer.unknown_fields
    assert all(update.quote == PETS for update in pets)


def test_marina_n6_explicit_nightly_fee_does_not_change_unit():
    answer = parse_answer(question("pets"), "Собак принимаем. Доплата 1000 рублей за ночь.")
    assert merged(answer, "pets")["fee_rub_night"] == 1000


def test_marina_original_group_statement_is_substantive_and_confirmed(api):
    repo, _, _ = api
    _, answer = confirm_text(repo, "room_orientation", ROOMS)
    fact = repo.find("facts", characteristic_id="room_orientation")[0]
    assert fact["status"] == "confirmed" and fact["confidence"] == pytest.approx(.85)
    assert fact["value"]["available"] is None
    assert len(fact["value"]["groups"]) == 2
    assert fact["value"]["absent_floors"] == [1]
    assert fact["evidence"][0]["quote"] == ROOMS
    assert answer.confirmed


@pytest.mark.parametrize("cid,value", [
    ("pets", {"available": None, "species": ["dog"], "max_weight_kg": 5, "fee_rub_night": 500, "excluded_species": ["cat"]}),
    ("room_orientation", {"available": None, "groups": [{"rooms": ["201", "202"], "balcony": True, "sea_view": True}, {"rooms": ["211"], "balcony": False, "courtyard": True}], "absent_floors": [1]}),
    ("soundproofing", {"available": None, "windows": "double", "air_conditioning": True}),
])
@pytest.mark.parametrize("synthetic", [False, True])
def test_registry_retains_real_partial_and_does_not_confirm_demo(cid, value, synthetic):
    repo = MemoryRepo()
    at = now()
    claim = Claim(hotel_id="test-hotel", characteristic_id=cid, value=value,
                  source="hotelier_simulator" if synthetic else "hotelier", method="hotelier_answer@1",
                  evidence=[Evidence(kind="simulation" if synthetic else "hotelier", quote="Дословный ответ", label="Ответ", source_ids=["hotelier_simulator" if synthetic else "hotelier"])],
                  confidence=.85, observed_at=at, valid_until=at + timedelta(days=30), synthetic=synthetic)
    facts, _ = resolve_claims("test-hotel", [claim], [CharacteristicSpec.model_validate(repo.get("characteristics", cid))], [], at=at)
    assert facts[0].status == ("computed" if synthetic else "confirmed")
    assert facts[0].confidence == pytest.approx(.85)
    assert facts[0].synthetic is synthetic
    if cid == "pets":
        assert facts[0].value["available"] is True
        assert claim.value["available"] is None  # immutable input is preserved


def test_igor_r2_17_route_answer_is_accepted_without_invented_distance(api):
    repo, _, _ = api
    _, answer = confirm_text(repo, "accessible_route", ROUTE)
    value = merged(answer, "accessible_route")
    assert value == {"available": True, "has_steps": False, "wheelchair_suitable": True, "entrance_ramp": True}
    fact = repo.find("facts", characteristic_id="accessible_route")[0]
    assert fact["status"] == "confirmed" and not fact["synthetic"]
    assert "distance_m" not in value


@pytest.mark.parametrize("statement,expected", [(True, True), (False, False)])
@pytest.mark.parametrize("response", ["Да", "Нет"])
def test_yes_affirms_single_negative_or_positive_hypothesis(statement, expected, response):
    q = question("quiet_night")
    q.hypothesis = "Ночью тихо." if statement else "Ночью шумно."
    q.hypothesis_update = FieldUpdate(characteristic_id="quiet_night", scope=Scope(facade="W"), value={"available": statement}, quote=q.hypothesis)
    answer = parse_answer(q, response)
    assert answer.updates[0].value == {"available": expected if response == "Да" else not expected}
    assert answer.updates[0].scope.facade == "W"
    assert answer.updates[0].quote == response


def test_yes_does_not_inherit_guest_requested_scope():
    q = question("quiet_night")
    q.requested_scopes = [Scope(facade="W", floor_min=5)]
    answer = parse_answer(q, "Да")
    assert not answer.updates
    assert answer.needs_clarification


def test_revision_keeps_original_question_and_other_pending_question(api):
    repo, client, auth = api
    q, old = confirm_text(repo, "room_orientation", ROOMS)
    unrelated = create_question("test-hotel", "soundproofing", repo)
    count = len(repo.all("questions"))
    response = client.post(f"/api/answers/{old.id}/revise", headers=auth, json={})
    assert response.status_code == 200, response.text
    assert response.json()["id"] == q.id
    assert len(repo.all("questions")) == count
    assert repo.get("questions", unrelated.id)["status"] == "pending"
    # Retrying the old confirm cannot close the newly reopened question.
    client.post(f"/api/answers/{old.id}/confirm", headers=auth, json={})
    assert repo.get("questions", q.id)["status"] == "pending"
    preview = client.post(f"/api/questions/{q.id}/answer", headers=auth, json={"text": "Номера 201–212 на море, с балконом."})
    assert preview.status_code == 200, preview.text
    accepted = client.post(f'/api/answers/{preview.json()["id"]}/confirm', headers=auth, json={})
    assert accepted.status_code == 200, accepted.text
    assert repo.get("questions", unrelated.id)["status"] == "pending"
    new_claim = repo.get("claims", accepted.json()["created_claim_ids"][0])
    assert set(old.created_claim_ids) <= set(new_claim["supersedes_ids"])
    assert len(repo.all("answers")) == 2
    assert repo.get("answers", old.id)["raw_text"] == ROOMS
    current = repo.find("facts", characteristic_id="room_orientation")[0]
    assert current["value"]["rooms"] == list(map(str, range(201, 213)))


def test_unknown_revision_retracts_only_previous_knowledge_of_same_question(api):
    repo, client, auth = api
    q, old = confirm_text(repo, "pets", "Собак принимаем до 5 кг.")
    unrelated, other = confirm_text(repo, "soundproofing", "Шумоизоляции нет.")
    client.post(f"/api/answers/{old.id}/revise", headers=auth, json={})
    preview = client.post(f"/api/questions/{q.id}/answer", headers=auth, json={"text": "Не знаю"}).json()
    response = client.post(f'/api/answers/{preview["id"]}/confirm', headers=auth, json={})
    assert response.status_code == 200, response.text
    retraction = repo.get("claims", response.json()["created_claim_ids"][0])
    assert retraction["method"] == "hotelier_retraction@1"
    assert retraction["value"] is None and retraction["confidence"] == 0
    assert set(retraction["supersedes_ids"]) == set(old.created_claim_ids)
    assert not set(other.created_claim_ids) & set(retraction["supersedes_ids"])
    assert repo.find("facts", characteristic_id="pets")[0]["status"] == "unknown"
    assert repo.get("questions", unrelated.id)["status"] == "answered"


def test_first_unknown_does_not_create_claim(api):
    repo, _, _ = api
    _, answer = confirm_text(repo, "pets", "Не знаю")
    assert not answer.created_claim_ids and not repo.all("claims")


def test_wifi_ask_and_confirm_does_not_invent_speed(api):
    repo, client, auth = api
    response = client.post("/api/questions", json={"hotel_id": "test-hotel", "characteristic_id": "connectivity"})
    assert response.status_code == 200, response.text
    q = response.json()
    assert "Wi-Fi в номерах" in q["text"]
    preview = client.post(f'/api/questions/{q["id"]}/answer', headers=auth, json={"text": "Wi-Fi во всех номерах бесплатный. Скорость не измеряли."})
    assert preview.status_code == 200, preview.text
    assert merged(parse_answer(Question.model_validate(q), preview.json()["raw_text"]), "connectivity") == {"available": None, "wifi_reported": True, "wifi_in_rooms": True, "fee": "free"}
    accepted = client.post(f'/api/answers/{preview.json()["id"]}/confirm', headers=auth, json={})
    assert accepted.status_code == 200, accepted.text
    fact = Fact.model_validate(repo.find("facts", characteristic_id="connectivity")[0])
    assert fact.status == "confirmed"
    assert "download_mbps" not in fact.value
    criterion = Criterion(characteristic_id="connectivity", label="Интернет", field="download_mbps", op="gte", value=30)
    assessment = assess_hotel([fact], ParsedQuery(text="Wi-Fi", must=[criterion]))
    assert assessment.matches[0].status == "partial"


def test_calculated_correction_uses_selected_fact_context_without_guest_question(api):
    repo, client, auth = api
    at = now()
    for side, available in [("E", True), ("W", False)]:
        fact = Fact(id="fact-" + side, hotel_id="test-hotel", characteristic_id="quiet_night", scope=Scope(facade=side),
                    value={"available": available}, status="computed", confidence=.65, valid_until=at + timedelta(days=1),
                    evidence=[Evidence(kind="calculation", label="Расчёт", source_ids=["geo_engine"], quote="Синтетический тест расчёта")], supporting_claim_ids=["claim-" + side])
        repo.insert("facts", fact.model_dump(mode="json"))
    response = client.post("/api/hotelier/test-hotel/questions/quiet_night", headers=auth,
                           json={"fact_id": "fact-W", "scope": Scope(facade="W").model_dump(mode="json")})
    assert response.status_code == 200, response.text
    q = response.json()
    assert q["reason"] == "low_confidence"
    assert "ночью шумно" in q["hypothesis"].lower()
    assert q["hypothesis_update"]["scope"]["facade"] == "W"
    assert q["hypothesis_update"]["value"] == {"available": False}
    # A cabinet background refresh must not replace the selected hypothesis.
    generate_questions("test-hotel", repo)
    assert repo.get("questions", q["id"])["hypothesis_update"] == q["hypothesis_update"]
    parsed = client.post(f'/api/questions/{q["id"]}/answer', headers=auth, json={"text": "Нет"})
    assert parsed.status_code == 200, parsed.text
    assert parsed.json()["updates"][0]["scope"]["facade"] == "W"
    assert parsed.json()["updates"][0]["value"] == {"available": True}


@pytest.mark.parametrize("cid,value", [
    ("soundproofing", {"available": None, "blackout": False}),
    ("pets", {"available": None, "fee_rub_night": 0}),
    ("room_orientation", {"available": None, "balcony": False}),
])
def test_false_and_zero_are_substantive_not_missing(cid, value):
    from backend.agents.hotelier_policy import substantive_value

    spec = MemoryRepo().get("characteristics", cid)
    assert substantive_value(value, spec["value_schema"])


def test_ambiguous_pet_season_cannot_become_year_round_permission():
    from backend.agents.hotelier_policy import normalize_value

    answer = parse_answer(question("pets"), "Летом собак до 5 кг принимаем.")
    value = merged(answer, "pets")
    spec = MemoryRepo().get("characteristics", "pets")
    assert normalize_value(value, spec["value_schema"])["available"] is None
    assert answer.unknown_fields == ["pets.available"]
    assert "одном ответе" in answer.needs_clarification


def test_reopened_question_missing_source_is_not_silently_recreated(api):
    repo, client, auth = api
    q, old = confirm_text(repo, "pets", "Собак принимаем.")
    del repo.tables["questions"][q.id]
    response = client.post(f"/api/answers/{old.id}/revise", headers=auth, json={})
    assert response.status_code == 404
    assert not repo.all("questions")


def test_wifi_directions_and_units_are_not_swapped_or_invented():
    answer = parse_answer(question("connectivity"), "Wi-Fi в номерах без доплаты. Загрузка 80 Мбит/с и отдача 20 Мбит/с. Пинг 15 мс.")
    assert merged(answer, "connectivity") == {"available": None, "wifi_reported": True, "wifi_in_rooms": True, "fee": "free", "download_mbps": 80, "upload_mbps": 20, "latency_ms": 15}
    answer = parse_answer(question("connectivity"), "Wi-Fi в номерах платный. Скорость 100.")
    assert "download_mbps" not in merged(answer, "connectivity")
    assert merged(answer, "connectivity")["fee"] == "paid"


def test_pet_month_range_can_cross_new_year_without_monthly_manual_answers():
    answer = parse_answer(question("pets"), "С декабря по февраль собак принимаем.")
    assert {update.scope.month for update in answer.updates} == {12, 1, 2}
    assert all(update.value["available"] is True for update in answer.updates)


def test_reopening_original_question_does_not_revive_an_old_alternate_preview(api):
    repo, client, auth = api
    q = create_question("test-hotel", "pets", repo)
    first = parse_answer(q, "Собак принимаем до 5 кг.")
    stale = parse_answer(q, "Собак принимаем до 30 кг.")
    for answer in (first, stale):
        repo.insert("answers", answer.model_dump(mode="json"))
    confirm_answer(first.id, repo, principal=DEMO_ADMIN)
    assert client.post(f"/api/answers/{first.id}/revise", headers=auth, json={}).status_code == 200
    response = client.post(f"/api/answers/{stale.id}/confirm", headers=auth, json={})
    assert response.status_code == 409
    assert "прежнему" in response.json()["detail"]
    assert repo.find("facts", characteristic_id="pets")[0]["value"]["max_weight_kg"] == 5
    assert repo.get("questions", q.id)["status"] == "pending"


def test_explicit_false_unknown_period_flag_does_not_block_allowed_species():
    from backend.agents.hotelier_policy import normalize_value

    schema = MemoryRepo().get("characteristics", "pets")["value_schema"]
    value = {"available": None, "species": ["dog"], "permission_period_unknown": False}
    assert normalize_value(value, schema)["available"] is True
    assert value["available"] is None


@pytest.mark.parametrize("schema", [
    None, [], "bad schema", {"x-hotelier": []}, {"x-hotelier": "bad policy"},
    {"x-hotelier": {"availability_from": []}},
    {"x-hotelier": {"availability_from": {"field": ["species"], "value": True}}},
    {"x-hotelier": {"availability_from": {"field": "species", "value": True, "unless": "bad list"}}},
    {"x-hotelier": {"availability_from": {"field": "species", "value": True, "unless": [[]]}}},
    {"x-hotelier": {"substantive_fields": "species"}},
    {"x-hotelier": {"substantive_fields": [["species"]]}},
])
def test_malformed_persisted_policy_is_not_a_materialization_exception(schema):
    from backend.agents.hotelier_policy import normalize_value, substantive_value

    value = {"available": None, "species": ["dog"]}
    assert normalize_value(value, schema) == value
    assert not substantive_value(value, schema)


def test_legacy_pending_question_hydrates_and_confirms_same_negative_hypothesis(api):
    repo, client, auth = api
    fact = Fact(id="legacy-negative", hotel_id="test-hotel", characteristic_id="quiet_night", scope=Scope(facade="W"),
                value={"available": False}, status="computed", confidence=.65, valid_until=now() + timedelta(days=1))
    repo.insert("facts", fact.model_dump(mode="json"))
    legacy = question("quiet_night").model_dump(mode="json")
    legacy.pop("hypothesis_update")
    legacy["hypothesis"] = "Старый контекст"
    repo.insert("questions", legacy)
    cabinet = client.get("/api/hotelier/test-hotel", headers=auth).json()
    hydrated = next(item for item in cabinet["questions"] if item["id"] == legacy["id"])
    assert hydrated["hypothesis_update"]["value"] == {"available": False}
    assert hydrated == repo.get("questions", legacy["id"])
    response = client.post(f'/api/questions/{legacy["id"]}/answer', headers=auth, json={"text": "Да"})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["updates"][0]["value"] == {"available": False}
    # An ordinary background cabinet refresh retains the displayed proposition.
    client.get("/api/hotelier/test-hotel", headers=auth)
    response = client.post(f'/api/answers/{preview["id"]}/confirm', headers=auth, json={})
    assert response.status_code == 200, response.text
    claim = repo.get("claims", response.json()["created_claim_ids"][0])
    assert claim["value"] == preview["updates"][0]["value"]
    assert claim["scope"] == preview["updates"][0]["scope"]
    assert claim["evidence"][0]["quote"] == "Да"
    assert claim["evidence"][0]["inputs"]["hypothesis_update"] == hydrated["hypothesis_update"]


@pytest.mark.parametrize("changed", ["hypothesis", "evidence_refs", "requested_scopes"])
def test_preview_context_change_requires_new_preview_even_when_boolean_value_matches(api, changed):
    repo, client, auth = api
    q = question("quiet_night")
    q.hypothesis = "Ночью шумно."
    q.hypothesis_update = FieldUpdate(characteristic_id="quiet_night", scope=Scope(facade="W"), value={"available": False}, quote=q.hypothesis)
    repo.insert("questions", q.model_dump(mode="json"))
    response = client.post(f"/api/questions/{q.id}/answer", headers=auth, json={"text": "Да"})
    assert response.status_code == 200, response.text
    preview = response.json()
    current = repo.get("questions", q.id)
    current[changed] = {"hypothesis": "Ночью шумно по другому источнику.", "evidence_refs": ["another-source"],
                        "requested_scopes": [Scope(facade="E").model_dump(mode="json")]}[changed]
    repo.upsert("questions", current)
    response = client.post(f'/api/answers/{preview["id"]}/confirm', headers=auth, json={})
    assert response.status_code == 409
    assert "Контекст" in response.json()["detail"]
    assert not repo.all("claims")
    assert repo.get("questions", q.id)["status"] == "pending"


@pytest.mark.parametrize("synthetic", [False, True])
@pytest.mark.parametrize("contradiction", [False, True])
def test_route_null_observation_does_not_vote_against_boolean_unless_common_field_conflicts(synthetic, contradiction):
    repo = MemoryRepo()
    at = now()
    geo_value = {"available": None, "has_steps": contradiction, "distance_m": 196}
    geo = Claim(hotel_id="test-hotel", characteristic_id="accessible_route", value=geo_value,
                source="geo_engine", method="synthetic_known_answer", confidence=.65,
                observed_at=at, valid_until=at + timedelta(days=1),
                evidence=[Evidence(kind="calculation", label="Test-copy route", source_ids=["geo_engine"], quote="Test-copy mapped steps")])
    partner = Claim(hotel_id="test-hotel", characteristic_id="accessible_route",
                    value={"available": True, "has_steps": False, "wheelchair_suitable": True, "entrance_ramp": True},
                    source="hotelier_simulator" if synthetic else "hotelier", method="hotelier_answer@1", confidence=.85,
                    observed_at=at, valid_until=at + timedelta(days=1), synthetic=synthetic,
                    evidence=[Evidence(kind="simulation" if synthetic else "hotelier", label="Test-copy answer",
                                       source_ids=["hotelier_simulator" if synthetic else "hotelier"], quote=ROUTE)])
    specs = [CharacteristicSpec.model_validate(repo.get("characteristics", "accessible_route"))]
    facts, conflicts = resolve_claims("test-hotel", [geo, partner], specs, [], at=at)
    if contradiction:
        assert any(conflict.status == "open" and set(conflict.claim_ids) == {geo.id, partner.id} for conflict in conflicts)
        assert any(fact.status == "disputed" for fact in facts)
    else:
        assert not conflicts
        actual = next(fact for fact in facts if partner.id in fact.supporting_claim_ids)
        assert actual.status == ("computed" if synthetic else "confirmed")
        assert actual.confidence == pytest.approx(.85)
        assert actual.value == partner.value and "distance_m" not in actual.value
        partial = next(fact for fact in facts if geo.id in fact.supporting_claim_ids)
        assert partial.value == geo_value and partial.confidence == .65
        assert partial.supporting_claim_ids == [geo.id] and partial.evidence == geo.evidence
        assert not partial.synthetic
        criterion = Criterion(characteristic_id="accessible_route", label="Коляска", field="available", value=True)
        assessment = assess_hotel(facts, ParsedQuery(text="коляска", must=[criterion]), specs)
        assert assessment.matches[0].status == "yes"


def test_wifi_partial_hotel_reply_coexists_with_osm_presence_without_borrowing_confidence():
    repo = MemoryRepo()
    at = now()
    specs = [CharacteristicSpec.model_validate(repo.get("characteristics", "connectivity"))]
    observations = []
    for source, confidence, value in [
        ("osm", .55, {"available": None, "wifi_reported": True, "kind": "map", "measurement": False}),
        ("hotelier", .85, {"available": None, "wifi_reported": True, "wifi_in_rooms": True, "fee": "free", "kind": "operator"}),
    ]:
        observations.append(Claim(hotel_id="test-hotel", characteristic_id="connectivity", value=value,
                                  source=source, method="test_copy", confidence=confidence,
                                  observed_at=at, valid_until=at + timedelta(days=1),
                                  evidence=[Evidence(kind="hotelier" if source == "hotelier" else "open_data", label="Test copy",
                                                     source_ids=[source], quote="Wi-Fi во всех номерах бесплатный. Скорость не измеряли.")]))
    facts, conflicts = resolve_claims("test-hotel", observations, specs, [], at=at)
    assert not conflicts
    actual = next(fact for fact in facts if fact.supporting_claim_ids == [observations[1].id])
    assert actual.status == "confirmed" and actual.confidence == pytest.approx(.85)
    assert actual.value == observations[1].value and "download_mbps" not in actual.value
    mapped = next(fact for fact in facts if fact.supporting_claim_ids == [observations[0].id])
    assert mapped.status == "computed" and mapped.confidence == .55
    assert mapped.value == observations[0].value and "wifi_in_rooms" not in mapped.value
    criterion = Criterion(characteristic_id="connectivity", label="Работа", field="download_mbps", op="gte", value=30)
    result = assess_hotel(facts, ParsedQuery(text="Wi-Fi", must=[criterion]), specs).matches[0]
    assert result.status == "partial" and not result.condition_met


def test_raw_osm_wifi_without_availability_and_no_fee_agrees_with_operator_free_wifi():
    repo = MemoryRepo()
    at = now()
    specs = [CharacteristicSpec.model_validate(repo.get("characteristics", "connectivity"))]
    observations = []
    for source, confidence, value in [
        ("osm", .55, {"wifi_reported": True, "internet_access": "wlan", "fee": "no", "download_mbps": None, "measurement": False}),
        ("hotelier", .85, {"available": None, "wifi_reported": True, "wifi_in_rooms": True, "fee": "free"}),
    ]:
        observations.append(Claim(hotel_id="test-hotel", characteristic_id="connectivity", value=value,
                                  source=source, method="test_copy", confidence=confidence, observed_at=at, valid_until=at + timedelta(days=1),
                                  evidence=[Evidence(kind="hotelier" if source == "hotelier" else "open_data", label="Test copy", source_ids=[source], quote="Test-copy Wi-Fi")]))
    facts, conflicts = resolve_claims("test-hotel", observations, specs, [], at=at)
    assert not conflicts
    assert len(facts) == 2
    assert next(fact for fact in facts if fact.status == "confirmed").supporting_claim_ids == [observations[1].id]
    assert next(fact for fact in facts if fact.status == "computed").value["fee"] == "no"


@pytest.mark.parametrize("hotel_speed", [None, 100])
def test_ookla_area_and_room_wifi_stay_in_separate_domains_with_independent_evidence(hotel_speed):
    repo = MemoryRepo()
    at = now()
    specs = [CharacteristicSpec.model_validate(repo.get("characteristics", "connectivity"))]
    claims = []
    for kind in ("fixed", "mobile"):
        claims.append(Claim(hotel_id="test-hotel", characteristic_id="connectivity",
                            value={"download_mbps": 80, "kind": kind, "quarter": "2026Q3", "tests": 50},
                            source="ookla", method="area_tile", confidence=.55, observed_at=at, valid_until=at + timedelta(days=1),
                            evidence=[Evidence(kind="open_data", label="Test-copy area sample", source_ids=["ookla"], quote="Area average, not hotel Wi-Fi")]))
    hotel = Claim(hotel_id="test-hotel", characteristic_id="connectivity",
                  value={"available": None, "wifi_in_rooms": True, "wifi_reported": True, "download_mbps": hotel_speed},
                  source="hotelier", method="hotelier_answer@1", confidence=.85, observed_at=at, valid_until=at + timedelta(days=1),
                  evidence=[Evidence(kind="hotelier", label="Test-copy room Wi-Fi", source_ids=["hotelier"], quote="Room Wi-Fi test-copy statement")])
    claims.append(hotel)
    facts, conflicts = resolve_claims("test-hotel", claims, specs, [], at=at)
    assert not conflicts and len(facts) == 3
    onsite = next(fact for fact in facts if fact.supporting_claim_ids == [hotel.id])
    assert onsite.status == "confirmed" and onsite.confidence == pytest.approx(.85)
    assert onsite.value == hotel.value
    for area in claims[:2]:
        observed = next(fact for fact in facts if fact.supporting_claim_ids == [area.id])
        assert observed.status == "computed" and observed.confidence == .55
        assert observed.evidence == area.evidence and observed.value == area.value
        assert "wifi_in_rooms" not in observed.value
    assert {fact.value.get("kind") for fact in facts if fact.status == "computed"} == {"fixed", "mobile"}
