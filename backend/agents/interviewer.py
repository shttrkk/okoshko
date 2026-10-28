"""Value-of-information queue; demand is counted from the local search log."""
from __future__ import annotations

import hashlib
from datetime import datetime
from threading import RLock

from psycopg.errors import UniqueViolation

from backend.agents.guest_text import rounded_minutes
from backend.contracts import FieldUpdate, Question, Scope, now

_LOCK = RLock()
IMPACT = {"soundproofing": 1., "room_orientation": 1., "pets": .85, "sunset_view": .95, "quiet_night": 1., "sea_view": .9, 'hotel_parking': .85}
SIDES = {"N": "север", "NE": "северо-восток", "E": "восток", "SE": "юго-восток", "S": "юг", "SW": "юго-запад", "W": "запад", "NW": "северо-запад"}
MONTHS = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")
_SCOPE_MARKER = "\nГость просит проверить выбранный номер: "
_LEGACY_SCOPE_MARKER = "\nГость просит уточнить модельный срез: "


class QuestionScopeLimit(ValueError):
    """The pending question is full; a ninth scope must not replace an old one."""


def _with_requested_scope(question: Question, scope: Scope | None) -> tuple[Question, bool]:
    if scope is None:
        return question, False
    scope = Scope.model_validate(scope)
    meaning = scope.model_dump(exclude={"type"}, exclude_none=True)
    if not meaning or any(existing.model_dump(exclude={"type"}, exclude_none=True) == meaning for existing in question.requested_scopes):
        return question, False
    if len(question.requested_scopes) >= 8:
        raise QuestionScopeLimit("В одном вопросе можно уточнить не более 8 разных вариантов. Новый вариант не добавлен.")
    result = question.model_copy(deep=True)
    result.requested_scopes.append(scope.model_copy(deep=True))
    descriptions = []
    for requested in result.requested_scopes:
        parts = [SIDES[requested.facade]] if requested.facade else []
        low, high = requested.floor_min, requested.floor_max
        if low is not None and high is not None:
            parts.append(f"{low}-й этаж" if low == high else f"этажи {low}–{high}")
        elif low is not None:
            parts.append(f"с {low}-го этажа")
        elif high is not None:
            parts.append(f"до {high}-го этажа")
        if requested.month:
            parts.append(MONTHS[requested.month-1])
        if requested.room_type:
            parts.append("указанный тип номера")
        descriptions.append(", ".join(parts))
    note = (_SCOPE_MARKER + " / ".join(descriptions) + ". Это пожелание гостя для проверки номера, а не подтверждение существования номера или окна. "
            "Укажите в ответе сторону, этаж, месяц и ограничения своими словами; одного «Да» недостаточно.")
    def without_note(text):
        for marker in (_SCOPE_MARKER, _LEGACY_SCOPE_MARKER):
            text = text.split(marker, 1)[0]
        return text
    result.text = without_note(result.text) + note
    result.hypothesis = without_note(result.hypothesis or "") + note
    return result, True


def stable_id(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]


def _label(spec: dict | None) -> str:
    spec = spec or {}
    return spec.get("display_name") or spec.get("name_ru") or "характеристика отеля"


def _place(scope: dict) -> str:
    parts = ["сторона: " + SIDES[scope["facade"]]] if scope.get("facade") in SIDES else []
    low, high = scope.get("floor_min"), scope.get("floor_max")
    if low and high:
        parts.append(f"{low}-й этаж" if low == high else f"этажи {low}–{high}")
    elif low:
        parts.append(f"с {low}-го этажа")
    elif high:
        parts.append(f"до {high}-го этажа")
    if scope.get("month"):
        parts.append(MONTHS[scope["month"]-1])
    if scope.get("room_type"):
        parts.append("указанная группа номеров")
    return ", ".join(parts)


def _fact_sentence(fact: dict, label: str) -> str:
    """Only render whitelisted observations; never stringify arbitrary values."""
    value = fact.get("value")
    value = value if isinstance(value, dict) else {"available": value}
    cid = fact["characteristic_id"]
    available = value.get("available")
    if cid == "quiet_night":
        index = value.get("index")
        detail = {"quiet": "ночью тихо", "moderate": "ночью умеренный шум", "loud": "ночью шумно", "noisy": "ночью шумно"}.get(index) if isinstance(index, str) else None
        if detail is None:
            detail = "ночью тихо" if available is True else "ночью не тихо" if available is False else "тишина ночью не подтверждена"
    elif cid in {"sea_view", "mountain_view", "sunset_view"}:
        target = {"sea_view": "море", "mountain_view": "горы", "sunset_view": "закат"}[cid]
        detail = f"{target} видно" if available is True else f"{target} не видно" if available is False else f"вид на {target} не подтверждён"
    elif cid == "soundproofing":
        detail = "заявлена шумоизоляция" if available is True else "заявленной шумоизоляции нет" if available is False else "шумоизоляция не подтверждена"
    elif cid == "morning_sun":
        detail = "утром в окна попадает прямое солнце" if available is True else "утром прямого солнца в окнах нет" if available is False else "утреннее солнце требует уточнения"
    elif cid == "beach_walk" and isinstance(value.get("minutes"), int | float):
        detail = f"до пляжа около {rounded_minutes(value['minutes'])} мин пешком"
    elif cid == "beach_surface":
        surface = {"sand": "песок", "pebblestone": "галька", "pebbles": "галька", "gravel": "гравий"}.get(value.get("surface"))
        detail = f"на пляже {surface}" if surface else "покрытие пляжа требует уточнения"
    elif cid == "accessible_route":
        detail = "на пути есть ступени" if value.get("has_steps") else "на пути нет ступеней" if value.get("has_steps") is False else "доступность пути требует уточнения"
    elif cid == "pets":
        detail = "размещение с питомцами разрешено" if available is True else "размещение с питомцами запрещено" if available is False else "условия размещения с питомцами требуют уточнения"
    elif cid == "railway_noise":
        detail = "возможен шум от железной дороги" if available else "шум от железной дороги требует уточнения"
    else:
        detail = f"«{label}»: условие выполнено" if available is True else f"«{label}»: условие не выполнено" if available is False else f"«{label}»: есть частичные сведения"
    place = _place(fact.get("scope", {}))
    return detail + (f" ({place})" if place else "")


def _refresh_night_hypothesis(question: Question, facts: list[dict]) -> Question:
    """Correct wording on the same proposition; never select another scope.

    Preview's stored question_context makes any changed wording require a new
    preview before confirmation. The Boolean value and original scope stay put.
    """
    update = question.hypothesis_update
    if (question.characteristic_ids != ["quiet_night"] or update is None
            or not isinstance(update.value, dict) or type(update.value.get("available")) is not bool):
        return question
    matching = [fact for fact in facts if fact.get("characteristic_id") == "quiet_night"
                and fact.get("status") in {"computed", "confirmed"}
                and isinstance(fact.get("value"), dict)
                and fact["value"].get("available") is update.value["available"]
                and Scope.model_validate(fact.get("scope", {})) == update.scope]
    if len(matching) != 1:
        return question
    statement = _fact_sentence(matching[0], "Тишина ночью").capitalize() + "."
    prior = question.hypothesis or ""
    offsets = [prior.index(marker) for marker in (_SCOPE_MARKER, _LEGACY_SCOPE_MARKER) if marker in prior]
    suffix = prior[min(offsets):] if offsets else ""
    if prior == statement + suffix and update.quote == statement:
        return question
    return question.model_copy(update={"hypothesis": statement + suffix,
                                       "hypothesis_update": update.model_copy(update={"quote": statement})})


def _usable(fact: dict) -> bool:
    if fact.get('characteristic_id') == 'hotel_parking':
        value = fact.get('value') or {}
        if not isinstance(value, dict) or value.get('private_parking') is None:
            return False
        if value.get('private_parking') is True and any(value.get(k) is None for k in ('fee_rub_per_day', 'reservation_required', 'ev_charging')):
            return False
    expiry = fact.get("valid_until")
    if isinstance(expiry, str):
        expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    return (fact.get("status") in {"computed", "confirmed"} and fact.get("value") is not None
            and fact.get("confidence", 0) >= .75 and (expiry is None or expiry > now()))


def _build_question(hotel_id: str, cid: str, facts: list[dict], *, spec=None, reason="unknown", ordinal=0, demand=0) -> Question:
    related = [fact for fact in facts if fact["characteristic_id"] == cid]
    confidence = max((fact.get("confidence", 0) for fact in related), default=0)
    uncertainty = 1-confidence
    hypothesis = None
    label = _label(spec)
    hypothesis_update = None
    known = [fact for fact in related if fact.get("status") not in {"stale", "disputed", "unknown"}
             and isinstance(fact.get("value"), dict) and type(fact["value"].get("available")) is bool]
    # One Boolean proposition has a precise yes/no meaning. Never combine
    # opposite facades, numeric proxies or independent amenities into it.
    if known and cid in {"quiet_night", "sea_view", "mountain_view", "sunset_view", "soundproofing", "morning_sun", "pets"}:
        fact = max(known, key=lambda item: (item.get("confidence", 0), item.get("id", "")))
        hypothesis = _fact_sentence(fact, label).capitalize() + "."
        hypothesis_update = FieldUpdate(characteristic_id=cid, scope=Scope.model_validate(fact.get("scope", {})),
                                        value={"available": fact["value"]["available"]}, quote=hypothesis)
    prompts = {
        "soundproofing": "Какие окна установлены? Есть ли заявленная шумоизоляция, плотные светонепроницаемые шторы и кондиционер? Укажите номера и ограничения; двойные окна сами по себе не доказывают шумоизоляцию.",
        "room_orientation": "Какие номера выходят на какую сторону, на каких этажах и есть ли у них балкон?",
        "pets": "Какие питомцы разрешены, до какого веса и какая доплата за ночь? Если есть ограничения по месяцам, перечислите их в одном ответе.",
        "quiet_night": "Ночью в номерах тихо? Если условия различаются, укажите стороны, этажи или номера.",
        "sea_view": "Из каких номеров видно море? Укажите сторону и этаж, если знаете.",
        "sunset_view": "Из каких номеров видно закат? Укажите сторону, этаж и месяцы, если знаете.",
        "mountain_view": "Из каких номеров видно горы? Укажите сторону и этаж, если знаете.",
        "morning_sun": "В какие номера утром попадает прямое солнце? Укажите сторону и этаж, если знаете.",
        "evening_heat": "В какие номера вечером попадает прямое солнце? Укажите сторону и этаж, если знаете; температуру измерять не нужно.",
        "railway_noise": "В каких номерах ночью слышны поезда? Укажите стороны и условия; уровень шума в децибелах без измерения не нужен.",
        "accessible_route": "Можно ли пройти весь путь от входа отеля до пляжа с коляской? Расскажите про ступени, пандусы и ограничения прохода.",
        "connectivity": "Есть ли Wi-Fi в номерах? Он бесплатный или платный? Если проверяли скорость, укажите загрузку и отдачу в Мбит/с; неизвестную скорость можно не указывать.",
        'hotel_parking': 'Есть ли у отеля своя парковка? Сколько она стоит в рублях за сутки, нужно ли бронировать место и есть ли зарядка для электромобиля? Укажите условия; общественную парковку рядом опишите отдельно.',
    }
    scoped = (spec or {}).get("scope", "hotel") != "hotel"
    text = prompts.get(cid, f"Уточните «{label}»: " + ("для каких сторон, этажей или номеров это верно? Укажите различия и ограничения." if scoped else "что вы знаете об этом у вашего отеля? Укажите условия и ограничения."))
    if hypothesis_update is not None:
        text = "Это утверждение верно? «Да» подтвердит его, «Нет» означает обратное. Если верно частично, опишите различия своими словами."
    if reason == "guest":
        text = ("Гость просит проверить это утверждение. " if hypothesis_update is not None else "Гость просит уточнить сведения. ") + text
    return Question(id=stable_id("question", hotel_id, cid, ordinal), hotel_id=hotel_id, characteristic_ids=[cid],
                    text=text,
                    hypothesis=hypothesis, hypothesis_update=hypothesis_update, priority=round((1+demand)*uncertainty*IMPACT.get(cid, .65), 3), reason=reason,
                    evidence_refs=[item["id"] for fact in related for item in fact.get("evidence", [])],
                    demand_estimate=demand, demand_synthetic=False)


def create_question(hotel_id: str, characteristic_id: str, repo, *, reason="guest", demand: dict | None = None,
                    scope: Scope | None = None) -> Question:
    """One pending question per hotel/characteristic, including concurrent retries."""
    from backend.agents.demand import search_demand
    demand = demand if demand is not None else search_demand(repo, hotel_id)["shown_with"]
    with _LOCK:
        previous = [value for value in repo.find("questions", hotel_id=hotel_id)
                    if characteristic_id in value.get("characteristic_ids", [])]
        pending = next((value for value in previous if value.get("status") == "pending"), None)
        if pending:
            original = Question.model_validate(pending)
            # Refresh legacy seeded wording and hypotheses without reopening a
            # question or dropping any of its independently requested slices.
            if original.reason != "conflict" and original.hypothesis_update is None:
                refreshed = _build_question(hotel_id, characteristic_id, repo.find("facts", hotel_id=hotel_id),
                                            spec=repo.get("characteristics", characteristic_id),
                                            reason="guest" if reason == "guest" else original.reason,
                                            demand=demand.get(characteristic_id, 0))
                original = original.model_copy(update={"text": refreshed.text, "hypothesis": refreshed.hypothesis,
                                                       "hypothesis_update": refreshed.hypothesis_update,
                                                       "reason": refreshed.reason, "priority": refreshed.priority,
                                                       "demand_estimate": refreshed.demand_estimate, "demand_synthetic": False})
                scopes = original.requested_scopes
                original.requested_scopes = []
                for requested in scopes:
                    original, _ = _with_requested_scope(original, requested)
            elif original.reason != "conflict":
                original = _refresh_night_hypothesis(original, repo.find("facts", hotel_id=hotel_id))
            if reason == "guest" and original.reason != "guest":
                original = original.model_copy(update={"reason": "guest", "text": "Гость просит проверить сведения. " + original.text})
            question, _ = _with_requested_scope(original, scope)
            if question.model_dump(mode="json") != pending:
                repo.upsert("questions", question.model_dump(mode="json"))
            return question
        question = _build_question(hotel_id, characteristic_id, repo.find("facts", hotel_id=hotel_id), reason=reason,
                                   spec=repo.get("characteristics", characteristic_id),
                                   ordinal=len(previous), demand=demand.get(characteristic_id, 0))
        question, _ = _with_requested_scope(question, scope)
        try:
            repo.insert("questions", question.model_dump(mode="json"))
        except UniqueViolation:
            winner = Question.model_validate(repo.get("questions", question.id))
            if winner.status != "pending":
                return create_question(hotel_id, characteristic_id, repo, reason=reason, demand=demand, scope=scope)
            question, changed = _with_requested_scope(winner, scope)
            if changed:
                repo.upsert("questions", question.model_dump(mode="json"))
        return question


def generate_questions(hotel_id: str, repo) -> list[Question]:
    from backend.agents.demand import search_demand
    demand = search_demand(repo, hotel_id)["shown_with"]
    facts = repo.find("facts", hotel_id=hotel_id)
    specs = [spec for spec in repo.all("characteristics") if spec.get("hotelier_answerable", True) and spec.get("status", "active") == "active"]
    # Reopening an answered question is an explicit correction intent, even
    # when the existing fact is complete. A read must not expire that form.
    answered_ids = {answer["question_id"] for answer in repo.all("answers") if answer.get("confirmed")}
    by_cid = {}
    for spec in specs:
        cid = spec["id"]
        related = [fact for fact in facts if fact["characteristic_id"] == cid]
        # A reliable observation of one facade must not hide an unresolved
        # different facade, floor, month or source conflict.
        complete = bool(related) and all(_usable(fact) for fact in related)
        previous = repo.find("questions", hotel_id=hotel_id)
        explicit = any(cid in q.get("characteristic_ids", []) and q.get("status") == "pending"
                       and (q.get("reason") in {"guest", "conflict"} or q["id"] in answered_ids) for q in previous)
        if complete and not explicit:
            for question in previous:
                if cid in question.get("characteristic_ids", []) and question.get("status") == "pending":
                    repo.upsert("questions", {**question, "status": "expired"})
            continue
        if not explicit and any(cid in q.get("characteristic_ids", []) and q.get("status") == "answered" for q in previous):
            continue
        reason = "disputed" if any(fact.get("status") == "disputed" for fact in related) else "low_confidence" if any(fact.get("value") is not None for fact in related) else "unknown"
        by_cid[cid] = create_question(hotel_id, cid, repo, reason=reason, demand=demand)
    by_id = {question.id: question for question in by_cid.values()}
    for value in repo.find("questions", hotel_id=hotel_id):
        if value.get("status") == "pending":
            question = Question.model_validate(value)
            by_id[question.id] = question
    return sorted(by_id.values(), key=lambda question: (question.demand_synthetic, -question.demand_estimate,
                                                       -question.priority, question.id))


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
            fallback_label = _label(repo.get("characteristics", conflict.characteristic_id))
            label, target = VIEW_LABELS.get(conflict.characteristic_id, (fallback_label, fallback_label))
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
