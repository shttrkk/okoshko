"""Synthetic guest requests remain separate through the local passport lifecycle."""
from datetime import timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.agents.freshness import notify_passports
from backend.api.passports import create_passport, passport_action, router
from backend.contracts import (
    Criterion,
    Evidence,
    Fact,
    ParsedQuery,
    PassportAction,
    PassportCreate,
    PassportItem,
    RoomRequest,
    RoomScope,
    Scope,
    now,
)
from backend.db.repository import get_repo
from backend.security import DEMO_ADMIN, Principal, issue_token
from backend.tests.test_hotelier import MemoryRepo


def criterion(cid="quiet_night", label="Тишина", **kwargs):
    return Criterion(characteristic_id=cid, label=label, **kwargs)


def room(label, *, side=None, floor_min=None, floor_max=None, must=None, nice=None, **kwargs):
    return RoomRequest(label=label, scope=RoomScope(facade=side, floor_min=floor_min, floor_max=floor_max),
                       must=must or [], nice=nice or [], **kwargs)


def insert_fact(repo, cid="quiet_night", *, side=None, low=None, high=None, value=None,
                status="computed", valid_until=None, identifier=None):
    if value is None:
        value = {"available": True, **({"months": [9]} if cid == "sunset_view" else {})}
    fact = Fact(id=identifier or f"fixture-{cid}-{side}-{low}-{high}", hotel_id="test-hotel", characteristic_id=cid,
                scope=Scope(facade=side, floor_min=low, floor_max=high),
                value=value, status=status, confidence=.85,
                supporting_claim_ids=["fixture-claim"], synthetic=True,
                valid_until=valid_until or now() + timedelta(days=120),
                evidence=[Evidence(id=f"evidence-{cid}-{side}", kind="simulation", label="Group passport fixture",
                                   source_ids=["test-fixture"], artifact="/tests/group-passport-fixture.json")])
    repo.insert("facts", fact.model_dump(mode="json"))
    return fact


def create(repo, rooms, *, must=None, nice=None, items=None, **kwargs):
    parsed = ParsedQuery(text="Синтетический запрос нескольких комнат", rooms=rooms, must=must or [], nice=nice or [], **kwargs)
    passport = create_passport(PassportCreate(hotel_id="test-hotel", parsed=parsed, items=items or []), repo)
    return parsed, passport


def confirm(repo, passport, *, note=None):
    return passport_action(passport.id, PassportAction(action="confirm", room_number=note), repo, principal=DEMO_ADMIN)


def room_items(passport, index):
    return [item for item in passport.items if item.room_index == index]


def test_different_facades_are_compatible_across_guests_and_common_items_exist_once():
    repo = MemoryRepo()
    west = insert_fact(repo, "sunset_view", side="W", low=4, high=6)
    east = insert_fact(repo, side="E", low=1, high=3)
    route = insert_fact(repo, "beach_walk", value={"available": True, "minutes": 7})
    parsed, passport = create(repo, [
        room("Романтики", side="W", must=[criterion("sunset_view", "Закат")]),
        room("Родители", side="E", must=[criterion()])],
        must=[criterion("beach_walk", "До пляжа 10 минут", field="minutes", op="lte", value=10)])
    assert passport.status == "sent" and len(passport.items) == 3
    assert passport.room_requests == parsed.rooms
    common = [item for item in passport.items if item.room_index is None]
    assert len(common) == 1 and common[0].fact_ids == [route.id]
    assert room_items(passport, 0)[0].room_label == "Романтики"
    assert room_items(passport, 0)[0].fact_ids == [west.id]
    assert room_items(passport, 1)[0].fact_ids == [east.id]
    updated = confirm(repo, passport, note="Размещение обсудим")
    assert updated.status == "confirmed"
    assert [(item.room_index, item.scope.facade) for item in updated.items] == [(None, None), (0, "W"), (1, "E")]
    assert all(item.status == "confirmed" for item in updated.items)
    assert repo.all("claims") == []
    parsed.rooms[0].label = "Changed request"
    assert updated.room_requests[0].label == "Романтики"


def test_duplicate_characteristic_ids_retain_each_guests_distinct_threshold():
    repo = MemoryRepo()
    insert_fact(repo, side="W", value={"available": True, "night_db_max": 50})
    insert_fact(repo, side="E", value={"available": True, "night_db_max": 60})
    _, passport = create(repo, [
        room("Чуткий сон", side="W", must=[criterion(field="night_db_max", op="lte", value=45)]),
        room("Обычный сон", side="E", must=[criterion(field="night_db_max", op="lte", value=65)])])
    stored = [repo.get("passport_items", item.id) for item in passport.items]
    assert [row["criteria"][0]["value"] for row in stored] == [45, 65]
    assert [row["room_index"] for row in stored] == [0, 1]
    updated = confirm(repo, passport)
    assert updated.status == "at_risk"
    assert [item.status for item in updated.items] == ["declined", "confirmed"]


def test_edited_group_mapping_uses_room_index_and_keeps_multiple_same_id_constraints():
    repo = MemoryRepo()
    insert_fact(repo, side="W", value={"available": True, "night_db_max": 40})
    insert_fact(repo, side="E", value={"available": True, "night_db_max": 60})
    rooms = [room("A", side="W", must=[criterion(field="night_db_max", op="lte", value=45),
                                           criterion(field="available", value=True)]),
             room("B", side="E", must=[criterion(field="night_db_max", op="lte", value=65)])]
    _, passport = create(repo, rooms, items=[
        PassportItem(room_index=1, characteristic_id="quiet_night", text="Мой текст B"),
        PassportItem(room_index=0, characteristic_id="quiet_night", text="Мой текст A", critical=False)])
    assert [item.text for item in passport.items] == ["Мой текст B", "Мой текст A"]
    rows = [repo.get("passport_items", item.id) for item in passport.items]
    assert [c["value"] for c in rows[0]["criteria"]] == [65]
    assert [c["value"] for c in rows[1]["criteria"]] == [45, True]
    assert confirm(repo, passport).status == "confirmed"


def test_floor_only_upper_bound_without_facade_is_a_pending_item_without_inventory_fact():
    repo = MemoryRepo()
    insert_fact(repo, "room_orientation", side="W", low=1, high=2)
    _, passport = create(repo, [room("Пожилые родители", floor_max=2)])
    [item] = passport.items
    assert item.room_index == 0 and item.scope.floor_max == 2 and item.scope.facade is None
    assert item.fact_ids == [] and "не выше 2-го" in item.text
    assert repo.get("passport_items", item.id)["scope_only"] is True
    updated = confirm(repo, passport, note="Проверяем первый этаж")
    assert updated.status == updated.items[0].status == "at_risk"
    assert updated.room_number == "Проверяем первый этаж" and updated.items[0].fact_ids == []
    assert repo.all("claims") == []


def test_incompatibility_is_reported_within_one_guest_without_marking_another_guest_alternative():
    repo = MemoryRepo()
    insert_fact(repo, "sunset_view", side="W")
    insert_fact(repo, side="E")
    _, passport = create(repo, [room("A", must=[criterion("sunset_view", "Закат"), criterion()]),
                               room("B", side="E", must=[criterion()])])
    assert passport.status == "alternative"
    assert all(item.status == "alternative" for item in room_items(passport, 0))
    assert room_items(passport, 1)[0].status == "pending"
    updated = confirm(repo, passport)
    assert updated.status == "alternative" and room_items(updated, 1)[0].status == "confirmed"


def test_optional_room_condition_compares_with_its_own_room_only():
    repo = MemoryRepo()
    insert_fact(repo, "sunset_view", side="W")
    insert_fact(repo, side="E")
    _, passport = create(repo, [room("A", side="W", must=[criterion("sunset_view", "Закат")]),
                               room("B", side="E", must=[criterion()], nice=[criterion(label="Желательно", field="available")])])
    updated = confirm(repo, passport)
    assert updated.status == "confirmed" and all(item.status == "confirmed" for item in updated.items)


def test_optional_condition_conflicts_only_with_the_critical_position_of_its_own_guest():
    repo = MemoryRepo()
    insert_fact(repo, "sunset_view", side="W", value={"available": True, "months": [9]})
    insert_fact(repo, side="E")
    _, passport = create(repo, [room("A", must=[criterion("sunset_view", "Закат")],
                                    nice=[criterion(scope=Scope(facade="E"))]),
                               room("B", side="E", must=[criterion()])])
    updated = confirm(repo, passport)
    assert updated.status == "confirmed"
    assert [item.status for item in room_items(updated, 0)] == ["confirmed", "alternative"]
    assert room_items(updated, 1)[0].status == "confirmed"


@pytest.mark.parametrize("value", [{"available": True}, {"available": True, "months": [7]},
                                    {"available": True, "month": 7},
                                    {"available": True, "month": 9, "months": [7]}])
def test_group_solar_confirmation_requires_explicit_coverage_for_the_selected_month(value):
    repo = MemoryRepo()
    insert_fact(repo, "sunset_view", side="W", value=value)
    _, passport = create(repo, [room("A", side="W", must=[criterion("sunset_view", "Закат")])])
    updated = confirm(repo, passport)
    assert updated.status == "at_risk" and updated.items[0].fact_ids == []


def test_required_no_data_conditions_are_kept_in_common_and_individual_snapshots():
    repo = MemoryRepo()
    insert_fact(repo, side="W")
    unknown = criterion("courtyard", "Окна во двор", field="available")
    parsed, passport = create(repo, [room("A", side="W", must=[criterion()], no_data_must=[unknown])],
                              no_data_must=[criterion("connecting_rooms", "Смежные комнаты")])
    assert len(passport.items) == 3 and passport.room_requests[0].no_data_must == [unknown]
    assert parsed.no_data_must[0].characteristic_id == "connecting_rooms"
    assert [(item.room_index, item.characteristic_id) for item in passport.items] == [
        (None, "connecting_rooms"), (0, "quiet_night"), (0, "courtyard")]
    updated = confirm(repo, passport)
    assert updated.status == "at_risk"
    for item in updated.items:
        if item.characteristic_id != "quiet_night":
            assert item.critical and item.status == "at_risk" and item.fact_ids == []


def test_same_position_and_known_single_room_mapping_never_imply_two_available_rooms():
    repo = MemoryRepo()
    insert_fact(repo, "room_orientation", side="W", value={"available": True, "rooms": ["501"]})
    _, passport = create(repo, [room(label, side="W", must=[criterion("room_orientation", "Запад")]) for label in ("A", "B")])
    updated = confirm(repo, passport, note="Предварительное описание размещения для двух запросов")
    assert updated.status == "confirmed" and updated.room_number.startswith("Предварительное описание")
    text = " ".join(event["text"] for event in updated.timeline)
    assert "одинаковое положение двух запросов не означает две свободные комнаты" in text
    assert "Общая заметка, не распределение комнат" in text
    assert "номер 501" not in text
    assert all(notification["channel"] == "local" and notification["synthetic"] for notification in updated.notifications)
    assert all(event["synthetic"] for event in updated.timeline) and updated.synthetic_booking
    assert repo.all("claims") == []


@pytest.mark.parametrize("state", ["missing", "unknown", "stale", "disputed", "expired", "negative", "conflict"])
def test_group_confirmation_does_not_turn_unknown_or_conflicting_evidence_into_confirmed(state):
    repo = MemoryRepo()
    if state != "missing":
        insert_fact(repo, side="W", status=state if state in {"unknown", "stale", "disputed"} else "computed",
                    valid_until=now() - timedelta(days=1) if state == "expired" else None,
                    value={"available": state != "negative"})
    if state == "conflict":
        insert_fact(repo, side="W", value={"available": False}, identifier="conflicting-negative")
    _, passport = create(repo, [room("A", side="W", must=[criterion()])])
    updated = confirm(repo, passport)
    assert updated.status == "at_risk" and updated.items[0].status != "confirmed"


def test_recheck_observes_changed_fact_even_without_trip_dates_and_preserves_other_guest():
    repo = MemoryRepo()
    west = insert_fact(repo, side="W")
    insert_fact(repo, side="E")
    _, passport = create(repo, [room("A", side="W", must=[criterion()]), room("B", side="E", must=[criterion()])])
    first = confirm(repo, passport)
    assert first.status == "confirmed"
    assert confirm(repo, passport).model_dump() == first.model_dump()
    repo.upsert("facts", west.model_copy(update={"value": {"available": False}}).model_dump(mode="json"))
    updated = confirm(repo, passport)
    assert updated.status == "at_risk"
    assert room_items(updated, 0)[0].status == "declined" and room_items(updated, 0)[0].fact_ids == []
    assert room_items(updated, 1)[0].status == "confirmed"
    assert len(updated.timeline) == len(first.timeline) + 1
    assert confirm(repo, passport).model_dump() == updated.model_dump()


def test_trip_expiry_marks_only_the_affected_room_and_survives_confirmation():
    repo = MemoryRepo()
    insert_fact(repo, side="W", valid_until=now() + timedelta(days=5))
    insert_fact(repo, side="E", valid_until=now() + timedelta(days=120))
    _, passport = create(repo, [room("A", side="W", must=[criterion()]), room("B", side="E", must=[criterion()])],
                        check_in=(now() + timedelta(days=10)).date(), check_out=(now() + timedelta(days=20)).date())
    updated = confirm(repo, passport)
    assert updated.status == "at_risk"
    assert room_items(updated, 0)[0].status == "at_risk" and "перепроверка" in room_items(updated, 0)[0].response
    assert room_items(updated, 1)[0].status == "confirmed"
    assert len([event for event in updated.timeline if event["type"] == "evidence_review"]) == 1


def test_change_notification_uses_each_guests_own_scope_and_stored_duplicate_criterion():
    repo = MemoryRepo()
    west = insert_fact(repo, "sea_view", side="W")
    east = insert_fact(repo, "sea_view", side="E")
    _, passport = create(repo, [room("A", side="W", must=[criterion("sea_view", "Море")]),
                               room("B", side="E", must=[criterion("sea_view", "Море")])])
    confirm(repo, passport)
    changed = west.model_copy(update={"value": {"available": False}})
    repo.upsert("facts", changed.model_dump(mode="json"))
    notifications = notify_passports(repo, "test-hotel", [west, east], [changed, east], "fixture-group-change")
    updated = repo.get("expectation_passports", passport.id)
    assert updated["status"] == "at_risk"
    assert [item["status"] for item in updated["items"]] == ["at_risk", "confirmed"]
    assert notifications[0]["item_ids"] == [passport.items[0].id]


def test_group_actions_keep_bearer_and_tenant_authorization():
    repo = MemoryRepo()
    insert_fact(repo, side="W")
    parsed, _ = create(repo, [room("A", side="W", must=[criterion()])])
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_repo] = lambda: repo
    client = TestClient(app)
    response = client.post("/api/passports", json=PassportCreate(hotel_id="test-hotel", parsed=parsed).model_dump(mode="json"))
    assert response.status_code == 200
    path = "/api/passports/" + response.json()["id"] + "/action"
    assert client.post(path, json={"action": "confirm"}).status_code == 401
    foreign = Principal("other", "hotelier", ("foreign-hotel",))
    with pytest.raises(HTTPException) as error:
        passport_action(response.json()["id"], PassportAction(action="confirm"), repo, principal=foreign)
    assert error.value.status_code == 403
    auth = {"Authorization": "Bearer " + issue_token(DEMO_ADMIN)}
    assert client.post(path, headers=auth, json={"action": "confirm"}).json()["status"] == "confirmed"


def test_old_single_path_still_requires_room_number_and_all_critical_scopes_share_one_room():
    repo = MemoryRepo()
    insert_fact(repo, "sunset_view", side="W")
    insert_fact(repo, side="E")
    parsed = ParsedQuery(text="Один номер", must=[criterion("sunset_view", "Закат"), criterion()])
    passport = create_passport(PassportCreate(hotel_id="test-hotel", parsed=parsed), repo)
    assert passport.status == "alternative" and passport.room_requests == []
    with pytest.raises(HTTPException) as error:
        confirm(repo, passport)
    assert error.value.status_code == 422
    assert confirm(repo, passport, note="512").status == "alternative"


def test_group_item_room_indices_and_scope_conflicts_cannot_be_silently_reassigned():
    repo = MemoryRepo()
    with pytest.raises(HTTPException) as error:
        create(repo, [room("A", side="W", must=[criterion()])],
               items=[PassportItem(characteristic_id="quiet_night", text="Wrong", room_index=1)])
    assert error.value.status_code == 422
    _, passport = create(repo, [room("A", side="W", must=[criterion()]), room("B", side="E", must=[criterion()])],
                         items=[PassportItem(characteristic_id="quiet_night", text="Conflict", room_index=0, scope=Scope(facade="E"))])
    assert passport.status == "alternative" and len(passport.items) == 2
    assert room_items(confirm(repo, passport), 0)[0].status == "alternative"


def test_existing_thirty_item_limit_still_bounds_group_input():
    repo = MemoryRepo()
    with pytest.raises(HTTPException) as error:
        create(repo, [room("A", must=[criterion(label=str(i)) for i in range(20)]),
                      room("B", must=[criterion(label=str(i)) for i in range(20)])])
    assert error.value.status_code == 422 and "30" in error.value.detail


@pytest.mark.parametrize("global_scope", [{"facade": "W"}, {"min_floor": 5}])
def test_global_position_restriction_is_not_erased_by_personal_scope(global_scope):
    repo = MemoryRepo()
    insert_fact(repo, side="E", low=1, high=2)
    _, passport = create(repo, [room("Маме", side="E", floor_max=2, must=[criterion()]),
                               room("Нам", must=[criterion()])], **global_scope)
    assert passport.status == "alternative"
    assert room_items(passport, 0)[0].status == "alternative"
