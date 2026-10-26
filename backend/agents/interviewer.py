"""Value-of-information queue; demand is counted from the local search log."""
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


def _build_question(hotel_id: str, cid: str, facts: list[dict], *, reason="unknown", ordinal=0, demand=0) -> Question:
    related = [fact for fact in facts if fact["characteristic_id"] == cid]
    confidence = max((fact.get("confidence", 0) for fact in related), default=0)
    uncertainty = 1-confidence
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
                    hypothesis=hypothesis, priority=round((1+demand)*uncertainty*IMPACT.get(cid, .65), 3), reason=reason,
                    evidence_refs=[item["id"] for fact in related for item in fact.get("evidence", [])],
                    demand_estimate=demand, demand_synthetic=False)


def create_question(hotel_id: str, characteristic_id: str, repo, *, reason="guest", demand: dict | None = None) -> Question:
    """One pending question per hotel/characteristic, including concurrent retries."""
    from backend.agents.demand import search_demand
    demand = demand if demand is not None else search_demand(repo, hotel_id)["shown_with"]
    with _LOCK:
        previous = [value for value in repo.find("questions", hotel_id=hotel_id)
                    if characteristic_id in value.get("characteristic_ids", [])]
        pending = next((value for value in previous if value.get("status") == "pending"), None)
        if pending:
            return Question.model_validate(pending)
        question = _build_question(hotel_id, characteristic_id, repo.find("facts", hotel_id=hotel_id), reason=reason,
                                   ordinal=len(previous), demand=demand.get(characteristic_id, 0))
        try:
            repo.insert("questions", question.model_dump(mode="json"))
        except UniqueViolation:
            return Question.model_validate(repo.get("questions", question.id))
        return question


def generate_questions(hotel_id: str, repo) -> list[Question]:
    from backend.agents.demand import search_demand
    demand = search_demand(repo, hotel_id)["shown_with"]
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
        by_cid[cid] = create_question(hotel_id, cid, repo, reason=reason, demand=demand)
    for value in repo.find("questions", hotel_id=hotel_id):
        if value.get("status") == "pending":
            question = Question.model_validate(value)
            by_cid[question.characteristic_ids[0]] = question
    return sorted(by_cid.values(), key=lambda question: (-question.priority, question.id))


def ask_contradictions(repo, conflicts: list) -> list:
    """Ask the hotel about each detected source contradiction exactly once.

    Returns the conflicts with ``status="asked"`` and the question reference. The
    question quotes both sides; it never proposes which side is right.
    """
    from backend.agents.resolver import CONTRADICTION_PREFIX, VIEW_LABELS
    updated = []
    for conflict in conflicts:
        if not conflict.id.startswith(CONTRADICTION_PREFIX) or conflict.status != "open":
            updated.append(conflict)
            continue
        question_id = stable_id("question", conflict.hotel_id, conflict.characteristic_id, "conflict", conflict.id)
        if repo.get("questions", question_id) is None:
            label, target = VIEW_LABELS.get(conflict.characteristic_id, (conflict.characteristic_id, conflict.characteristic_id))
            claims = [repo.get("claims", claim_id) for claim_id in conflict.claim_ids]
            evidence_refs = [evidence["id"] for claim in claims if claim for evidence in claim.get("evidence", [])]
            if conflict.characteristic_id == "beach_walk":
                text = ("Источники расходятся: на сайте указано короткое расстояние до пляжа, а пешеходный маршрут по карте "
                        "заметно длиннее. Есть ли более короткий пеший проход к пляжу? Сколько минут идти и есть ли на пути "
                        "лестницы или переходы через дорогу?")
            else:
                text = (f"Источники расходятся: в открытом описании указан {label}, а наш расчёт видимости не находит его "
                        "ни с одного рассчитанного фасада. Какие номера, с какой стороны и с какого этажа видят "
                        f"{target}? Если таких номеров нет, напишите об этом.")
            question = Question(id=question_id, hotel_id=conflict.hotel_id, characteristic_ids=[conflict.characteristic_id], text=text,
                                hypothesis=conflict.resolution_note, priority=1000.0, reason="conflict",
                                evidence_refs=evidence_refs, demand_estimate=0, demand_synthetic=False)
            try:
                repo.insert("questions", question.model_dump(mode="json"))
            except UniqueViolation:
                pass
        updated.append(conflict.model_copy(update={"status": "asked", "question_id": question_id}))
    return updated
