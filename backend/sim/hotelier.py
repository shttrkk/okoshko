"""Private synthetic hotelier responses; never observations about real hotels."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.agents.answer_parser import parse_answer
from backend.contracts import Answer, Question

PROFILE = Path(__file__).resolve().parents[2] / "data" / "hotelier_profiles" / "synthetic.json"


def simulate_answer(question: Question, repo, *, seed: int = 42) -> Answer:
    if not repo.get("hotels", question.hotel_id):
        raise ValueError("Отель не найден")
    profiles = json.loads(PROFILE.read_text())["profiles"]
    index = int(hashlib.sha256(f"{seed}:{question.hotel_id}".encode()).hexdigest()[:8], 16) % len(profiles)
    profile = profiles[index]
    cid = question.characteristic_ids[0]
    text = profile["answers"].get(cid, "Не знаю, нужно уточнить конкретные номера и условия.")
    return parse_answer(question, text, synthetic=True)
