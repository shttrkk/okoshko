"""Conservative Russian extraction; quotes are evidence, not measurements.

The optional LLM may propose fields, but only independently validated, verbatim
supported extractions survive. Unknowns never become negative hotel facts.
"""
from __future__ import annotations

import re

from backend.contracts import Answer, FieldUpdate, Question, Scope

SIDES = {
    "SW": r"юго[ -]?запад\w*", "NW": r"северо[ -]?запад\w*",
    "SE": r"юго[ -]?вост\w*", "NE": r"северо[ -]?вост\w*",
    "W": r"запад\w*", "E": r"вост\w*", "N": r"север\w*", "S": r"юг\w*|южн\w*",
}
UNCERTAIN = r"(?:не\s+зна\w*|не\s+уверен\w*|возможно|наверно\w*|кажется|может\s+быть|не\s+помню|уточн\w*|неизвест\w*|не\s+провер\w*|не\s+измер\w*|нет\s+измер\w*)"
NEGATIVE = r"(?:\bне\b|\bнет\b|\bбез\b|нельзя|запрещ\w*|отсутств\w*)"
INTERNAL = {"soundproofing", "room_orientation", "pets", "quiet_night", "sea_view", "sunset_view", "mountain_view"}


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;!?\n]+|(?<!\d)\.|\.(?!\d)", text) if part.strip()]


def _side(text: str) -> str | None:
    # Compounds must be consumed before their component directions.
    found = []
    rest = text
    for side, pattern in SIDES.items():
        if re.search(pattern, rest):
            found.append(side)
            rest = re.sub(pattern, "", rest)
    return found[0] if len(found) == 1 else None


def _floor(text: str) -> tuple[int | None, int | None, bool]:
    match = re.search(r"(?:с|от)\s*(\d{1,3})\s*(?:-?(?:го|ого))?\s*этаж", text)
    if match:
        floor = int(match[1])
        return (floor, None, not 1 <= floor <= 100)
    match = re.search(r"(?:на\s*)?(\d{1,3})\s*[–—-]\s*(\d{1,3})\s*(?:-?(?:м|х|й))?\s*этаж", text)
    if match:
        low, high = int(match[1]), int(match[2])
        return low, high, not 1 <= low <= high <= 100
    match = re.search(r"(?:на\s*)?(\d{1,3})\s*(?:-?(?:м|й|ом))?\s*этаж", text)
    if match:
        floor = int(match[1])
        return floor, floor, not 1 <= floor <= 100
    return None, None, False


def _bool_feature(sentence: str, pattern: str) -> bool | None:
    """Read local negation, refusing uncertainty and contradictory mentions."""
    if re.match(r"\s*" + UNCERTAIN, sentence):
        return None
    values = []
    # Commas separate independent amenities: "blackout нет, кондиционер есть".
    for clause in re.split(r"[,;]|\bно\b|\bа\b|\bи\b", sentence):
        if not re.search(pattern, clause):
            continue
        if re.search(UNCERTAIN, clause):
            return None
        values.append(not bool(re.search(NEGATIVE, clause)))
    return values[0] if values and len(set(values)) == 1 else None


def _offline(question: Question, raw: str, synthetic: bool) -> Answer:
    updates: list[FieldUpdate] = []
    unknown: set[str] = set()
    clarifications: list[str] = []

    def add(cid: str, value: dict, quote: str, scope: Scope | None = None):
        scope = scope or Scope()
        prior = next((u for u in updates if u.characteristic_id == cid and u.scope == scope), None)
        if prior:
            contradictory = [key for key in value if key in prior.value and prior.value[key] != value[key]
                             and prior.value[key] is not None and value[key] is not None]
            if contradictory:
                updates.remove(prior)
                unknown.add(cid)
                clarifications.append(f"Уточните противоречивые значения: {cid} ({', '.join(contradictory)}).")
                return
            prior.value.update(value)
            prior.quote = raw
        elif cid not in unknown:
            updates.append(FieldUpdate(characteristic_id=cid, scope=scope, value=value, quote=quote))

    normalized = raw.lower().replace("ё", "е")
    quick = normalized.strip().strip(".! ")
    if quick in {"не знаю", "частично", "затрудняюсь ответить"}:
        return Answer(question_id=question.id, raw_text=raw, synthetic=synthetic,
                      unknown_fields=question.characteristic_ids,
                      needs_clarification="Уточните конкретные номера, условия и ограничения; неизвестное останется неизвестным.")
    if quick in {"да", "нет"}:
        if len(question.characteristic_ids) == 1 and question.characteristic_ids[0] in {"pets", "sea_view", "sunset_view", "mountain_view", "quiet_night"} and not question.hypothesis:
            add(question.characteristic_ids[0], {"available": quick == "да"}, raw)
        else:
            unknown.update(question.characteristic_ids)
            clarifications.append("Уточните, какие именно свойства, номера и ограничения вы подтверждаете.")

    for sentence in _sentences(raw):
        low = sentence.lower().replace("ё", "е")
        side = _side(low)
        low_floor, high_floor, invalid_floor = _floor(low)
        scope = Scope(type="floor_range" if low_floor else "facade", facade=side,
                      floor_min=low_floor, floor_max=high_floor) if side and not invalid_floor else Scope()
        if invalid_floor:
            unknown.add("room_orientation")
            clarifications.append("Укажите этажи от 1 до 100 и возрастающий диапазон.")

        # Split opposite facades rather than claiming one room faces both ways.
        direction_mentions = sum(bool(re.search(pattern, low)) for pattern in SIDES.values())
        if not side and direction_mentions:
            unknown.add("room_orientation")
            clarifications.append("Укажите сторону и номера отдельно для каждого фасада.")
        room_match = re.search(r"(?<!\d)(\d{3,4})\s*[–—-]\s*(\d{3,4})(?!\d)", low)
        orientation = {}
        if side:
            denied_orientation = bool(re.search(r"(?:номеров|комнат)\s+нет|не\s+выход\w*|нет\s+номер|не\s+на\s+", low))
            orientation.update(available=not denied_orientation, facade=side)
        if low_floor and not invalid_floor:
            orientation.update(min_floor=low_floor)
            if high_floor:
                orientation["max_floor"] = high_floor
        if room_match:
            first, last = int(room_match[1]), int(room_match[2])
            if first <= last and last-first <= 100:
                orientation["rooms"] = [str(number) for number in range(first, last+1)]
            else:
                unknown.add("room_orientation")
                clarifications.append("Уточните возрастающий диапазон номеров (не более 101 номера за ответ).")
        if re.search(r"балкон", low):
            balcony = _bool_feature(low, r"балкон")
            if balcony is not None:
                orientation["balcony"] = balcony
        if orientation and not re.search(UNCERTAIN, low) and not invalid_floor:
            if "available" not in orientation:
                orientation["available"] = True
            add("room_orientation", orientation, sentence, scope)
        elif orientation:
            unknown.add("room_orientation")

        sound = {}
        window_clauses = [clause for clause in low.split(",") if re.search(r"окн|стеклопакет", clause)]
        for clause in window_clauses:
            if re.search(UNCERTAIN, clause):
                unknown.add("soundproofing.windows")
                continue
            if re.search(r"(?:двойн\w*|двухкамер\w*|двухслойн\w*)", clause):
                sound["windows"] = "double" if not re.search(r"не\s+(?:двойн|двухкамер|двухслойн)", clause) else None
                if sound["windows"] is None:
                    unknown.add("soundproofing.windows")
            elif re.search(r"одинар\w*|однокамер\w*", clause):
                sound["windows"] = "single"
        for key, pattern in (("blackout", r"blackout|бл[эе]каут|плотн\w*\s+штор"), ("air_conditioning", r"кондиционер|\bac\b")):
            if re.search(pattern, low):
                sound[key] = _bool_feature(low, pattern)
                if sound[key] is None:
                    unknown.add("soundproofing."+key)
        if re.search(r"шумоизоляц|звукоизоляц", low):
            sound["available"] = _bool_feature(low, r"шумоизоляц|звукоизоляц")
        if sound and any(value is not None for value in sound.values()):
            # Double glazing is a window description, never proof of measured insulation.
            sound.setdefault("available", None)
            add("soundproofing", sound, sentence, scope)
        elif sound:
            unknown.add("soundproofing")

        if re.search(r"питом|животн|собак|кош[екк]|кошк|псов|\bпес\b", low):
            if re.search(UNCERTAIN, low):
                unknown.add("pets")
                continue
            species = []
            allowed, denied = [], []
            for clause in re.split(r"[,;]|\bно\b|\bа\b", low):
                animal = [name for name, pattern in (("dog", r"собак|псов|\bпес\b"), ("cat", r"кош[екк]|кошк")) if re.search(pattern, clause)]
                if animal:
                    if re.search(NEGATIVE, clause):
                        denied.extend(animal)
                    else:
                        allowed.extend(animal)
            if set(allowed) & set(denied):
                unknown.add("pets")
                clarifications.append("Уточните, какие виды животных разрешены.")
                continue
            species = sorted(set(allowed))
            pets = {"available": bool(allowed) if allowed or denied else not bool(re.search(NEGATIVE, low))}
            if species:
                pets["species"] = species
            weight = re.search(r"до\s*(-?\d+(?:[.,]\d+)?)\s*кг", low)
            if weight:
                kg = float(weight[1].replace(",", "."))
                if 0 < kg <= 100:
                    pets["max_weight_kg"] = kg
                else:
                    clarifications.append("Укажите допустимый вес питомца больше 0 и не более 100 кг.")
                    unknown.add("pets.max_weight_kg")
            money = re.search(r"(-?\d[\d ]*(?:[.,]\d+)?)\s*(?:₽|руб\w*|р\.)", low)
            if money:
                fee = float(money[1].replace(" ", "").replace(",", "."))
                if 0 <= fee <= 100000 and re.search(r"ноч|сут|/\s*(?:день|дн)|в\s+день", low):
                    pets["fee_rub_night"] = fee
                else:
                    unknown.add("pets.fee_rub_night")
                    clarifications.append("Уточните доплату в рублях за ночь (без залога и разового сбора).")
            if re.search(r"без\s+доплат|бесплат", low):
                pets["fee_rub_night"] = 0
                # "без доплаты" does not mean pets are prohibited.
                if not denied:
                    pets["available"] = True
            add("pets", pets, sentence)

        for cid, pattern in (("quiet_night", r"тих\w*|шумн\w*"), ("sea_view", r"вид\w*\s+(?:на\s+)?мор|море\s+вид"), ("sunset_view", r"закат"), ("mountain_view", r"вид\w*\s+(?:на\s+)?гор")):
            if re.search(pattern, low):
                if re.search(UNCERTAIN, low):
                    unknown.add(cid)
                    continue
                available = _bool_feature(low, pattern)
                if available is None:
                    unknown.add(cid)
                    continue
                if cid == "quiet_night" and re.search(r"шумн", low):
                    available = not available
                value = {"available": available}
                if side:
                    value["facade"] = side
                if low_floor and cid != "quiet_night":
                    value["min_floor"] = low_floor
                add(cid, value, sentence, scope)

    represented = {update.characteristic_id for update in updates}
    unknown.update(set(question.characteristic_ids)-represented)
    if unknown and not clarifications:
        clarifications.append("Уточните неизвестные свойства и конкретные номера; пропуски не означают отсутствие услуги.")
    return Answer(question_id=question.id, raw_text=raw, updates=updates, unknown_fields=sorted(unknown),
                  needs_clarification=" ".join(dict.fromkeys(clarifications)) or None, synthetic=synthetic)


def parse_answer(question: Question, text: str, *, synthetic: bool = False, llm=None) -> Answer:
    if not text.strip() or len(text) > 10000:
        raise ValueError("Ответ должен содержать от 1 до 10000 символов")
    fallback = _offline(question, text, synthetic)
    if llm is None:
        return fallback
    proposed, mode = llm.structured(Answer, [{"role": "user", "content":
        "Разбери ответ отельера на русском. Точные цитаты из raw_text обязательны. Не выводи шумоизоляцию из двойных окон. "
        "Не выдумывай измерения, этаж из номера комнаты или стоимость за ночь из разового сбора. "
        "Неизвестное и отрицания сохраняй. question=" + question.model_dump_json() + "\nraw_text=" + text}],
        fallback=lambda: fallback, task="hotelier_answer", budget_s=2.4)
    # Live proposals cannot bypass the conservative evidence/negation/range gate.
    # The deterministic extraction is the authority for factual fields; the model
    # may improve the clarification wording, never invent a new hotel assertion.
    if proposed.raw_text == text and proposed.needs_clarification and fallback.needs_clarification:
        fallback.needs_clarification = proposed.needs_clarification
    fallback.parser = mode
    return fallback
