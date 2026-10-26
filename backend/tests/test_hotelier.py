"""Synthetic invariants; these are not accuracy labels for real hotels."""
from __future__ import annotations

import copy
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.agents.answer_parser import parse_answer
from backend.agents.interviewer import create_question, generate_questions
from backend.api.hotelier import confirm_answer, router
from backend.contracts import Claim, Question
from backend.db.repository import get_repo
from backend.security import DEMO_ADMIN
from backend.sim.hotelier import simulate_answer


class MemoryRepo:
    """Test-only repository; production routers use the durable psycopg dependency."""
    def __init__(self):
        self.tables = {}
        self.insert("hotels", {"id": "test-hotel", "name": "Synthetic hotel", "lat": 43.58, "lon": 39.72})
        root = Path(__file__).resolve().parents[2]
        for path in (root / "data" / "characteristics").glob("*.yaml"):
            self.insert("characteristics", yaml.safe_load(path.read_text()))

    def all(self, table):
        return copy.deepcopy(list(self.tables.get(table, {}).values()))

    def get(self, table, item_id):
        return copy.deepcopy(self.tables.get(table, {}).get(item_id))

    def find(self, table, **equal):
        return [row for row in self.all(table) if all(row.get(key) == value for key, value in equal.items())]

    def insert(self, table, value):
        if value["id"] in self.tables.setdefault(table, {}):
            raise ValueError("duplicate")
        self.tables[table][value["id"]] = copy.deepcopy(value)
        return value

    def upsert(self, table, value):
        self.tables.setdefault(table, {})[value["id"]] = copy.deepcopy(value)
        return value

    def add_claim(self, claim):
        return self.insert("claims", Claim.model_validate(claim).model_dump(mode="json"))


class ParserTests(unittest.TestCase):
    def question(self, cid="soundproofing"):
        return Question(hotel_id="test-hotel", characteristic_ids=[cid], text="Уточните свойство")

    def parsed(self, text, cid="soundproofing"):
        answer = parse_answer(self.question(cid), text)
        self.assertTrue(all(update.quote in text for update in answer.updates))
        self.assertFalse(answer.confirmed)
        return {u.characteristic_id: u for u in answer.updates}, answer

    def test_multi_field_exact_quotes_and_no_inferred_insulation(self):
        fields, answer = self.parsed("Окна двойные, на запад номера 501–512 с балконом, с 5 этажа. Blackout есть, кондиционер есть. Принимаем собак до 30 кг за 1000 рублей за ночь.")
        # Separate scope observations remain separate; all known amenity fields survive.
        sound = {}
        for update in answer.updates:
            if update.characteristic_id == "soundproofing":
                sound.update(update.value)
        fields["soundproofing"].value = sound
        self.assertIsNone(fields["soundproofing"].value["available"])
        self.assertEqual(fields["soundproofing"].value["windows"], "double")
        self.assertTrue(fields["soundproofing"].value["blackout"])
        self.assertTrue(fields["soundproofing"].value["air_conditioning"])
        self.assertEqual(fields["room_orientation"].value["rooms"], [str(number) for number in range(501, 513)])
        self.assertEqual(fields["room_orientation"].scope.facade, "W")
        self.assertEqual(fields["room_orientation"].scope.floor_min, 5)
        self.assertEqual(fields["pets"].value["max_weight_kg"], 30)
        self.assertEqual(fields["pets"].value["fee_rub_night"], 1000)

    def test_explicit_feature_negations(self):
        for text in ("Blackout нет, кондиционер есть.", "Нет blackout, есть кондиционер.", "Шторы blackout отсутствуют, кондиционер установлен."):
            with self.subTest(text=text):
                fields, _ = self.parsed(text)
                self.assertFalse(fields["soundproofing"].value["blackout"])
                self.assertTrue(fields["soundproofing"].value["air_conditioning"])

    def test_pets_prohibited_not_unknown(self):
        for text in ("С питомцами нельзя.", "Животных не принимаем.", "Собак не принимаем.", "Размещение без животных."):
            with self.subTest(text=text):
                fields, _ = self.parsed(text, "pets")
                self.assertFalse(fields["pets"].value["available"])

    def test_no_fee_negation_does_not_prohibit_pets(self):
        fields, _ = self.parsed("С собаками до 30 кг можно, без доплаты.", "pets")
        self.assertTrue(fields["pets"].value["available"])
        self.assertEqual(fields["pets"].value["fee_rub_night"], 0)

    def test_species_restriction(self):
        fields, _ = self.parsed("Кошек принимаем, собак нет.", "pets")
        self.assertTrue(fields["pets"].value["available"])
        self.assertEqual(fields["pets"].value["species"], ["cat"])

    def test_uncertainty_is_never_true(self):
        for text in ("Кажется, можно с собакой.", "Не знаю.", "Возможно, есть blackout.", "Шумоизоляция неизвестна."):
            with self.subTest(text=text):
                fields, answer = self.parsed(text)
                self.assertFalse(any(u.value.get("available") is True for u in fields.values()))
                self.assertTrue(answer.unknown_fields)

    def test_partial_uncertainty_preserves_known_windows(self):
        fields, answer = self.parsed("Окна двойные, про blackout не знаю.")
        self.assertEqual(fields["soundproofing"].value["windows"], "double")
        self.assertIsNone(fields["soundproofing"].value.get("blackout"))
        self.assertIn("soundproofing.blackout", answer.unknown_fields)

    def test_room_number_does_not_prove_floor(self):
        fields, _ = self.parsed("На запад номера 501–512 с балконом.")
        self.assertIsNone(fields["room_orientation"].scope.floor_min)
        self.assertNotIn("min_floor", fields["room_orientation"].value)

    def test_invalid_ranges_and_units_need_clarification(self):
        for text in ("На запад номера 512–501.", "На запад с 150 этажа.", "Собаки до 300 кг за 1000 рублей.", "Собаки до -3 кг за -500 рублей за ночь."):
            with self.subTest(text=text):
                fields, answer = self.parsed(text, "pets")
                self.assertTrue(answer.needs_clarification)
                for update in fields.values():
                    self.assertNotIn("max_weight_kg", update.value)
                    self.assertNotIn("fee_rub_night", update.value)

    def test_two_facades_not_one_room(self):
        fields, answer = self.parsed("На запад и восток номера 501–512.")
        self.assertNotIn("room_orientation", fields)
        self.assertTrue(answer.needs_clarification)

    def test_short_yes_requires_scope_clarification(self):
        _, answer = self.parsed("Да")
        self.assertFalse(answer.updates)
        self.assertTrue(answer.needs_clarification)

    def test_pets_unknown_does_not_mean_false(self):
        fields, answer = self.parsed("Про питомцев не знаем.", "pets")
        self.assertNotIn("pets", fields)
        self.assertIn("pets", answer.unknown_fields)

    def test_measured_values_not_invented(self):
        fields, _ = self.parsed("Окна двойные. Ночью тихо.")
        self.assertNotIn("night_db_min", fields["quiet_night"].value)
        self.assertIsNone(fields["soundproofing"].value["available"])


class HotelierWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepo()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_repo] = lambda: self.repo
        self.client = TestClient(app)
        login = self.client.post("/api/hotelier/login", json={"username": "hotelier", "password": "hotelier123"})
        self.auth = {"Authorization": "Bearer " + login.json()["token"]}

    def test_preview_confirm_and_retry_are_durable_and_quoted(self):
        question = self.client.post("/api/questions", json={"hotel_id": "test-hotel", "characteristic_id": "pets"}).json()
        self.assertEqual(question["id"], self.client.post("/api/questions", json={"hotel_id": "test-hotel", "characteristic_id": "pets"}).json()["id"])
        response = self.client.post(f'/api/questions/{question["id"]}/answer', headers=self.auth,
                                    json={"text": "С собаками до 30 кг можно за 500 рублей за ночь."})
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        self.assertEqual(self.repo.all("claims"), [])
        self.assertFalse(preview["confirmed"])
        confirmed = self.client.post(f'/api/answers/{preview["id"]}/confirm', headers=self.auth, json={})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertTrue(confirmed.json()["confirmed"])
        again = self.client.post(f'/api/answers/{preview["id"]}/confirm', headers=self.auth, json={})
        self.assertEqual(again.json(), confirmed.json())
        self.assertEqual(len(self.repo.all("claims")), 1)
        claim = self.repo.all("claims")[0]
        self.assertEqual(claim["source"], "hotelier_simulator")
        self.assertTrue(claim["synthetic"])
        self.assertIn(claim["evidence"][0]["quote"], preview["raw_text"])
        self.assertEqual(self.repo.find("facts", characteristic_id="pets")[0]["status"], "computed")
        # New repository reads use stored answers/facts, no module-level state.
        copied = MemoryRepo()
        copied.tables = copy.deepcopy(self.repo.tables)
        self.assertEqual(confirm_answer(preview["id"], copied, principal=DEMO_ADMIN).created_claim_ids, confirmed.json()["created_claim_ids"])

    def test_confirmation_is_concurrently_idempotent(self):
        question = create_question("test-hotel", "pets", self.repo)
        answer = parse_answer(question, "С собакой можно.")
        self.repo.insert("answers", answer.model_dump(mode="json"))
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(lambda _: confirm_answer(answer.id, self.repo, principal=DEMO_ADMIN), range(8)))
        self.assertTrue(all(value.confirmed for value in values))
        self.assertEqual(len(self.repo.all("claims")), 1)

    def test_unauthorized_mutations_cannot_write(self):
        question = create_question("test-hotel", "pets", self.repo)
        response = self.client.post(f"/api/questions/{question.id}/answer", json={"text": "Да"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.repo.all("answers"), [])
        response = self.client.post(f"/api/questions/{question.id}/simulate", json={})
        self.assertEqual(response.status_code, 401)

    def test_cannot_skip_preview(self):
        question = create_question("test-hotel", "pets", self.repo)
        response = self.client.post(f"/api/questions/{question.id}/answer", headers=self.auth, json={"text": "Да", "confirm": True})
        self.assertEqual(response.status_code, 422)
        self.assertFalse(self.repo.all("claims"))

    def test_simulated_claims_stay_synthetic(self):
        question = create_question("test-hotel", "pets", self.repo)
        answer = simulate_answer(question, self.repo)
        self.assertTrue(answer.synthetic)
        self.assertEqual(self.repo.all("claims"), [])
        self.repo.insert("answers", answer.model_dump(mode="json"))
        confirm_answer(answer.id, self.repo, principal=DEMO_ADMIN)
        self.assertTrue(all(c["synthetic"] and c["source"] == "hotelier_simulator" for c in self.repo.all("claims")))
        self.assertTrue(self.repo.find("facts", characteristic_id="pets")[0]["synthetic"])

    def test_voi_question_order_synthetic_demand(self):
        questions = generate_questions("test-hotel", self.repo)
        self.assertTrue(questions)
        self.assertEqual([q.priority for q in questions], sorted([q.priority for q in questions], reverse=True))
        self.assertTrue(all(q.demand_synthetic and len(q.options) == 4 for q in questions))
        self.assertEqual([q.id for q in questions], [q.id for q in generate_questions("test-hotel", self.repo)])


if __name__ == "__main__":
    unittest.main()


class SyntheticLabelledParserCases(unittest.TestCase):
    def test_manually_authored_synthetic_regressions(self):
        import json
        path = Path(__file__).resolve().parents[2] / "data/hotelier_profiles/parser_eval.json"
        dataset = json.loads(path.read_text())
        self.assertTrue(dataset["synthetic"])
        for case in dataset["cases"]:
            with self.subTest(case=case["id"], text=case["text"]):
                cid = case["question_characteristic_id"]
                question = Question(hotel_id="synthetic", characteristic_ids=[cid], text="Уточните")
                answer = parse_answer(question, case["text"], synthetic=True)
                self.assertTrue(answer.synthetic)
                self.assertTrue(all(update.quote in answer.raw_text for update in answer.updates))
                matching = [update for update in answer.updates if update.characteristic_id == cid]
                if case["absent"]:
                    self.assertFalse(matching)
                else:
                    self.assertTrue(matching)
                    actual = {key: value for update in matching for key, value in update.value.items()}
                    for key, expected in case["expected"].items():
                        self.assertIn(key, actual)
                        self.assertEqual(actual[key], expected)
                if case["unknown"]:
                    self.assertIn(cid, answer.unknown_fields)


class QualitativeAndNightParserTests(unittest.TestCase):
    def answer(self, text, cid):
        question = Question(hotel_id="synthetic", characteristic_ids=[cid], text="Уточните")
        answer = parse_answer(question, text, synthetic=True)
        self.assertTrue(all(update.quote in text for update in answer.updates))
        return answer

    def test_negative_insulation_quality_is_not_positive_availability(self):
        for text in ("Шумоизоляция плохая.", "У нас слабая шумоизоляция.", "Низкая звукоизоляция.",
                     "Шумоизоляция очень плохая.", "Шумоизоляция ужасная.", "Отвратительная шумоизоляция.",
                     "Шумоизоляция недостаточная.", "Шумоизоляция так себе."):
            with self.subTest(text=text):
                answer = self.answer(text, "soundproofing")
                updates = [u for u in answer.updates if u.characteristic_id == "soundproofing"]
                self.assertTrue(updates)
                self.assertTrue(all(u.value["available"] is False for u in updates))

    def test_later_window_description_preserves_explicit_poor_insulation(self):
        answer = self.answer("Шумоизоляция плохая. Окна двойные.", "soundproofing")
        update = next(u for u in answer.updates if u.characteristic_id == "soundproofing")
        self.assertIs(update.value["available"], False)
        self.assertEqual(update.value["windows"], "double")

    def test_negated_quality_and_uncertainty_abstain(self):
        for text in ("Шумоизоляция не плохая.", "Кажется, шумоизоляция слабая.", "Шумоизоляция, наверное, плохая."):
            with self.subTest(text=text):
                answer = self.answer(text, "soundproofing")
                self.assertFalse(any(u.value.get("available") is True for u in answer.updates))
                self.assertTrue(answer.unknown_fields)
                self.assertTrue(answer.needs_clarification)

    def test_day_noise_does_not_reverse_night_quiet(self):
        for text in ("Днём шумно, ночью тихо.", "Днем шумно, а ночью тихо.", "Днем шумно. Ночью тихо.",
                     "Днем шумно ночью тихо.", "Ночью тихо, днем шумно.", "Утром шумно, ночью не шумно."):
            with self.subTest(text=text):
                answer = self.answer(text, "quiet_night")
                updates = [u for u in answer.updates if u.characteristic_id == "quiet_night"]
                self.assertTrue(updates)
                self.assertTrue(all(u.value["available"] is True for u in updates))
                self.assertTrue(all("ноч" in u.quote.lower() for u in updates))

    def test_day_quiet_does_not_reverse_night_noise(self):
        answer = self.answer("Днем тихо, ночью шумно.", "quiet_night")
        update = next(u for u in answer.updates if u.characteristic_id == "quiet_night")
        self.assertFalse(update.value["available"])
        self.assertEqual(update.quote, "ночью шумно")

    def test_day_only_statement_does_not_assert_night(self):
        for text in ("Днем тихо.", "Днём шумно.", "Вечером тихо."):
            with self.subTest(text=text):
                answer = self.answer(text, "quiet_night")
                self.assertFalse(any(u.characteristic_id == "quiet_night" for u in answer.updates))
                self.assertIn("quiet_night", answer.unknown_fields)


class ContradictoryNightClausesTests(unittest.TestCase):
    def test_untagged_continuation_does_not_hide_night_contradiction(self):
        for text in ("Ночью тихо, но шумно.", "Ночью тихо и шумно."):
            with self.subTest(text=text):
                question = Question(hotel_id="synthetic", characteristic_ids=["quiet_night"], text="Уточните")
                answer = parse_answer(question, text, synthetic=True)
                self.assertFalse(any(u.characteristic_id == "quiet_night" for u in answer.updates))
                self.assertIn("quiet_night", answer.unknown_fields)


class SafeConfirmationSchemaTests(unittest.TestCase):
    def test_persisted_unsafe_or_invalid_schemas_are_rejected_before_writes_without_network(self):
        from unittest.mock import patch

        from fastapi import HTTPException

        schemas = [
            {"$ref": "http://127.0.0.1:12345/forbidden-schema"},
            {"type": "object", "properties": {"available": {"$ref": "file:///forbidden-schema"}}},
            {"$dynamicRef": "http://127.0.0.1:12345/forbidden-schema"},
            {"$ref": "#/missing"},
            {"type": "invalid-schema-type"},
        ]
        for schema in schemas:
            with self.subTest(schema=schema):
                repo = MemoryRepo()
                question = create_question("test-hotel", "pets", repo)
                answer = parse_answer(question, "С собакой можно.")
                repo.insert("answers", answer.model_dump(mode="json"))
                spec = repo.get("characteristics", "pets")
                spec["value_schema"] = schema
                repo.upsert("characteristics", spec)
                with patch("urllib.request.urlopen", side_effect=AssertionError("Network retrieval is forbidden")) as retrieve:
                    with self.assertRaises(HTTPException) as error:
                        confirm_answer(answer.id, repo, principal=DEMO_ADMIN)
                    self.assertEqual(error.exception.status_code, 422)
                    retrieve.assert_not_called()
                self.assertEqual(repo.all("claims"), [])
                self.assertEqual(repo.all("sources"), [])
                self.assertFalse(repo.get("answers", answer.id)["confirmed"])
