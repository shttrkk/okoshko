"""Build/validate invented fixture dialogues with the actual parser and confirm.

This private offline fixture tool uses the test-only repository. It never calls
HTTP, a live DB, LLM transport, email or a real hotel.
"""
from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

from backend.agents.answer_parser import parse_answer
from backend.agents.interviewer import stable_id
from backend.agents.resolver import resolve_claims
from backend.api.hotelier import confirm_answer
from backend.contracts import AgentRun, AgentStep, Answer, CharacteristicSpec, Claim, Question, now
from backend.contracts.validation import validate_value
from backend.sim.hotelier import simulate_answer
from backend.tests.test_hotelier import MemoryRepo

ROOT = Path(__file__).resolve().parents[2]
PROFILE = Path(__file__).with_name("pets_dialogue_profiles.json")
PUBLIC = ROOT / "data/seed/demo_hotelier_dialogues.json"
ARTIFACT = "/data/seed/demo_hotelier_dialogues.json"


def build(profile: dict) -> dict:
    scenario = profile["scenario_id"]
    timestamp = now()
    hotels = {hotel["id"]: hotel for hotel in json.loads((ROOT / "data/seed/hotels.json").read_text())}
    repo = MemoryRepo()
    result = {"schema_version": 1, "synthetic": True, "questions": [], "answers": [], "claims": [], "agent_runs": [], "agent_steps": []}
    for item in profile["completed_profiles"]:
        hid = item["hotel_id"]
        repo.upsert("hotels", hotels[hid])
        question = Question(id=stable_id(scenario, hid, "question"), hotel_id=hid, characteristic_ids=["pets"],
            text="Синтетический демо-вопрос: каких питомцев принимаете, до какого веса и за какую доплату за ночь?",
            reason="guest", demand_estimate=100, demand_synthetic=True, priority=100, asked_at=timestamp)
        repo.insert("questions", question.model_dump(mode="json"))
        answer = parse_answer(question, item["answers"]["pets"], synthetic=True)
        answer.id = stable_id(scenario, hid, "answer")
        answer.created_at = timestamp
        assert [update.value for update in answer.updates] == [item["truth"]["pets"]]
        repo.insert("answers", answer.model_dump(mode="json"))
        run = AgentRun(id=stable_id(scenario, hid, "run"), kind="interview", hotel_id=hid,
            characteristic_id="pets", trigger="hotelier_preview", status="waiting", llm_mode="offline", started_at=timestamp)
        run.steps = [
            AgentStep(id=stable_id(scenario, hid, "step", 0), run_id=run.id, seq=0, agent="interviewer", type="decision",
                name="Синтетический вопрос о питомцах", input={"scenario_id": scenario, "synthetic": True},
                output={"question_id": question.id, "synthetic": True}, rationale="Вымышленная политика пока не получена в demo-сценарии", created_at=timestamp),
            AgentStep(id=stable_id(scenario, hid, "step", 1), run_id=run.id, seq=1, agent="answer_parser", type="extraction",
                name="Ответ симулятора разобран на поля с цитатами", input={"question_id": question.id, "answer_id": answer.id, "synthetic": True},
                output={"updates": [u.model_dump(mode="json") for u in answer.updates], "unknown_fields": answer.unknown_fields, "synthetic": True},
                rationale="Запущен существующий parse_answer без LLM и без предположений о реальной гостинице", created_at=timestamp),
            AgentStep(id=stable_id(scenario, hid, "step", 2), run_id=run.id, seq=2, agent="hotelier_simulator", type="preview",
                name="Синтетический предпросмотр: claims ещё не созданы", input={"answer_id": answer.id, "synthetic": True},
                output={"created_claim_ids": [], "confirmed": False, "synthetic": True}, rationale="Создание claim требует отдельного подтверждения", created_at=timestamp),
        ]
        repo.insert("agent_runs", run.model_dump(mode="json"))
        before = len(repo.all("claims"))
        confirmed = confirm_answer(answer.id, repo)
        assert confirmed.confirmed and len(repo.all("claims")) == before + len(answer.updates)
        assert confirm_answer(answer.id, repo).created_claim_ids == confirmed.created_claim_ids
        assert len(repo.all("claims")) == before + len(answer.updates)
        exported_claims = []
        for claim_id in confirmed.created_claim_ids:
            claim = Claim.model_validate(repo.get("claims", claim_id))
            # Artifact/run references are attached before the first seed persistence.
            claim.run_id = run.id
            for ev in claim.evidence:
                ev.artifact = ARTIFACT
                ev.label = "Ответ симулятора подтверждён: вымышленная политика для demo"
                ev.inputs.update(scenario_id=scenario, raw_text=answer.raw_text, synthetic=True)
                ev.limitations = [*profile["limitations"], "Ответ симулятора подтверждён; реального подтверждения гостиницы нет."]
            exported_claims.append(claim.model_dump(mode="json"))
        run = AgentRun.model_validate(repo.get("agent_runs", run.id))
        run.trigger = "synthetic_demo_dialogue"
        run.steps[-1].id = stable_id(scenario, hid, "step", 3)
        run.steps[-1].name = "Ответ симулятора подтверждён"
        run.steps[-1].input["synthetic"] = True
        run.steps[-1].output.update(synthetic=True, fact_status="computed", display_status="Ответ симулятора подтверждён")
        run.steps[-1].rationale = "Демо-подтверждение создало synthetic claims; это не реальное подтверждение гостиницы"
        run.steps[-1].evidence_refs = [ev["id"] for claim in exported_claims for ev in claim["evidence"]]
        result["questions"].append(repo.get("questions", question.id))
        result["answers"].append(confirmed.model_dump(mode="json"))
        result["claims"].extend(exported_claims)
        result["agent_runs"].append(run.model_dump(mode="json"))
        result["agent_steps"].extend(step.model_dump(mode="json") for step in run.steps)
    for item in profile["live_profiles"]:
        hid = item["hotel_id"]
        question = Question(id=stable_id(scenario, hid, "question"), hotel_id=hid, characteristic_ids=["pets"],
            text="Живая синтетическая демонстрация: каких питомцев принимаете, до какого веса и за какую доплату? Нажмите «Симулировать», проверьте цитаты и подтвердите.",
            reason="guest", status="pending", priority=1000, demand_estimate=100, demand_synthetic=True, asked_at=timestamp)
        result["questions"].append(question.model_dump(mode="json"))
    return result


def validate(envelope: dict, profile: dict) -> dict:
    assert envelope["schema_version"] == 1 and envelope["synthetic"] is True
    models = {"questions": Question, "answers": Answer, "claims": Claim, "agent_runs": AgentRun, "agent_steps": AgentStep}
    parsed = {table: [model.model_validate(value) for value in envelope[table]] for table, model in models.items()}
    tables = {table: {value.id: value for value in values} for table, values in parsed.items()}
    assert all(len(tables[table]) == len(values) for table, values in parsed.items())
    hotels = {hotel["id"]: hotel for hotel in json.loads((ROOT / "data/seed/hotels.json").read_text())}
    specs = [CharacteristicSpec.model_validate(value) for value in MemoryRepo().all("characteristics")]
    spec = next(value for value in specs if value.id == "pets")
    truth = {item["hotel_id"]: item for item in profile["completed_profiles"]}
    assert len(truth) == len(tables["answers"]) == len(tables["claims"]) == 7
    assert len({q.hotel_id for q in parsed["questions"]}) == 9
    assert len([q for q in parsed["questions"] if q.status == "pending"]) == 2
    used_claims = set()
    for answer in parsed["answers"]:
        question = tables["questions"][answer.question_id]
        assert question.hotel_id in hotels and question.status == "answered"
        assert answer.synthetic and answer.confirmed and answer.created_claim_ids
        replay = parse_answer(question, answer.raw_text, synthetic=True)
        assert [u.model_dump() for u in replay.updates] == [u.model_dump() for u in answer.updates]
        assert [u.value for u in answer.updates] == [truth[question.hotel_id]["truth"]["pets"]]
        assert all(u.quote in answer.raw_text for u in answer.updates)
        for index, cid in enumerate(answer.created_claim_ids):
            claim = tables["claims"][cid]
            used_claims.add(cid)
            update = answer.updates[index]
            assert claim.hotel_id == question.hotel_id and claim.characteristic_id == "pets"
            assert claim.synthetic and claim.source == "hotelier_simulator" and claim.value == update.value and claim.scope == update.scope
            assert claim.valid_until - claim.observed_at == timedelta(days=90)
            assert claim.observed_at == answer.created_at and claim.value["max_weight_kg"] >= 30 and "dog" in claim.value["species"]
            validate_value(claim.value, spec.value_schema)
            assert claim.run_id in tables["agent_runs"]
            for ev in claim.evidence:
                assert ev.kind == "simulation" and ev.message_id == answer.id and ev.quote == update.quote and ev.quote in answer.raw_text
                assert ev.artifact == ARTIFACT and ev.inputs["raw_text"] == answer.raw_text and ev.inputs["synthetic"]
            facts, _ = resolve_claims(question.hotel_id, [claim], specs, [])
            pets = next(f for f in facts if f.characteristic_id == "pets")
            assert pets.synthetic and pets.status == "computed" and pets.value == update.value
    assert used_claims == set(tables["claims"])
    for run in parsed["agent_runs"]:
        assert run.status == "completed" and run.hotel_id in truth
        assert [s.model_dump() for s in run.steps] == [s.model_dump() for s in parsed["agent_steps"] if s.run_id == run.id]
        assert run.steps[-1].name == "Ответ симулятора подтверждён"
    assert all(step.run_id in tables["agent_runs"] for step in parsed["agent_steps"])
    live = {item["hotel_id"]: item for item in profile["live_profiles"]}
    repo = MemoryRepo()
    for question in parsed["questions"]:
        if question.status != "pending":
            continue
        item = live[question.hotel_id]
        repo.upsert("hotels", hotels[question.hotel_id])
        preview = simulate_answer(question, repo, seed=item["simulator_seed"])
        assert preview.synthetic and not preview.confirmed
        assert preview.raw_text == item["answers"]["pets"] and preview.updates[0].value == item["truth"]["pets"]
    return {"completed_hotels": 7, "pending_live_hotels": 2, "questions": len(parsed["questions"]), "answers": len(parsed["answers"]),
            "claims": len(parsed["claims"]), "agent_runs": len(parsed["agent_runs"]), "agent_steps": len(parsed["agent_steps"]), "orphan_records": 0, "synthetic": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Build using the actual offline parser/confirmation and write the dedicated public fixture")
    args = parser.parse_args(argv)
    profile = json.loads(PROFILE.read_text())
    envelope = build(profile) if args.write else json.loads(PUBLIC.read_text())
    report = validate(envelope, profile)
    if args.write:
        PUBLIC.write_text(json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
