"""Conservative Russian extraction; quotes are evidence, not measurements.

The optional LLM may propose fields, but only independently validated, verbatim
supported extractions survive. Unknowns never become negative hotel facts.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.contracts import Answer, FieldUpdate, Question, Scope

SIDES = {
    "SW": r"юго[ -]?запад\w*", "NW": r"северо[ -]?запад\w*",
    "SE": r"юго[ -]?вост\w*", "NE": r"северо[ -]?вост\w*",
    "W": r"запад\w*", "E": r"вост\w*", "N": r"север\w*", "S": r"юг\w*|южн\w*",
}
UNCERTAIN = r"(?:не\s+зна\w*|не\s+уверен\w*|возможно|наверно\w*|кажется|может\s+быть|не\s+помню|уточн\w*|неизвест\w*|не\s+провер\w*|не\s+измер\w*|нет\s+измер\w*)"
NEGATIVE = r"(?:\bне\b|\bнет\b|\bбез\b|нельзя|запрещ\w*|отсутств\w*)"
INTERNAL = {"soundproofing", "room_orientation", "pets", "quiet_night", "sea_view", "sunset_view", "mountain_view"}
HOTELIER_VALUE_FIELDS = {
    "soundproofing": {"available", "windows", "blackout", "air_conditioning"},
    "room_orientation": {"available", "facade", "min_floor", "max_floor", "rooms", "balcony", "groups", "absent_floors", "courtyard", "sea_view"},
    "pets": {"available", "species", "excluded_species", "max_weight_kg", "fee_rub_night"},
    "quiet_night": {"available", "facade"},
    "sea_view": {"available", "facade", "min_floor"},
    "sunset_view": {"available", "facade", "min_floor"},
    "mountain_view": {"available", "facade", "min_floor"},
    "beach_walk": {"available", "minutes", "distance_m", "has_steps"},
}
ROOM_GROUP_FIELDS = {"rooms", "facade", "min_floor", "max_floor", "balcony", "courtyard", "sea_view"}
MONTH_PATTERNS = (r"январ(?:ь|я|е|ю|ем)", r"феврал(?:ь|я|е|ю|ем)", r"март(?:а|е|у|ом)?", r"апрел(?:ь|я|е|ю|ем)",
                  r"ма[йяею]|маем", r"июн(?:ь|я|е|ю|ем)", r"июл(?:ь|я|е|ю|ем)", r"август(?:а|е|у|ом)?",
                  r"сентябр(?:ь|я|е|ю|ем)", r"октябр(?:ь|я|е|ю|ем)", r"ноябр(?:ь|я|е|ю|ем)", r"декабр(?:ь|я|е|ю|ем)")


class _ProposedUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    characteristic_id: str
    scope: Scope = Field(default_factory=Scope)
    value: dict
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def local_fields(self):
        allowed = HOTELIER_VALUE_FIELDS.get(self.characteristic_id)
        if not allowed or set(self.value) - allowed:
            raise ValueError("Unsupported hotelier extraction fields")
        return self


class _AnswerProposal(BaseModel):
    """No IDs, source, confirmation, dates or confidence under model control."""
    model_config = ConfigDict(extra="forbid")
    raw_text: str
    updates: list[_ProposedUpdate] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)
    needs_clarification: str | None = None

    @model_validator(mode="after")
    def quotes(self):
        if any(update.quote not in self.raw_text for update in self.updates):
            raise ValueError("Proposals require verbatim quotes")
        return self


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
    text = re.sub(r"\bперв(?:ом|ый|ого)\s+этаж", "1 этаж", text)
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


def _room_groups(raw: str) -> tuple[list[dict], list[int], list[str]]:
    """Explicit room lists are observations, never inferred floors or categories."""
    groups, absent, clarifications = [], [], []
    for sentence in _sentences(raw):
        low = sentence.lower().replace("ё", "е")
        low_floor, high_floor, invalid = _floor(low)
        if re.search(r"(?:номеров|комнат)\s+нет|нет\s+(?:номеров|комнат)", low) and low_floor and not invalid:
            absent.extend(range(low_floor, (high_floor or low_floor) + 1))
            continue
        matches = list(re.finditer(r"(?<!\d)(\d{3,4})\s*[–—-]\s*(\d{3,4})(?!\d)", low))
        if not matches or re.search(UNCERTAIN, low):
            continue
        # Separate groups in one clause need an explicit boundary. Never apply
        # the last balcony/view description to every room mentioned there.
        if len(matches) > 1 and any(re.search(r"мор|двор|балкон|выход|смотр", low[a.end():b.start()])
                                           for a, b in zip(matches, matches[1:], strict=False)):
            clarifications.append("Разделите разные группы номеров точкой или точкой с запятой и опишите каждую отдельно.")
            continue
        rooms = []
        for match in matches:
            first, last = int(match[1]), int(match[2])
            if first > last or last-first > 100:
                clarifications.append("Уточните возрастающий диапазон номеров (не более 101 номера в группе).")
                break
            rooms.extend(str(number) for number in range(first, last+1))
        else:
            group = {"rooms": list(dict.fromkeys(rooms))}
            side = _side(low)
            if side:
                group["facade"] = side
            if low_floor and not invalid:
                group["min_floor"] = low_floor
                if high_floor:
                    group["max_floor"] = high_floor
            for key, pattern in (("balcony", r"балкон"), ("courtyard", r"(?:во?\s+)?двор\w*"),
                                 ("sea_view", r"(?:на\s+)?море|вид\w*\s+(?:на\s+)?мор\w*")):
                if re.search(pattern, low):
                    value = _bool_feature(low, pattern)
                    if value is not None:
                        group[key] = value
            groups.append(group)
    for index, group in enumerate(groups):
        for other in groups[index+1:]:
            if set(group["rooms"]) & set(other["rooms"]) and any(
                    group[key] != other[key] for key in set(group) & set(other) - {"rooms"}):
                return [], sorted(set(absent)), ["Для одних и тех же номеров указаны разные условия. Уточните группы номеров отдельно."]
    return groups, sorted(set(absent)), clarifications


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


def _insulation_available(sentence: str) -> bool | None:
    if re.search(UNCERTAIN, sentence):
        return None
    feature = r"(?:шумо|звуко)изоляц\w*"
    negative_quality = r"плох\w*|слаб\w*|низк\w*|ужасн\w*|отвратительн\w*|посредственн\w*|недостаточн\w*|так\s+себе"
    for clause in re.split(r"[,;]|\bно\b|\bа\b|\bи\b", sentence):
        if not re.search(feature, clause):
            continue
        quality = re.search(negative_quality, clause)
        if quality:
            # "Not bad" is not evidence of adequate insulation either.
            if re.search(r"\bне\s+$", clause[:quality.start()]):
                return None
            adjacent = re.search(r"(?:" + negative_quality + r")\s+" + feature, clause) or re.search(
                feature + r"\s+(?:(?:очень|крайне|довольно|весьма|тут|здесь)\s+)?(?:" + negative_quality + r")", clause)
            return False if adjacent else None
    return _bool_feature(sentence, feature)


def _night_observations(sentence: str) -> list[tuple[str, bool | None]]:
    """Keep daytime statements out of the nighttime characteristic."""
    night = r"ночью|ночн\w*|по\s+ночам|\bночи\b"
    other_time = r"днем|дневн\w*|утром|утрен\w*|вечером|вечерн\w*"
    clauses = [part.strip() for part in re.split(
        r"[,;]|\bно\b|\bа\b|\bи\b|(?=\b(?:днем|ночью|утром|вечером|по\s+ночам)\b)", sentence,
        flags=re.IGNORECASE) if part.strip()]
    normalized = sentence.lower().replace("ё", "е")
    prefix_uncertain = bool(re.match(UNCERTAIN, normalized))
    has_explicit_time = bool(re.search(night + "|" + other_time, normalized))
    observations = []
    active_time = None
    for clause in clauses:
        low = clause.lower().replace("ё", "е")
        explicit_night = bool(re.search(night, low))
        explicit_other = bool(re.search(other_time, low))
        if explicit_night or explicit_other:
            active_time = "night" if explicit_night and not explicit_other else "other"
        if has_explicit_time and active_time != "night":
            continue
        values = []
        for pattern, invert in ((r"тих\w*", False), (r"шумн\w*", True)):
            if re.search(pattern, low):
                present = _bool_feature(low, pattern)
                values.append(None if present is None else not present if invert else present)
        if values:
            available = values[0] if not prefix_uncertain and None not in values and len(set(values)) == 1 else None
            observations.append((clause if explicit_night or not has_explicit_time else sentence, available))
    return observations


def _offline(question: Question, raw: str, synthetic: bool) -> Answer:
    updates: list[FieldUpdate] = []
    unknown: set[str] = set()
    clarifications: list[str] = []
    normalized = raw.lower().replace("ё", "е")
    months = {index+1 for index, pattern in enumerate(MONTH_PATTERNS) if re.search(r"\b(?:"+pattern+r")\b", normalized)}
    open_period = (r"\b(?:летом|зимой|весной|осенью|летн\w*|зимн\w*|весенн\w*|осенн\w*|сезон\w*)\b|"
                   r"\b(?:с|до|по|после|начиная\s+с)\s+(?:(?:начала|конца|середины)\s+)?(?:"+"|".join(MONTH_PATTERNS)+r")\b|"
                   r"\b(?:в|во)\s+(?:(?:первой|второй)\s+)?(?:начале|конце|середине|половине)\s+(?:"+"|".join(MONTH_PATTERNS)+r")\b|"
                   r"\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?(?!\d)(?!\s*(?:кг|килограмм\w*|руб\w*|₽|мин\w*|метр\w*|м\b))|"
                   r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}-?м\s+месяц\w*")
    restricted_period = len(months) > 1 or bool(re.search(open_period, normalized))
    pets_partial = restricted_period and "pets" in question.characteristic_ids
    if restricted_period and not pets_partial:
        return Answer(question_id=question.id, raw_text=raw, synthetic=synthetic,
                      disposition="clarification",
                      unknown_fields=question.characteristic_ids,
                      needs_clarification="Уточните один календарный месяц и отдельный ответ для него; период не будет превращён в утверждение на весь год.")
    month = None if pets_partial else next(iter(months), None)
    grouped_rooms, absent_floors, group_clarifications = _room_groups(raw)
    use_groups = len(grouped_rooms) > 1 or bool(absent_floors) or bool(group_clarifications)
    clarifications.extend(group_clarifications)
    if pets_partial:
        unknown.add("pets.available")
        clarifications.append("Поняли ограничения по питомцам. Разрешение по сезонам пока не сохранено: в какие месяцы можно заселиться с животным? Укажите один месяц для отдельного ответа.")

    def add(cid: str, value: dict, quote: str, scope: Scope | None = None):
        if pets_partial and cid != "pets":
            unknown.add(cid)
            return
        scope = scope or Scope()
        if month is not None:
            scope = scope.model_copy(update={"month": month})
            quote = raw  # The evidence quote must include the explicit month.
        prior = next((u for u in updates if u.characteristic_id == cid and u.scope == scope), None)
        if prior:
            contradictory = [key for key in value if key in prior.value and prior.value[key] != value[key]
                             and prior.value[key] is not None and value[key] is not None]
            if contradictory:
                updates.remove(prior)
                unknown.add(cid)
                clarifications.append("Не понял разные значения для одной и той же области. Уточните, к каким номерам и условиям относится каждое значение.")
                return
            # A later partial amenity sentence must not erase an explicit
            # insulation statement with its default available=null.
            prior.value.update({key: item for key, item in value.items() if item is not None or key not in prior.value})
            prior.quote = raw
        elif cid not in unknown:
            updates.append(FieldUpdate(characteristic_id=cid, scope=scope, value=value, quote=quote))

    quick = normalized.strip().strip(".! ")
    if quick in {"не знаю", "затрудняюсь ответить", "неизвестно"}:
        return Answer(question_id=question.id, raw_text=raw, synthetic=synthetic, disposition="unknown",
                      unknown_fields=question.characteristic_ids)
    if quick == "частично":
        return Answer(question_id=question.id, raw_text=raw, synthetic=synthetic,
                      disposition="clarification",
                      unknown_fields=question.characteristic_ids,
                      needs_clarification="Уточните конкретные номера, условия и ограничения; неизвестное останется неизвестным.")
    if quick in {"да", "нет"}:
        boolean_ids = {"pets", "sea_view", "sunset_view", "mountain_view", "quiet_night", "soundproofing"}
        if (len(question.characteristic_ids) == 1 and question.characteristic_ids[0] in boolean_ids
                and not question.requested_scopes and (quick == "нет" or (not question.hypothesis and question.characteristic_ids[0] != "soundproofing"))):
            add(question.characteristic_ids[0], {"available": quick == "да"}, raw)
        else:
            unknown.update(question.characteristic_ids)
            clarifications.append("Уточните, какие именно свойства, номера и ограничения вы подтверждаете.")

    for sentence in _sentences(raw):
        low = sentence.lower().replace("ё", "е")
        side = _side(low)
        low_floor, high_floor, invalid_floor = _floor(low)
        scope = Scope(type="floor_range" if low_floor else "facade", facade=side,
                      floor_min=low_floor, floor_max=high_floor) if side and not invalid_floor else Scope(
                          floor_min=low_floor, floor_max=high_floor) if not invalid_floor else Scope()
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
        if use_groups:
            orientation = {}
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
            sound["available"] = _insulation_available(low)
            if sound["available"] is None:
                unknown.add("soundproofing.available")
        if sound and any(value is not None for value in sound.values()):
            # Double glazing is a window description, never proof of measured insulation.
            sound.setdefault("available", None)
            add("soundproofing", sound, sentence, scope)
        elif sound:
            unknown.add("soundproofing")

        animal_pattern = r"питом|животн|собак|кош[екк]|кошк|псов|\bпес\b"
        pet_text = low
        if pets_partial:
            # Only extract the independent clauses before a seasonal qualifier.
            # Their availability stays null: this is not year-round permission.
            pet_text = re.split(r",?\s*\b(?:в|во|с|до|по|летом|зимой|весной|осенью)\s+(?:" + "|".join(MONTH_PATTERNS) + r")\b", low)[0]
        if re.search(animal_pattern, pet_text) or ("pets" in question.characteristic_ids and re.search(r"доплат|бесплат", pet_text)):
            low = pet_text
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
            if denied:
                pets["excluded_species"] = sorted(set(denied))
            if pets_partial or not re.search(animal_pattern, low):
                pets["available"] = None
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
                if not denied and not pets_partial:
                    pets["available"] = True
            add("pets", pets, sentence)

        for quote, available in _night_observations(sentence):
            if available is None:
                unknown.add("quiet_night")
                continue
            night_text = quote.lower().replace("ё", "е")
            night_side = _side(night_text)
            night_scope = Scope(type="facade", facade=night_side) if night_side else Scope()
            value = {"available": available}
            if night_side:
                value["facade"] = night_side
            add("quiet_night", value, quote, night_scope)

        for cid, pattern in (("sea_view", r"вид\w*\s+(?:на\s+)?мор|море\s+вид"), ("sunset_view", r"закат"), ("mountain_view", r"вид\w*\s+(?:на\s+)?гор")):
            if re.search(pattern, low):
                if re.search(UNCERTAIN, low):
                    unknown.add(cid)
                    continue
                available = _bool_feature(low, pattern)
                if available is None:
                    unknown.add(cid)
                    continue
                value = {"available": available}
                if side:
                    value["facade"] = side
                if low_floor and cid != "quiet_night":
                    value["min_floor"] = low_floor
                add(cid, value, sentence, scope)

        if "beach_walk" in question.characteristic_ids and re.search(r"пляж|мор[еяю]", low):
            route = {}
            minutes = re.search(r"(\d{1,3})\s*мин", low)
            metres = re.search(r"(\d{2,5})\s*(?:м\b|метр)", low)
            if minutes:
                route["minutes"] = float(minutes[1])
            if metres:
                route["distance_m"] = float(metres[1])
            if re.search(r"лестниц|ступен", low):
                steps = _bool_feature(low, r"лестниц|ступен")
                if steps is not None:
                    route["has_steps"] = steps
            if route and not re.search(UNCERTAIN, low):
                route["available"] = True
                add("beach_walk", route, sentence)
            elif route:
                unknown.add("beach_walk")

    if use_groups and (grouped_rooms or absent_floors):
        # A structured list describes only named groups. Null at the outer level
        # prevents their balcony/view properties becoming a hotel-wide fact.
        value = {"available": None, "groups": grouped_rooms}
        if absent_floors:
            value["absent_floors"] = absent_floors
        add("room_orientation", value, raw)
    represented = {update.characteristic_id for update in updates}
    unknown.update(set(question.characteristic_ids)-represented)
    if unknown and not clarifications:
        clarifications.append("Уточните неизвестные свойства и конкретные номера; пропуски не означают отсутствие услуги.")
    return Answer(question_id=question.id, raw_text=raw, updates=updates, unknown_fields=sorted(unknown),
                  disposition="parsed" if updates else "clarification",
                  needs_clarification=" ".join(dict.fromkeys(clarifications)) or None, synthetic=synthetic)


def parse_answer(question: Question, text: str, *, synthetic: bool = False, llm=None) -> Answer:
    if not text.strip() or len(text) > 10000:
        raise ValueError("Ответ должен содержать от 1 до 10000 символов")
    fallback = _offline(question, text, synthetic)
    if llm is None:
        return fallback
    proposal_fallback = {"raw_text": fallback.raw_text, "unknown_fields": fallback.unknown_fields,
                         "needs_clarification": fallback.needs_clarification,
                         "updates": [update.model_dump(exclude={"confidence"}) for update in fallback.updates]}
    _proposed, mode = llm.structured(_AnswerProposal, [{"role": "user", "content":
        "Разбери ответ отельера на русском. Точные цитаты из raw_text обязательны. Не выводи шумоизоляцию из двойных окон. "
        "Не выдумывай измерения, этаж из номера комнаты или стоимость за ночь из разового сбора. "
        "Неизвестное и отрицания сохраняй. question=" + question.model_dump_json() + "\nraw_text=" + text}],
        fallback=lambda: proposal_fallback, task="hotelier_answer", budget_s=2.4)
    # Live proposals cannot bypass the conservative evidence/negation/range gate.
    # The deterministic extraction is the authority for factual fields; the model
    # cannot supply authority, a confirmation, confidence or free-form instructions
    # in the clarification channel. Clarifications also come from the local gate.
    fallback.parser = mode
    return fallback
