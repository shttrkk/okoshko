"""Value-of-information queue with explicitly synthetic demand estimates."""
from __future__ import annotations

import hashlib
from threading import RLock

from psycopg.errors import UniqueViolation

from backend.contracts import Question

_LOCK = RLock()
LABELS = {"soundproofing": "шумоизоляция, окна, blackout и кондиционер", "room_orientation": "сторона, этажи и номера с балконом", "pets": "правила размещения с питомцами", "sunset_view": "закат из окна", "sea_view": "вид на море", "quiet_night": "тишина ночью", "mountain_view": "вид на горы"}
IMPACT = {"soundproofing": 1., "room_orientation": 1., "pets": .85, "sunset_view": .95, "quiet_night": 1., "sea_view": .9}


def stable_id(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]


def _build_question(hotel_id: str, cid: str, facts: list[dict], *, reason="unknown", ordinal=0) -> Question:
    related = [fact for fact in facts if fact["characteristic_id"] == cid]
    confidence = max((fact.get("confidence", 0) for fact in related), default=0)
    uncertainty = 1-confidence
    # This seeded value is a product demo, not search traffic or a forecast.
    demand = 30 + int(stable_id(hotel_id, cid)[:6], 16) % 171
    hypothesis = None
    known = next((fact for fact in related if fact.get("value") is not None), None)
    if known:
        scope = known.get("scope", {})
        qualifiers = []
        if scope.get("facade"):
            qualifiers.append("фасад " + scope["facade"])
        if scope.get("floor_min"):
            qualifiers.append("с " + str(scope["floor_min"]) + " этажа")
        hypothesis = "Текущее утверждение: " + str(known["value"]) + ("; " + ", ".join(qualifiers) if qualifiers else "") + ". Уточните конкретные номера и ограничения."
    label = LABELS.get(cid, cid)
    prompts = {
        "soundproofing": "Какие окна установлены? Есть ли заявленная шумоизоляция, шторы blackout и кондиционер? Укажите номера и ограничения; двойные окна сами по себе не доказывают шумоизоляцию.",
        "room_orientation": "Какие номера выходят на какую сторону, на каких этажах и есть ли у них балкон?",
        "pets": "Какие питомцы разрешены, до какого веса и какая доплата за ночь?",
    }
    return Question(id=stable_id("question", hotel_id, cid, ordinal), hotel_id=hotel_id, characteristic_ids=[cid],
                    text=prompts.get(cid, f"Подтвердите свойство «{label}»: в каких номерах, на какой стороне и с какого этажа?"),
                    hypothesis=hypothesis, priority=round(demand*uncertainty*IMPACT.get(cid, .65), 3), reason=reason,
                    evidence_refs=[item["id"] for fact in related for item in fact.get("evidence", [])],
                    demand_estimate=demand, demand_synthetic=True)


def create_question(hotel_id: str, characteristic_id: str, repo, *, reason="guest") -> Question:
    """One pending question per hotel/characteristic, including concurrent retries."""
    with _LOCK:
        previous = [value for value in repo.find("questions", hotel_id=hotel_id)
                    if characteristic_id in value.get("characteristic_ids", [])]
        pending = next((value for value in previous if value.get("status") == "pending"), None)
        if pending:
            return Question.model_validate(pending)
        question = _build_question(hotel_id, characteristic_id, repo.find("facts", hotel_id=hotel_id), reason=reason, ordinal=len(previous))
        try:
            repo.insert("questions", question.model_dump(mode="json"))
        except UniqueViolation:
            return Question.model_validate(repo.get("questions", question.id))
        return question


def generate_questions(hotel_id: str, repo) -> list[Question]:
    facts = repo.find("facts", hotel_id=hotel_id)
    specs = [spec for spec in repo.all("characteristics") if spec.get("hotelier_answerable", True) and spec.get("status", "active") == "active"]
    by_cid = {}
    for spec in specs:
        cid = spec["id"]
        related = [fact for fact in facts if fact["characteristic_id"] == cid]
        usable = [fact for fact in related if fact.get("status") in {"computed", "confirmed"} and fact.get("confidence", 0) >= .75]
        if usable:
            continue
        reason = "disputed" if any(fact.get("status") == "disputed" for fact in related) else "low_confidence" if any(fact.get("value") is not None for fact in related) else "unknown"
        by_cid[cid] = create_question(hotel_id, cid, repo, reason=reason)
    for value in repo.find("questions", hotel_id=hotel_id):
        if value.get("status") == "pending":
            question = Question.model_validate(value)
            by_cid[question.characteristic_ids[0]] = question
    return sorted(by_cid.values(), key=lambda question: (-question.priority, question.id))
