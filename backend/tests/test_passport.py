"""Synthetic scope and lifecycle checks for local expectation passports."""
from __future__ import annotations

import unittest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.api.hotelier import router as hotelier_router
from backend.api.passports import create_passport, passport_action, router, scopes_compatible
from backend.contracts import (
    Criterion,
    Fact,
    ParsedQuery,
    PassportAction,
    PassportCreate,
    PassportItem,
    Scope,
)
from backend.db.repository import get_repo
from backend.tests.test_hotelier import MemoryRepo


class PassportTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepo()
        self.app = FastAPI()
        self.app.include_router(hotelier_router)
        self.app.include_router(router)
        self.app.dependency_overrides[get_repo] = lambda: self.repo
        self.client = TestClient(self.app)
        result = self.client.post("/api/hotelier/login", json={"username": "hotelier", "password": "hotelier123"})
        self.auth = {"Authorization": "Bearer " + result.json()["token"]}

    def fact(self, cid, *, facade=None, floor_min=None, floor_max=None, value=None, status="computed", evidence=None):
        scope = Scope(type="floor_range" if facade and (floor_min or floor_max) else "facade" if facade else "hotel", facade=facade, floor_min=floor_min, floor_max=floor_max)
        fact = Fact(hotel_id="test-hotel", characteristic_id=cid, scope=scope, value=value or {"available": True},
                    status=status, confidence=.8, synthetic=True, evidence=evidence or [])
        self.repo.insert("facts", fact.model_dump(mode="json"))
        return fact

    def create(self, items=None, parsed=None):
        return create_passport(PassportCreate(hotel_id="test-hotel", guest_name="Маша", items=items or [], parsed=parsed), self.repo)

    def test_scope_invariants(self):
        self.assertFalse(scopes_compatible([Scope(type="facade", facade="W"), Scope(type="facade", facade="E")]))
        self.assertFalse(scopes_compatible([Scope(floor_min=5), Scope(floor_max=3)]))
        self.assertTrue(scopes_compatible([Scope(floor_min=5), Scope(floor_max=8)]))

    def test_parsed_request_prefills_criticality_dates_and_fact_references(self):
        fact = self.fact("sunset_view", facade="W", floor_min=5)
        parsed = ParsedQuery(text="закат и море", check_in="2026-11-01", check_out="2026-11-07",
                             must=[Criterion(characteristic_id="sunset_view", label="Закат")],
                             nice=[Criterion(characteristic_id="sea_view", label="Море")])
        passport = self.create(parsed=parsed)
        self.assertEqual(str(passport.check_in), "2026-11-01")
        self.assertTrue(passport.items[0].critical)
        self.assertFalse(passport.items[1].critical)
        self.assertEqual(passport.items[0].fact_ids, [fact.id])
        self.assertEqual(passport.items[0].scope.facade, "W")
        self.assertEqual(passport.items[0].scope.floor_min, 5)
        self.assertTrue(passport.synthetic_booking)
        self.assertTrue(all(n["channel"] == "local" for n in passport.notifications))

    def test_confirm_incompatible_facades_offers_alternative(self):
        self.fact("sunset_view", facade="W", floor_min=5)
        self.fact("quiet_night", facade="E", floor_min=5)
        passport = self.create([PassportItem(characteristic_id="sunset_view", text="Закат"), PassportItem(characteristic_id="quiet_night", text="Тишина")])
        self.assertEqual(passport.status, "alternative")
        updated = passport_action(passport.id, PassportAction(action="confirm", room_number="512"), self.repo)
        self.assertEqual(updated.status, "alternative")
        self.assertIsNone(updated.room_number)
        self.assertTrue(all(item.status == "alternative" for item in updated.items))

    def test_confirm_incompatible_floors_offers_alternative(self):
        passport = self.create([PassportItem(characteristic_id="sunset_view", text="Высоко", scope=Scope(floor_min=5)),
                                PassportItem(characteristic_id="quiet_night", text="Низко", scope=Scope(floor_max=3))])
        updated = passport_action(passport.id, PassportAction(action="confirm", room_number="512"), self.repo)
        self.assertNotEqual(updated.status, "confirmed")

    def test_compatible_fact_alternative_is_selected(self):
        self.fact("sunset_view", facade="W", floor_min=5)
        self.fact("quiet_night", facade="E")
        west_quiet = self.fact("quiet_night", facade="W")
        passport = self.create([PassportItem(characteristic_id="sunset_view", text="Закат"), PassportItem(characteristic_id="quiet_night", text="Тишина")])
        self.assertEqual(passport.status, "sent")
        self.assertEqual(passport.items[1].fact_ids, [west_quiet.id])
        updated = passport_action(passport.id, PassportAction(action="confirm", room_number="512"), self.repo)
        self.assertEqual(updated.status, "confirmed")
        self.assertEqual(updated.room_number, "512")
        self.assertTrue(updated.synthetic_booking)

    def test_optional_conflict_does_not_prevent_critical_confirmation(self):
        passport = self.create([PassportItem(characteristic_id="sunset_view", text="Закат", scope=Scope(type="facade", facade="W")),
                                PassportItem(characteristic_id="quiet_night", text="Желательно тихо", critical=False, scope=Scope(type="facade", facade="E"))])
        updated = passport_action(passport.id, PassportAction(action="confirm", room_number="512"), self.repo)
        self.assertEqual(updated.status, "confirmed")
        self.assertNotEqual(updated.items[1].status, "confirmed")

    def test_room_number_not_in_known_room_mapping_cannot_confirm(self):
        self.fact("room_orientation", facade="W", value={"available": True, "rooms": ["501", "502"]})
        passport = self.create([PassportItem(characteristic_id="room_orientation", text="На запад")])
        updated = passport_action(passport.id, PassportAction(action="confirm", room_number="512"), self.repo)
        self.assertEqual(updated.status, "at_risk")
        self.assertIsNone(updated.room_number)

    def test_known_negative_cannot_be_confirmed(self):
        self.fact("pets", value={"available": False})
        passport = self.create([PassportItem(characteristic_id="pets", text="С собакой")])
        updated = passport_action(passport.id, PassportAction(action="confirm", room_number="512"), self.repo)
        self.assertEqual(updated.status, "at_risk")

    def test_confirm_retry_does_not_duplicate_timeline_notifications(self):
        passport = self.create([PassportItem(characteristic_id="pets", text="С собакой")])
        request = PassportAction(action="confirm", room_number="512")
        first = passport_action(passport.id, request, self.repo)
        second = passport_action(passport.id, request, self.repo)
        self.assertEqual(first.model_dump(), second.model_dump())

    def test_alternative_and_feedback_are_local_and_demo_labeled(self):
        from backend.contracts import Evidence
        self.repo.insert("sources", {"id": "hotelier_simulator", "alpha": 7, "beta": 3})
        self.fact("pets", evidence=[Evidence(kind="simulation", label="Demo", source_ids=["hotelier_simulator"], quote="С собакой можно")])
        passport = self.create([PassportItem(characteristic_id="pets", text="С собакой")])
        alternative = passport_action(passport.id, PassportAction(action="alternative", text="Номер 501 с доплатой"), self.repo)
        self.assertEqual(alternative.status, "alternative")
        request = PassportAction(action="feedback", matched=False, text="Не совпало")
        feedback = passport_action(passport.id, request, self.repo)
        self.assertEqual(self.repo.get("sources", "hotelier_simulator")["beta"], 4)
        self.assertIn("DEMO", self.repo.get("sources", "hotelier_simulator")["notes"])
        passport_action(passport.id, request, self.repo)
        self.assertEqual(self.repo.get("sources", "hotelier_simulator")["beta"], 4)
        self.assertTrue(all(notification["channel"] == "local" for notification in feedback.notifications))

    def test_public_create_get_and_authenticated_actions(self):
        response = self.client.post("/api/passports", json={"hotel_id": "test-hotel", "guest_name": "Маша", "items": [{"characteristic_id": "pets", "text": "С собакой"}]})
        self.assertEqual(response.status_code, 200, response.text)
        pid = response.json()["id"]
        self.assertEqual(self.client.get(f"/api/passports/{pid}").status_code, 200)
        self.assertEqual(self.client.post(f"/api/passports/{pid}/action", json={"action": "confirm", "room_number": "512"}).status_code, 401)
        result = self.client.post(f"/api/passports/{pid}/action", headers=self.auth, json={"action": "confirm", "room_number": "512"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["status"], "confirmed")

    def test_no_empty_passport_invalid_dates_or_foreign_facts(self):
        cases = [PassportCreate(hotel_id="test-hotel"),
                 PassportCreate(hotel_id="test-hotel", check_in="2026-11-07", check_out="2026-11-01", items=[PassportItem(characteristic_id="pets", text="Питомец")]),
                 PassportCreate(hotel_id="test-hotel", items=[PassportItem(characteristic_id="pets", text="Питомец", fact_ids=["other-hotel-fact"])])]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(HTTPException) as error:
                    create_passport(case, self.repo)
                self.assertEqual(error.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()


class EditedPassportCriteriaTests(unittest.TestCase):
    setUp = PassportTests.setUp
    fact = PassportTests.fact
    create = PassportTests.create

    def explicit(self, cid, criterion, *, critical=True, text="Моё отредактированное ожидание", parsed_text="Запрос", **parsed_fields):
        parsed = ParsedQuery(text=parsed_text, must=[criterion], **parsed_fields)
        item = PassportItem(characteristic_id=cid, text=text, critical=critical)
        passport = self.create([item], parsed=parsed)
        return passport, self.repo.get("passport_items", passport.items[0].id)

    def confirm(self, passport):
        return passport_action(passport.id, PassportAction(action="confirm", room_number="512"), self.repo)

    def test_explicit_item_retains_numeric_criterion_and_guest_edits(self):
        self.fact("beach_walk", value={"available": True, "minutes": 30})
        criterion = Criterion(characteristic_id="beach_walk", label="До пляжа 10 минут", field="minutes", op="lte", value=10, weight=3)
        passport, stored = self.explicit("beach_walk", criterion, critical=False)
        self.assertFalse(passport.items[0].critical)
        self.assertEqual(passport.items[0].text, "Моё отредактированное ожидание")
        self.assertEqual(stored["criteria"][0], criterion.model_dump(mode="json"))
        self.assertEqual(passport.items[0].fact_ids, [])
        confirmed = self.confirm(passport)
        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.items[0].status, "declined")

    def test_critical_ten_minute_walk_rejects_thirty_minute_fact(self):
        self.fact("beach_walk", value={"available": True, "minutes": 30})
        passport, _stored = self.explicit("beach_walk", Criterion(characteristic_id="beach_walk", label="До 10 минут", field="minutes", op="lte", value=10))
        confirmed = self.confirm(passport)
        self.assertEqual(confirmed.status, "at_risk")
        self.assertEqual(confirmed.items[0].status, "declined")
        self.assertIsNone(confirmed.room_number)
        # A rejected confirmation is idempotent too.
        again = self.confirm(passport)
        self.assertEqual(again.model_dump(), confirmed.model_dump())

    def test_matching_numeric_fact_confirms_and_default_field_is_catalogue_backed(self):
        fact = self.fact("beach_walk", value={"available": True, "minutes": 7})
        passport, stored = self.explicit("beach_walk", Criterion(characteristic_id="beach_walk", label="До 10 минут", op="lte", value=10))
        self.assertEqual(stored["criteria"][0]["field"], "minutes")
        self.assertEqual(passport.items[0].fact_ids, [fact.id])
        self.assertEqual(self.confirm(passport).status, "confirmed")

    def test_numeric_field_missing_does_not_fall_back_to_availability(self):
        self.fact("beach_walk", value={"available": True})
        passport, _stored = self.explicit("beach_walk", Criterion(characteristic_id="beach_walk", label="До 10 минут", field="minutes", op="lte", value=10))
        updated = self.confirm(passport)
        self.assertEqual(updated.status, "at_risk")
        self.assertEqual(updated.items[0].status, "at_risk")

    def test_merged_explicit_item_preserves_all_same_characteristic_constraints(self):
        self.fact("beach_walk", value={"available": True, "minutes": 7, "has_steps": True})
        parsed = ParsedQuery(text="До пляжа без лестниц за 10 минут", must=[
            Criterion(characteristic_id="beach_walk", label="До 10 минут", field="minutes", op="lte", value=10),
            Criterion(characteristic_id="beach_walk", label="Без лестниц", field="has_steps", value=False)])
        passport = self.create([PassportItem(characteristic_id="beach_walk", text="До пляжа удобно")], parsed=parsed)
        stored = self.repo.get("passport_items", passport.items[0].id)
        self.assertEqual(len(stored["criteria"]), 2)
        self.assertEqual(self.confirm(passport).status, "at_risk")

    def test_separate_rows_keep_per_criterion_critical_toggles(self):
        self.fact("beach_walk", value={"available": True, "minutes": 30, "has_steps": False})
        parsed = ParsedQuery(text="До пляжа до 10 минут без лестниц", must=[
            Criterion(characteristic_id="beach_walk", label="До 10 минут", field="minutes", op="lte", value=10),
            Criterion(characteristic_id="beach_walk", label="Без лестниц", field="has_steps", value=False)])
        passport = self.create([PassportItem(characteristic_id="beach_walk", text="Можно идти подольше", critical=False),
                                PassportItem(characteristic_id="beach_walk", text="Коляска, строго без лестниц", critical=True)], parsed=parsed)
        updated = self.confirm(passport)
        self.assertEqual(updated.status, "confirmed")
        self.assertEqual([item.status for item in updated.items], ["declined", "confirmed"])
        self.assertEqual([item.critical for item in updated.items], [False, True])

    def test_negative_pets_requirement_is_not_inverted(self):
        for available, expected_status in ((False, "confirmed"), (True, "at_risk")):
            with self.subTest(available=available):
                self.repo.tables["facts"] = {}
                self.fact("pets", value={"available": available})
                passport, _stored = self.explicit("pets", Criterion(characteristic_id="pets", label="Без питомцев", field="available", value=False), parsed_text="Без питомцев")
                self.assertEqual(self.confirm(passport).status, expected_status)

    def test_pet_weight_and_species_are_preserved_with_explicit_items(self):
        for value, expected_status in (({"available": True, "max_weight_kg": 10, "species": ["dog"]}, "at_risk"),
                                       ({"available": True, "max_weight_kg": 40, "species": ["cat"]}, "at_risk"),
                                       ({"available": True, "max_weight_kg": 40, "species": ["dog"]}, "confirmed"),
                                       ({"available": False, "max_weight_kg": 40, "species": ["dog"]}, "at_risk")):
            with self.subTest(value=value):
                self.repo.tables["facts"] = {}
                self.fact("pets", value=value)
                passport, stored = self.explicit("pets", Criterion(characteristic_id="pets", label="С питомцем", field="available", value=True),
                                                parsed_text="С собакой 30 кг", pet_weight_kg=30)
                self.assertEqual({c["field"] for c in stored["criteria"]}, {"available", "max_weight_kg", "species"})
                self.assertEqual(self.confirm(passport).status, expected_status)

    def test_explicit_scope_and_parsed_scope_cannot_be_silently_reconciled(self):
        self.fact("sunset_view", facade="E", floor_min=5)
        parsed = ParsedQuery(text="Запад, от пятого этажа", facade="W", min_floor=5, must=[
            Criterion(characteristic_id="sunset_view", label="Закат", field="available", value=True)])
        item = PassportItem(characteristic_id="sunset_view", text="Отредактированное ожидание", scope=Scope(type="facade", facade="E"))
        passport = self.create([item], parsed=parsed)
        self.assertEqual(passport.status, "alternative")
        self.assertEqual(self.confirm(passport).status, "alternative")

    def test_value_embedded_facade_floor_qualifiers_are_not_lost(self):
        for value in ({"available": True, "facade": "E", "min_floor": 5},
                      {"available": True, "facade": "W", "min_floor": 1, "max_floor": 3}):
            with self.subTest(value=value):
                self.repo.tables["facts"] = {}
                self.fact("sunset_view", value=value)
                passport, _stored = self.explicit("sunset_view", Criterion(characteristic_id="sunset_view", label="Закат", field="available", value=True),
                                                parsed_text="Запад, от пятого этажа", facade="W", min_floor=5)
                self.assertEqual(self.confirm(passport).status, "alternative")

    def test_old_stored_single_criterion_remains_enforced(self):
        self.fact("beach_walk", value={"available": True, "minutes": 30})
        criterion = Criterion(characteristic_id="beach_walk", label="До 10 минут", field="minutes", op="lte", value=10)
        passport, stored = self.explicit("beach_walk", criterion)
        stored.pop("criteria")
        stored["criterion"] = criterion.model_dump(mode="json")
        self.repo.upsert("passport_items", stored)
        self.assertEqual(self.confirm(passport).status, "at_risk")


class SyntheticDialogueFixtureTests(unittest.TestCase):
    def load_fixture(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        envelope = json.loads((root / "data/seed/demo_hotelier_dialogues.json").read_text())
        profiles = json.loads((root / "data/hotelier_profiles/pets_dialogue_profiles.json").read_text())
        return envelope, profiles

    def test_completed_fixture_is_parser_generated_and_has_no_orphans(self):
        from data.hotelier_profiles.build_dialogues import validate

        envelope, profiles = self.load_fixture()
        report = validate(envelope, profiles)
        self.assertEqual(report["completed_hotels"], 7)
        self.assertEqual(report["pending_live_hotels"], 2)
        self.assertEqual(report["orphan_records"], 0)
        self.assertEqual(report["claims"], 7)

    def test_missing_message_and_real_source_substitution_are_rejected(self):
        from data.hotelier_profiles.build_dialogues import validate

        for defect in ("message", "source"):
            with self.subTest(defect=defect):
                envelope, profiles = self.load_fixture()
                if defect == "message":
                    envelope["claims"][0]["evidence"][0]["message_id"] = "missing-answer"
                else:
                    envelope["claims"][0]["synthetic"] = False
                    envelope["claims"][0]["source"] = "hotelier"
                with self.assertRaises(AssertionError):
                    validate(envelope, profiles)

    def test_pending_live_preview_and_confirm_use_the_actual_api(self):
        envelope, profiles = self.load_fixture()
        repo = MemoryRepo()
        import json
        from pathlib import Path

        hotels = {h["id"]: h for h in json.loads((Path(__file__).resolve().parents[2] / "data/seed/hotels.json").read_text())}
        pending = [q for q in envelope["questions"] if q["status"] == "pending"]
        for question in pending:
            repo.upsert("hotels", hotels[question["hotel_id"]])
            repo.insert("questions", question)
        app = FastAPI()
        app.include_router(hotelier_router)
        app.dependency_overrides[get_repo] = lambda: repo
        client = TestClient(app)
        token = client.post("/api/hotelier/login", json={"username": "hotelier", "password": "hotelier123"}).json()["token"]
        auth = {"Authorization": "Bearer " + token}
        answers = []
        for question in pending:
            result = client.post(f'/api/questions/{question["id"]}/simulate', headers=auth, json={})
            self.assertEqual(result.status_code, 200, result.text)
            preview = result.json()
            self.assertTrue(preview["synthetic"])
            self.assertFalse(preview["confirmed"])
            self.assertEqual(repo.all("claims"), [])
            expected = next(p for p in profiles["live_profiles"] if p["hotel_id"] == question["hotel_id"])
            self.assertEqual(preview["raw_text"], expected["answers"]["pets"])
            answers.append(preview)
        for answer in answers:
            result = client.post(f'/api/answers/{answer["id"]}/confirm', headers=auth, json={})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(result.json()["confirmed"])
        self.assertEqual(len(repo.all("claims")), 2)
        self.assertTrue(all(c["source"] == "hotelier_simulator" and c["synthetic"] for c in repo.all("claims")))
        pets_facts = repo.find("facts", characteristic_id="pets")
        self.assertTrue(all(f["status"] == "computed" and f["synthetic"] for f in pets_facts))


class PassportRouteConstraintTests(unittest.TestCase):
    setUp = PassportTests.setUp
    fact = PassportTests.fact
    create = PassportTests.create

    def constrained(self, value, *, no_stairs=True, no_crossings=False, field="minutes"):
        fact = self.fact("beach_walk", value=value)
        criterion = Criterion(characteristic_id="beach_walk", label="Маршрут с коляской",
                              field=field, op="lte" if field == "minutes" else "eq",
                              value=10 if field == "minutes" else True, no_stairs=no_stairs, no_crossings=no_crossings)
        parsed = ParsedQuery(text="До пляжа 10 минут без лестниц", must=[criterion])
        passport = self.create([PassportItem(characteristic_id="beach_walk", text="С коляской, без лестниц")], parsed=parsed)
        return fact, passport

    def confirm(self, passport):
        return passport_action(passport.id, PassportAction(action="confirm", room_number="512"), self.repo)

    def test_stairs_true_or_missing_cannot_be_confirmed(self):
        cases = [({"available": True, "minutes": 7, "has_steps": True}, "declined"),
                 ({"available": True, "minutes": 7}, "at_risk"),
                 ({"available": True, "minutes": 7, "has_steps": None}, "at_risk")]
        for value, item_status in cases:
            with self.subTest(value=value):
                self.repo.tables["facts"] = {}
                _fact, passport = self.constrained(value)
                stored = self.repo.get("passport_items", passport.items[0].id)
                self.assertTrue(stored["criteria"][0]["no_stairs"])
                result = self.confirm(passport)
                self.assertEqual(result.status, "at_risk")
                self.assertEqual(result.items[0].status, item_status)
                self.assertIsNone(result.room_number)

    def test_known_clear_route_confirms_but_not_if_crossings_are_unknown(self):
        for crossings, expected in (([], "confirmed"), ([{"id": "road-crossing"}], "at_risk"), (None, "at_risk")):
            with self.subTest(crossings=crossings):
                self.repo.tables["facts"] = {}
                value = {"available": True, "minutes": 7, "has_steps": False}
                if crossings is not None:
                    value["crossings"] = crossings
                _fact, passport = self.constrained(value, no_crossings=True)
                self.assertEqual(self.confirm(passport).status, expected)

    def test_route_flags_also_apply_when_primary_field_is_available(self):
        _fact, passport = self.constrained({"available": True}, field="available")
        result = self.confirm(passport)
        self.assertEqual(result.status, "at_risk")
        self.assertEqual(result.items[0].status, "at_risk")

    def test_clear_accessibility_does_not_override_failed_numeric_limit(self):
        _fact, passport = self.constrained({"available": True, "minutes": 30, "has_steps": False, "crossings": []}, no_crossings=True)
        result = self.confirm(passport)
        self.assertEqual(result.status, "at_risk")
        self.assertEqual(result.items[0].status, "declined")

    def test_known_violation_wins_over_a_missing_route_tag(self):
        from backend.api.passports import _comparison

        fact = self.fact("beach_walk", value={"available": True, "minutes": 30})
        criterion = Criterion(characteristic_id="beach_walk", label="До 10 минут без лестниц", field="minutes", op="lte", value=10, no_stairs=True)
        self.assertIs(_comparison(fact, criterion), False)

    def test_route_change_notifications_use_the_same_stored_constraints(self):
        from backend.agents.freshness import notify_passports

        for after_value in ({"available": True, "minutes": 7, "has_steps": True},
                            {"available": True, "minutes": 7}):
            with self.subTest(after_value=after_value):
                self.repo.tables["facts"] = {}
                before, passport = self.constrained({"available": True, "minutes": 7, "has_steps": False})
                self.assertEqual(self.confirm(passport).status, "confirmed")
                after = before.model_copy(update={"value": after_value})
                self.repo.upsert("facts", after.model_dump(mode="json"))
                notifications = notify_passports(self.repo, "test-hotel", [before], [after], "synthetic-route-change-" + passport.id)
                result = self.repo.get("expectation_passports", passport.id)
                self.assertEqual(result["status"], "at_risk")
                self.assertEqual(result["items"][0]["status"], "at_risk")
                self.assertTrue(any(n["passport_id"] == passport.id and n["channel"] == "local" for n in notifications))


class PassportEvidenceDatesTests(unittest.TestCase):
    def setUp(self):
        from datetime import UTC, datetime
        from unittest.mock import patch

        self.repo = MemoryRepo()
        clock = patch("backend.api.passports.now", return_value=datetime(2026, 10, 26, tzinfo=UTC))
        clock.start()
        self.addCleanup(clock.stop)
        self.fact = Fact(id="date-test-route", hotel_id="test-hotel", characteristic_id="beach_walk",
                         value={"available": True, "minutes": 7, "has_steps": False}, confidence=.85, status="computed",
                         valid_until="2027-01-24T14:00:24+00:00", synthetic=True)
        self.repo.insert("facts", self.fact.model_dump(mode="json"))

    def create(self, check_in=None, check_out=None, *, critical=True):
        scope = Scope(type="floor_range", facade="W", floor_min=5, floor_max=5)
        parsed = ParsedQuery(text="До пляжа 10 минут без лестниц", check_in=check_in, check_out=check_out,
                             must=[Criterion(characteristic_id="beach_walk", label="Пляж", field="minutes", op="lte", value=10, no_stairs=True, scope=scope)])
        return create_passport(PassportCreate(hotel_id="test-hotel", guest_name="Маша", parsed=parsed,
            items=[PassportItem(characteristic_id="beach_walk", text="До 10 минут без лестниц", scope=scope, critical=critical)]), self.repo)

    def confirm(self, passport):
        return passport_action(passport.id, PassportAction(action="confirm", room_number="512"), self.repo)

    def assert_review(self, passport):
        self.assertEqual(passport.status, "at_risk")
        self.assertEqual(passport.items[0].status, "at_risk")
        self.assertIn("перепроверка до 2027-01-24", passport.items[0].response)
        self.assertIn("не гарантируют", passport.items[0].response)
        reviews = [event for event in passport.timeline if event.get("type") == "evidence_review"]
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["review_by"], "2027-01-24")
        stored = self.repo.get("passport_items", passport.items[0].id)
        self.assertTrue(stored["criteria"][0]["no_stairs"])
        self.assertEqual(passport.items[0].scope.facade, "W")
        self.assertEqual(passport.items[0].scope.floor_min, 5)

    def test_trip_after_evidence_deadline_stays_at_risk_after_room_confirmation(self):
        before = self.repo.get("facts", self.fact.id)
        passport = self.create("2027-09-14", "2027-09-21")
        self.assert_review(passport)
        self.assertIn("2027-09-14–2027-09-21", passport.items[0].response)
        confirmed = self.confirm(passport)
        self.assert_review(confirmed)
        self.assertIsNone(confirmed.room_number)
        self.assertIn("Назначение номера не гарантирует", confirmed.timeline[-1]["text"])
        self.assertEqual(self.repo.get("facts", self.fact.id), before)
        self.assertEqual(self.repo.all("claims"), [])
        self.assertEqual(self.confirm(passport).model_dump(), confirmed.model_dump())

    def test_trip_crossing_deadline_requires_review_through_checkout(self):
        passport = self.create("2027-01-23", "2027-01-26")
        self.assert_review(passport)
        self.assert_review(self.confirm(passport))

    def test_trip_within_evidence_period_keeps_normal_confirmation(self):
        passport = self.create("2026-11-14", "2026-11-21")
        self.assertEqual(passport.status, "sent")
        self.assertFalse(any(event["type"] == "evidence_review" for event in passport.timeline))
        confirmed = self.confirm(passport)
        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.room_number, "512")

    def test_deadline_on_checkout_date_is_not_earlier_than_the_stay_end(self):
        passport = self.create("2027-01-23", "2027-01-24")
        self.assertEqual(passport.status, "sent")
        self.assertEqual(self.confirm(passport).status, "confirmed")

    def test_only_check_in_is_used_without_inventing_a_checkout(self):
        passport = self.create("2027-09-14")
        self.assert_review(passport)
        self.assertIsNone(passport.check_out)
        self.assertIn("2027-09-14", passport.items[0].response)

    def test_no_dates_preserves_behavior_without_assuming_a_year(self):
        passport = self.create()
        confirmed = self.confirm(passport)
        self.assertIsNone(confirmed.check_in)
        self.assertIsNone(confirmed.check_out)
        self.assertEqual(confirmed.status, "confirmed")
        self.assertFalse(any(event["type"] == "evidence_review" for event in confirmed.timeline))
        self.assertNotIn("2027", confirmed.items[0].response)

    def test_optional_expiring_property_is_transparent_without_blocking_room(self):
        passport = self.create("2027-09-14", "2027-09-21", critical=False)
        self.assertEqual(passport.status, "sent")
        self.assertEqual(passport.items[0].status, "at_risk")
        confirmed = self.confirm(passport)
        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.room_number, "512")
        self.assertEqual(confirmed.items[0].status, "at_risk")
        self.assertIn("перепроверка до 2027-01-24", confirmed.items[0].response)

    def test_extended_deadline_can_be_checked_by_same_confirm_without_new_flow(self):
        passport = self.create("2027-09-14", "2027-09-21")
        self.assertEqual(self.confirm(passport).status, "at_risk")
        from datetime import UTC, datetime

        renewed = self.fact.model_copy(update={"valid_until": datetime(2028, 1, 24, tzinfo=UTC)})
        self.repo.upsert("facts", renewed.model_dump(mode="json"))
        confirmed = self.confirm(passport)
        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.room_number, "512")
        self.assertNotIn("перепроверка", confirmed.items[0].response)
        self.assertEqual(self.confirm(passport).model_dump(), confirmed.model_dump())
