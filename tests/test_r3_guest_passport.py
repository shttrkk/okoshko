"""R3 guest lifecycle contracts; all observations are synthetic test data."""
from datetime import timedelta

import pytest

from backend.api.passports import create_passport, passport_action
from backend.contracts import (
    Criterion,
    Fact,
    ParsedQuery,
    PassportAction,
    PassportCreate,
    PassportItem,
    Scope,
    now,
)
from backend.security import DEMO_ADMIN
from backend.tests.test_hotelier import MemoryRepo


def observation(repo, cid="quiet_night", *, valid_until=None, value=None, status="computed"):
    fact = Fact(hotel_id="test-hotel", characteristic_id=cid,
                scope=Scope(type="floor_range", facade="W", floor_min=5, floor_max=5),
                value=value or {"available": True}, status=status, confidence=.8,
                valid_until=valid_until or now() + timedelta(days=30), synthetic=True)
    repo.insert("facts", fact.model_dump(mode="json"))
    return fact


def create(repo, criteria=None):
    criteria = criteria or [Criterion(characteristic_id="quiet_night", label="Тихий номер")]
    return create_passport(PassportCreate(hotel_id="test-hotel", guest_name="Гость",
        parsed=ParsedQuery(text="Мои пожелания", must=criteria,
                          check_in=(now() + timedelta(days=300)).date(),
                          check_out=(now() + timedelta(days=307)).date()),
        items=[PassportItem(characteristic_id=criterion.characteristic_id, text=criterion.label,
                            scope=Scope(type="floor_range", facade="W", floor_min=5, floor_max=5))
               for criterion in criteria]), repo)


def confirm(repo, passport, text="Номер 512 на западной стороне, ночью тихо."):
    return passport_action(passport.id, PassportAction(action="confirm", room_number="512", text=text),
                           repo, principal=DEMO_ADMIN)


def test_multiple_future_deadlines_have_one_neutral_review_preserving_scope_and_comment():
    repo = MemoryRepo()
    facts = [observation(repo), observation(repo, "beach_walk", value={"available": True, "minutes": 7},
                                           valid_until=now() + timedelta(days=60))]
    before = repo.all("facts")
    passport = create(repo, [Criterion(characteristic_id="quiet_night", label="Тихий номер"),
                            Criterion(characteristic_id="beach_walk", label="Пляж до 10 минут", field="minutes", op="lte", value=10)])
    assert passport.status == "sent"
    assert all(item.status == "pending" for item in passport.items)
    updated = confirm(repo, passport)
    assert updated.status == "confirmed" and updated.room_number == "512"
    assert all(item.status == "confirmed" for item in updated.items)
    assert all(item.scope.facade == "W" and item.scope.floor_min == item.scope.floor_max == 5 for item in updated.items)
    reviews = [event for event in updated.timeline if event["type"] == "evidence_review"]
    assert len(reviews) == 1 and "Перепроверим ближе к поездке" in reviews[0]["text"]
    assert reviews[0]["review_by"] == facts[0].valid_until.date().isoformat()
    assert not any("Перепроверим" in (item.response or "") for item in updated.items)
    assert not any(event["type"] == "evidence_review" for event in updated.notifications)
    assert updated.timeline[-1]["hotel_comment"] == "Номер 512 на западной стороне, ночью тихо."
    assert updated.timeline[-1]["reported_room_number"] == "512"
    assert "Проверено по карте" in updated.items[1].response
    assert repo.all("facts") == before and repo.all("claims") == []
    assert confirm(repo, passport).model_dump() == updated.model_dump()


@pytest.mark.parametrize("status,deadline", [("computed", -1), ("stale", 30), ("disputed", 30)])
def test_expired_stale_or_disputed_observations_cannot_be_confirmed(status, deadline):
    repo = MemoryRepo()
    observation(repo, status=status, valid_until=now() + timedelta(days=deadline))
    updated = confirm(repo, create(repo))
    assert updated.status == updated.items[0].status == "at_risk"
    assert updated.items[0].fact_ids == [] and updated.room_number is None
    assert updated.timeline[-1]["hotel_comment"]
    assert updated.timeline[-1]["reported_room_number"] == "512"


def test_a_reported_wifi_connection_does_not_confirm_a_speed_threshold():
    repo = MemoryRepo()
    observation(repo, "connectivity", value={"available": True, "wifi_reported": True})
    passport = create(repo, [Criterion(characteristic_id="connectivity", label="Интернет от 30 Мбит/с",
                                      field="download_mbps", op="gte", value=30)])
    updated = confirm(repo, passport, "Wi-Fi есть, скорость не замеряли.")
    assert updated.status == "at_risk" and updated.items[0].status == "at_risk"
    assert updated.items[0].fact_ids == []
    assert updated.timeline[-1]["hotel_comment"] == "Wi-Fi есть, скорость не замеряли."


def test_same_confirmation_rechecks_a_real_value_change_without_renewing_evidence():
    repo = MemoryRepo()
    fact = observation(repo)
    passport = create(repo)
    first = confirm(repo, passport)
    assert first.status == "confirmed"
    repo.upsert("facts", fact.model_copy(update={"value": {"available": False}}).model_dump(mode="json"))
    changed = confirm(repo, passport)
    assert changed.status == "at_risk" and changed.items[0].status == "declined"
    assert changed.items[0].fact_ids == [] and changed.room_number is None
    assert len(changed.timeline) == len(first.timeline) + 1
    assert confirm(repo, passport).model_dump() == changed.model_dump()


def test_unobserved_nearby_places_are_not_described_as_checked_on_the_map():
    repo = MemoryRepo()
    passport = create(repo, [Criterion(characteristic_id="family_nearby", label="Детская площадка")])
    updated = confirm(repo, passport)
    assert updated.status == "at_risk" and updated.items[0].status == "at_risk"
    assert "Проверено по карте" not in updated.items[0].response
