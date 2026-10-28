"""Russian explanations assembled only from resolved facts and their evidence."""
from __future__ import annotations

import math

from backend.agents.guest_text import (
    beach_name,
    criterion_label,
    field_unit,
    guest_limitation,
    number,
    rounded_minutes,
)
from backend.agents.query_match import (
    FLOOR_DOMAIN_CAVEAT,
    FLOOR_DOMAIN_UNKNOWN,
    ROOM_CHARACTERISTICS,
    Assessment,
    Match,
    assess_hotel,
    fresh,
    scope_values,
    sunset_aspect,
)
from backend.contracts import CharacteristicSpec, Criterion, CriterionVerdict, Fact, Hotel, ParsedQuery

SIDES = {"N": "северная", "NE": "северо-восточная", "E": "восточная", "SE": "юго-восточная", "S": "южная", "SW": "юго-западная", "W": "западная", "NW": "северо-западная"}


def approximate(value, *, minutes=False) -> str:
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        return number(value)
    rounded = rounded_minutes(value) if minutes else math.floor(value + .5)
    return f"≈ {rounded}"


def scope_text(fact: Fact) -> str:
    scope = scope_values(fact)
    parts = []
    if scope.get("facade"):
        parts.append(SIDES[scope["facade"]] + " сторона")
    low, high = scope.get("floor_min"), scope.get("floor_max")
    if low and high:
        parts.append(f"{low}-й этаж" if low == high else f"{low}–{high}-й этажи")
    elif low:
        parts.append(f"с {low}-го этажа")
    elif high:
        parts.append(f"до {high}-го этажа")
    if scope.get("room_type"):
        parts.append("тип номера «" + scope["room_type"] + "»")
    return ", ".join(parts)


def _route_detail(route, destination: str) -> str:
    if not isinstance(route, dict):
        return destination + ': пеший маршрут не подтверждён'
    if route.get('minutes') is not None:
        result = destination + ' ' + approximate(route['minutes'], minutes=True) + ' мин пешком'
        if route.get('distance_m') is not None:
            result += ' (' + approximate(route['distance_m']) + ' м)'
        return result
    return destination + ': пеший маршрут не подтверждён'


def _tz_details(fact: Fact, criterion: Criterion) -> str | None:
    value = fact.value if isinstance(fact.value, dict) else {}
    cid, field = fact.characteristic_id, criterion.field or 'available'
    if cid == 'airport_noise':
        distance = value.get('runway_distance_m')
        detail = ('до взлётной полосы ' + approximate(distance) + ' м по карте') if distance is not None else 'расстояние до полосы неизвестно'
        if value.get('in_corridor') is True:
            detail += '; отель под продолжением оси полосы'
        return detail + '. Слышимость самолётов и ночные рейсы неизвестны'
    if cid == 'airport_transfer':
        transfers = value.get('transfers')
        if transfers is not None:
            detail = 'по карте есть связь ' + ('без пересадок' if transfers == 0 else 'с одной пересадкой' if transfers == 1 else f'с {number(transfers)} пересадками')
        else:
            reasons = {
                'hotel_stops_absent_from_relations': 'остановки у отеля не связаны с маршрутами в используемой карте',
                'no_mapped_zero_or_one_transfer_join': 'связь с нулём или одной пересадкой по карте не установлена',
                'hotel_stop_directed_walk_not_established': 'пеший подход от остановки к отелю не подтверждён',
                'hotel_ordered_members_missing': 'в карте не хватает порядка остановок маршрута',
                'hotel_stop_snap_missing_or_walk_disconnected': 'пеший граф у остановки не связан с отелем',
                'hotel_stop_walk_exceeds_threshold': 'подход к отелю превышает 15 минут по принятому маршруту',
            }
            detail = reasons.get(value.get('primary_unknown_reason'), 'транспортная связь по карте не подтверждена')
        if value.get('car_distance_km') is not None:
            detail += '; на машине ' + approximate(value['car_distance_km']) + ' км по дорожному графу'
        return detail + '. Время поездки и работа ночью неизвестны: открытого расписания нет'
    if cid == 'landmark_view' or fact.scope.landmark_id:
        name = value.get('name') or 'выбранная достопримечательность'
        if value.get('available') is None:
            return str(name) + ': ' + str(value.get('unknown_reason') or 'видимость не рассчитана; нет подтверждённых входных данных')
        place = scope_text(fact)
        detail = str(name) + (': ' + place if place else '')
        if value.get('distance_m') is not None:
            detail += '; до объекта ' + approximate(value['distance_m']) + ' м'
        return detail + '. Рассчитан вид на верх контура, вид всего объекта не гарантируется'
    if cid == 'night_open':
        kind = field.split('.')[0]
        if kind in {'shop', 'food'}:
            route = value.get(kind)
            return _route_detail(route, 'до магазина' if kind == 'shop' else 'до места, где поесть') + ('; часы по карте: открыто в 02:00' if isinstance(route, dict) else '')
        return '; '.join(_route_detail(value.get(k), label) for k, label in [('shop', 'до магазина'), ('food', 'до еды')])
    if cid == 'dog_walk':
        route = value.get('dog_park')
        detail = _route_detail(route, 'до площадки для выгула')
        if not route or route.get('available') is not True:
            fallback = value.get('ordinary_park_fallback')
            if fallback:
                detail += '; ' + _route_detail(fallback, 'обычный парк') + ' — это не площадка для выгула'
        return detail
    if cid in {'veterinary_nearby', 'pet_shop_nearby'}:
        return _route_detail(value, 'до ветклиники' if cid == 'veterinary_nearby' else 'до зоомагазина')
    if cid == 'hotel_parking':
        known = value.get('private_parking')
        detail = 'своя парковка есть' if known is True else 'своей парковки нет' if known is False else 'своя парковка не подтверждена; спросите отель'
        if value.get('fee_rub_per_day') is not None:
            detail += '; ' + number(value['fee_rub_per_day']) + ' ₽/сутки'
        if value.get('reservation_required') is not None:
            detail += '; нужно бронировать' if value['reservation_required'] else '; бронирование не требуется'
        return detail
    if cid == 'car_arrival':
        distance = value.get('road_distance_m')
        return ('до разрешённой для машин дороги ' + approximate(distance) + ' м' if distance is not None else 'подъезд по карте не установлен') + '. Открытый въезд к двери нужно уточнить у отеля'
    if cid == 'mobile_masts':
        count, radius = value.get('mapped_count'), value.get('radius_m')
        return ('на карте мачт: ' + number(count) + (' в радиусе ' + number(radius) + ' м' if radius is not None else '') if count is not None else 'данных о мачтах нет') + '. Их число не подтверждает покрытие 4G/5G или скорость'
    if cid == 'construction_nearby':
        count = value.get('mapped_count')
        return ('архивных отметок строительства рядом: ' + number(count) + '. ' if count is not None else '') + 'Текущие работы и шум не подтверждены; карта может отставать от реальности'
    if cid == 'location_access':
        if field.startswith('metro.'):
            routes = [_route_detail(value.get(k), label) for k, label in [('station_route', 'до станции электрички'), ('bus_stop_route', 'до остановки')]]
            return 'в Сочи нет метро; ' + '; '.join(routes)
        route = field.split('.')[0]
        if route.endswith('_route'):
            return _route_detail(value.get(route), {'center_route': 'до центра зоны', 'station_route': 'до станции электрички', 'bus_stop_route': 'до остановки'}[route])
        district = value.get('district_name')
        return ('Центральный район Сочи по административной границе' if value.get('in_central_sochi') else 'за пределами Центрального района Сочи') + (': ' + district if district else '')
    if cid == 'climate_days':
        parts = []
        for key, label in [('sunny_days', 'солнечных дней'), ('rainy_days', 'дождливых дней')]:
            if value.get(key) is not None:
                parts.append(label + ' ' + approximate(value[key]))
        if value.get('water_c') is not None:
            parts.append('вода ' + approximate(value['water_c']) + ' °C — ' + str(value.get('water_comfort', 'комфорт индивидуален')))
        return '; '.join(parts) + '. Климат прошлых лет, не прогноз на даты поездки'
    if cid == 'mobile_internet':
        if field == 'radio_generation':
            return 'поколение мобильной сети 4G/5G не подтверждено; число мачт и тесты скорости его не определяют'
        if value.get('download_mbps') is not None:
            return 'мобильный интернет в районе ' + approximate(value['download_mbps']) + ' Мбит/с по тестам. Это не Wi-Fi отеля'
        return 'измерений мобильного интернета для этой точки нет; скорость неизвестна'
    if cid == 'photo_features':
        name = {'balcony': 'балконы', 'terrace': 'терраса', 'pool': 'бассейн'}.get(field, 'признаки здания')
        return name + (': указаны в явно связанном описании или тегах здания' if value.get(field) is True else ': не подтверждены явными метаданными; изображение само по себе не проверялось')
    if cid == 'events_nearby':
        return 'актуальный открытый источник мероприятий и перекрытий не получен; события на даты поездки неизвестны'
    if cid == 'building_history':
        parts = []
        if value.get('explicit_building_date'):
            parts.append('дата постройки по источнику: ' + str(value['explicit_building_date']))
        elif value.get('inception'):
            parts.append('дата в источнике: ' + str(value['inception']) + '; она может означать открытие отеля')
        if value.get('architecture'):
            parts.append('архитектура: ' + str(value['architecture']))
        if value.get('historic_designation') is True:
            parts.append('в источнике указан исторический статус')
        else:
            parts.append('старинность здания не подтверждена')
        return '; '.join(parts)
    if cid == 'family_nearby' and field.startswith('walk_'):
        route = field.split('.')[0]
        labels = {'walk_children_hospital': 'до детской больницы', 'walk_hospital': 'до больницы',
                  'walk_playground': 'до площадки', 'walk_promenade': 'до пешеходной улицы или набережной'}
        return _route_detail(value.get(route), labels.get(route, 'до места'))
    return None


def _details(fact: Fact, criterion: Criterion) -> str:
    from backend.agents.query_match import ROUTE_FIELDS
    route = ROUTE_FIELDS.get((fact.characteristic_id, criterion.field or 'available'))
    if route and isinstance(fact.value, dict) and route[0] in fact.value:
        label = {'walk_playground': 'до детской площадки', 'walk_hospital': 'до больницы',
                 'walk_parking': 'до публичной парковки', 'walk_ev': 'до зарядки для электромобиля'}[route[0]]
        return _route_detail(fact.value.get(route[0]), label)
    custom = _tz_details(fact, criterion)
    if custom is not None:
        return custom
    value = fact.value if isinstance(fact.value, dict) else {}
    cid = fact.characteristic_id
    details = []
    if cid in {"sea_view", "sunset_view", "mountain_view", "room_orientation", "quiet_night", "soundproofing"}:
        place = scope_text(fact)
        if place:
            details.append(place)
    if cid == "quiet_night":
        low, high = value.get("night_db_min"), value.get("night_db_max")
        if low is not None and high is not None:
            details.append(f"ночью снаружи {approximate(low)}–{approximate(high).removeprefix('≈ ')} дБ по расчёту")
    elif cid == "beach_walk":
        if value.get("minutes") is not None:
            name = beach_name(value.get("beach_name"))
            destination = "до ближайшего пляжа" if name == "ближайший пляж" else f"до пляжа «{name}»"
            details.append(f"{destination} {approximate(value['minutes'], minutes=True)} мин пешком")
        if value.get("return_minutes") is not None:
            details.append(f"обратно {approximate(value['return_minutes'], minutes=True)} мин")
        if value.get("return_ascent_m") is not None:
            details.append(f"подъём обратно {approximate(value['return_ascent_m'])} м")
        if value.get("has_steps"):
            details.append("на маршруте есть лестницы")
        elif value.get("has_steps") is False and criterion.no_stairs:
            details.append("по данным маршрута лестниц нет")
        crossings = value.get("crossings")
        if crossings:
            details.append(f"переходов на маршруте: {len(crossings) if isinstance(crossings, list) else number(crossings)}")
        elif crossings is not None and not isinstance(crossings, bool) and criterion.no_crossings:
            details.append("по данным маршрута переходов нет")
    elif cid == "sunset_view":
        aspect = sunset_aspect(fact)
        if aspect and value.get("available") is True:
            if aspect.minimum_deg > 45:
                details.append("закат сбоку")
            elif aspect.maximum_deg <= 45:
                details.append("закат перед окнами")
        if value.get("visible_minutes") is not None:
            details.append(f"перед закатом солнце видно {approximate(value['visible_minutes'], minutes=True)} мин по расчёту")
    elif cid == "climate":
        month = fact.scope.month or value.get("month")
        if month:
            details.append(f"месяц {month}")
        if value.get("water_c") is not None:
            details.append(f"вода {number(value['water_c'])} °C по климатическим данным")
        if value.get("air_c") is not None:
            details.append(f"воздух {number(value['air_c'])} °C")
    elif cid == "connectivity" and value.get("download_mbps") is not None:
        details.append(f"загрузка {number(value['download_mbps'])} Мбит/с по тестам в районе")
    elif cid == "pets":
        if value.get("max_weight_kg") is not None:
            details.append(f"питомцы до {number(value['max_weight_kg'])} кг")
        if value.get("fee_rub_night") is not None:
            details.append(f"доплата {number(value['fee_rub_night'])} ₽/ночь")
    elif cid == "soundproofing":
        if value.get("windows"):
            details.append("двойные окна" if value["windows"] == "double" else "окна: " + str(value["windows"]))
        if value.get("blackout") is not None:
            details.append("плотные шторы есть" if value["blackout"] else "плотных штор нет")
        if value.get("air_conditioning") is not None:
            details.append("кондиционер есть" if value["air_conditioning"] else "кондиционера нет")
    elif cid == "air_quality" and value.get("european_aqi") is not None:
        details.append(f"European AQI {number(value['european_aqi'])}")
    elif cid == "nightlife" and value.get("venues_500m") is not None:
        details.append(f"в 500 м заведений: {value['venues_500m']}, из них баров и клубов {value.get('bars_500m', 0)}")
        if value.get("promenade_distance_m") is not None:
            details.append(f"до пешеходной улицы или набережной {approximate(value['promenade_distance_m'])} м")
    elif cid == "night_pharmacy" and value.get("minutes") is not None:
        details.append(f"до аптеки {approximate(value['minutes'], minutes=True)} мин")
    if cid in {"family_nearby", "car_access", "night_pharmacy"} and criterion.field and value.get(criterion.field) is not None:
        labels = {"playground_distance_m": "до детской площадки", "parking_distance_m": "до парковки",
                  "ev_distance_m": "до зарядки для электромобиля", "distance_m": "до ночной аптеки"}
        if criterion.field in labels:
            details.append(f"{labels[criterion.field]} {approximate(value[criterion.field])} м")
    if not details and criterion.field and value.get(criterion.field) is not None:
        actual = value[criterion.field]
        if not isinstance(actual, dict | list | bool):
            unit = field_unit(criterion.field)
            details.append(f"{criterion_label(criterion)}: {number(actual)}" + (f" {unit}" if unit else ""))
    return "; ".join(details)


def supporting_basis(facts: list[Fact]) -> str:
    """Classify only current supporting witnesses, never the claim's wording."""
    kinds = {e.kind for f in facts if fresh(f) and f.supporting_claim_ids for e in f.evidence}
    origins = {"open_data" if kind == "quote" else kind if kind in {"calculation", "open_data", "hotelier"} else "unknown"
               for kind in kinds}
    return next(iter(origins)) if len(origins) == 1 else "mixed" if origins else "unknown"


def hotelier_confirmation(facts: list[Fact], status: str, *, conflict: bool = False) -> bool:
    """A successful condition is not itself an actual hotel's confirmation."""
    if status != "yes" or conflict or not facts or any(
        not fresh(f) or f.synthetic or f.status == "disputed"
        or any(e.kind in {"simulation", "mock"} or e.inputs.get("synthetic") is True
               or "hotelier_simulator" in e.source_ids for e in f.evidence) for f in facts
    ):
        return False
    return any(f.supporting_claim_ids and any(e.kind == "hotelier" and e.quote and e.quote.strip() for e in f.evidence)
               for f in facts)


def context_caveats(match: Match) -> list[str]:
    """Context witnesses qualify a condition without becoming its primary proof."""
    notes = []
    for fact in match.risk_facts:
        if fact.characteristic_id == 'airport_noise':
            notes.append('Рядом аэропорт или направление захода самолётов: учитываем это при оценке тишины. Слышимость самолётов и ночные рейсы неизвестны')
        elif fact.characteristic_id == 'nightlife':
            distance = fact.value.get('nearest_bar_m') if isinstance(fact.value, dict) else None
            notes.append(f'Рядом бар — {approximate(distance)} м по карте. Ночью может быть шумно; часы работы и слышимость в номере неизвестны')
        elif fact.characteristic_id == 'beach_walk':
            notes.append('Предварительная оценка по маршруту на карте: бордюры, ширина проходов и проезд у входов не проверены')
    return notes


def verdict(criterion: Criterion, match: Match, required: bool, parsed: ParsedQuery, *, conflict: bool = False,
            alternatives: list[Fact] | None = None, spec: CharacteristicSpec | None = None) -> CriterionVerdict:
    facts = list({f.id: f for f in [*match.facts, *(alternatives or [] if conflict else [])]}.values())
    selected = match.facts[0] if match.facts else None
    detail = _details(selected, criterion) if selected else ""
    prefixes = {"yes": "Да, но учитывайте условия", "partial": "Предварительно", "no": "Нет", "unknown": "Не подтверждено"}
    text = prefixes[match.status] + ": " + (detail or match.reason)
    if (selected and selected.characteristic_id == "nightlife" and match.status == "no" and criterion.op == "gte"
            and criterion.field in {"venues_500m", "bars_500m"} and isinstance(match.value, int | float)):
        text = f"Не подходит: по карте мало — {number(match.value)} из {number(criterion.value)} заведений в 500 м"
    if (selected and selected.characteristic_id == 'connectivity' and match.status == 'partial'
            and isinstance(selected.value, dict) and selected.value.get('wifi_reported') is True
            and match.value is None):
        text = 'Wi-Fi есть по карте · скорость неизвестна'
    if criterion.characteristic_id == 'connectivity' and match.reason == 'Тесты интернета в районе не подтверждают Wi-Fi отеля':
        text = match.reason
    if criterion.characteristic_id == 'accessible_route' and not match.facts and match.risk_facts:
        text = match.reason
    if (selected and selected.characteristic_id == "beach_walk" and match.status in {"no", "unknown"}
            and (criterion.no_stairs or criterion.no_crossings) and match.reason.startswith("Маршрут ")):
        text = prefixes[match.status] + ": " + match.reason + ("; " + detail if detail else "")
    caveats = context_caveats(match)
    if match.status in {"unknown", "partial", "no"}:
        caveats.append(match.reason)
    if match.reason == FLOOR_DOMAIN_UNKNOWN:
        caveats.append(FLOOR_DOMAIN_CAVEAT)
    if selected and selected.characteristic_id == "quiet_night":
        caveats.append("Приближённая модель наружного шума с погрешностью около ±8 дБ; сон и шумоизоляцию окон она не гарантирует. Уточните окна у отеля")
    if selected and selected.characteristic_id == "connectivity":
        value = selected.value if isinstance(selected.value, dict) else {}
        if value.get("wifi_reported") is True and not any(value.get(k) is not None for k in ("download_mbps", "upload_mbps", "latency_ms")):
            caveats.append("Наличие Wi-Fi указано в открытых данных; работу сети и скорость в номере не проверяли")
        else:
            caveats.append("Это агрегированные тесты в районе, а не проверка Wi-Fi конкретного номера")
    if match.status == "no" and selected and selected.confidence < .7:
        caveats.append("Условие не выполняется по имеющимся данным; данные могут быть неполными")
    if match.status == "no" and selected and selected.characteristic_id == "nightlife":
        caveats.append("Карта заведений может быть неполной")
    if selected and selected.characteristic_id == "climate":
        caveats.append("Климатические данные за прошлые периоды, не прогноз погоды на даты поездки")
    if selected and selected.characteristic_id == "beach_walk":
        caveats.append("Расчёт времени ходьбы по модели Тоблера; лестницы и переходы могут замедлить путь")
        caveats.append("Маршрут по OSM: теги лестниц и переходов могут быть неполными, а стыки графа и короткие участки у входа и пляжа не подтверждены. Отсутствие отметки не гарантирует доступность с коляской")
    if selected and selected.characteristic_id == "sunset_view":
        caveats.append("Минуты — сумма видимых интервалов перед закатом по модели, а не гарантия непрерывного вида или видимости до самого заката")
        aspect = sunset_aspect(selected)
        if aspect:
            if aspect.exact_normal:
                caveats.append(f"Угол к нормали окна {approximate(aspect.angle_deg)}° по геометрии фасада")
            else:
                caveats.append(f"Ориентировочный угол к направлению стороны {approximate(aspect.minimum_deg)}–{math.ceil(aspect.maximum_deg)}°; точная нормаль окна неизвестна")
            caveats.append(f"Сектор окна в геомодели ±{number(aspect.window_half_angle_deg)}°; угол оценивает направление взгляда, а не уверенность в расчёте")
            if not aspect.exact_normal:
                caveats.append("Для оценки угла использован компасный сектор фасада ±22,5°, точной нормали окна в evidence нет; диапазон ориентировочный")
    if any(f.synthetic or any(e.kind == "simulation" for e in f.evidence) for f in facts):
        if any(e.kind in {"simulation", "hotelier"} for f in facts for e in f.evidence):
            caveats.append("Ответ отеля · демо; реальный ответ отеля ещё нужно получить")
        else:
            caveats.append("Демо-данные; реальные условия нужно уточнить у отеля")
    if conflict:
        caveats.append("Подходящие сведения относятся к разным сторонам, этажам или типам номера. Один номер со всеми обязательными условиями не подтверждён")
    for fact in facts:
        for evidence in fact.evidence:
            caveats.extend(guest_caveat for limitation in evidence.limitations if (guest_caveat := guest_limitation(limitation)))
    return CriterionVerdict(characteristic_id=criterion.characteristic_id, label=criterion_label(criterion, spec),
                            required=required, status=match.status, symbol={"yes": "✓", "partial": "≈", "no": "✗", "unknown": "?"}[match.status],
                            text=text, confidence=match.confidence, fact_ids=[f.id for f in facts],
                            evidence_refs=list(dict.fromkeys(e.id for f in facts for e in f.evidence)),
                            context_fact_ids=list(dict.fromkeys(f.id for f in match.risk_facts)),
                            context_evidence_refs=list(dict.fromkeys(e.id for f in match.risk_facts for e in f.evidence)),
                            caveat="; ".join(dict.fromkeys(caveats)) or None,
                            hotelier_confirmed=hotelier_confirmation(match.facts, match.status, conflict=conflict),
                            basis=supporting_basis(match.facts or (match.risk_facts if criterion.characteristic_id == 'accessible_route' else [])),
                            disputed=any(f.status == "disputed" for f in facts),
                            synthetic=any(f.synthetic or any(e.kind == "simulation" for e in f.evidence) for f in facts))


def _default_query(facts: list[Fact]) -> ParsedQuery:
    # Detail pages may request a room without a search. Only propose views that
    # actually have some positive evidence, rather than asking for every view.
    labels = {"sea_view": "Вид на море", "sunset_view": "Закат из окна", "mountain_view": "Вид на горы"}
    criteria = [Criterion(characteristic_id=cid, label=label, field="available") for cid, label in labels.items()
                if any(f.characteristic_id == cid and fresh(f) and isinstance(f.value, dict) and f.value.get("available") is True for f in facts)]
    return ParsedQuery(text="", must=criteria)


def room_from_assessment(assessment: Assessment, parsed: ParsedQuery) -> str:
    relevant = assessment.matches[:len(parsed.must)] or assessment.matches
    domain_reason = assessment.domain_reason or next((m.reason for m in relevant if m.reason == FLOOR_DOMAIN_UNKNOWN), None)
    if domain_reason:
        return domain_reason + "."
    if assessment.scope_conflict:
        return "Один номер со всеми обязательными условиями не подтверждён: подходящие данные относятся к разным сторонам, этажам или типам номера. Уточните компромисс у отеля."
    if any(m.status == "no" for m in assessment.matches[:len(parsed.must)]):
        return "Номер со всеми обязательными условиями не найден в имеющихся данных. Уточните у отеля альтернативный номер или измените условие."
    supported = [f for m in relevant if m.status in {"yes", "partial"} for f in m.facts]
    scoped = [scope_values(f) for f in supported if any(scope_values(f).get(k) is not None for k in ("facade", "floor_min", "floor_max", "room_type"))]
    if not scoped:
        return "Уточните сторону, этаж и доступность конкретного номера у отеля."
    context = assessment.context
    requested = [c.scope.model_dump(exclude_none=True) for c in parsed.must if c.scope]
    low = max([s.get("floor_min", 1) for s in [*scoped, *requested]] + [parsed.min_floor or 1])
    highs = [s["floor_max"] for s in [*scoped, *requested] if s.get("floor_max")]
    if assessment.known_floors:
        highs.append(assessment.known_floors)
    high = min(highs) if highs else None
    parts = []
    if context.facade:
        parts.append(SIDES[context.facade] + " сторона")
    if high:
        parts.append(f"{low}-й этаж" if low == high else f"{low}–{high}-й этажи")
    elif low > 1:
        parts.append(f"от {low}-го этажа")
    if context.room_type:
        parts.append("тип «" + context.room_type + "»")
    if not parts:
        return "Уточните сторону и этаж конкретного номера у отеля."
    complete = bool(relevant) and all(m.status == "yes" for m in relevant)
    prefix = "Просите номер: " if complete else "Лучший вариант: "
    return prefix + ", ".join(parts) + "."


def recommend_room(facts: list[Fact], parsed: ParsedQuery | None = None, *, hotel: Hotel | None = None) -> str:
    parsed = parsed or _default_query(facts)
    return room_from_assessment(assess_hotel(facts, parsed, hotel=hotel), parsed)


def disappointments_from_assessment(facts: list[Fact], parsed: ParsedQuery, assessment: Assessment) -> list[str]:
    notes = []
    if assessment.domain_reason:
        notes.append(assessment.domain_reason + ".")
    if assessment.scope_conflict:
        notes.append("Обязательные условия найдены в разных номерах; общего сочетания стороны, этажа и типа номера нет в подтверждённых данных.")
    for criterion, match in zip([*parsed.must, *parsed.nice], assessment.matches, strict=True):
        if criterion.characteristic_id == 'quiet_night':
            notes.extend('Тишина ночью: ' + note for note in context_caveats(match))
        if match.status == "no":
            subject = 'для выбранного номера' if criterion.characteristic_id in ROOM_CHARACTERISTICS else 'по имеющимся данным'
            notes.append(criterion_label(criterion) + ": условие не выполняется " + subject + ".")
    selected = {f.id: f for m in assessment.matches for f in m.facts}
    # Route disadvantages concern the hotel, so they remain useful on detail pages.
    selected.update({f.id: f for f in facts if f.characteristic_id == "beach_walk" and fresh(f)})
    for fact in selected.values():
        value = fact.value if isinstance(fact.value, dict) else {}
        if fact.characteristic_id == "beach_walk":
            if (isinstance(value.get("return_minutes"), int | float) and not isinstance(value["return_minutes"], bool)
                    and isinstance(value.get("minutes"), int | float) and not isinstance(value["minutes"], bool)
                    and math.isfinite(value["return_minutes"]) and math.isfinite(value["minutes"])
                    and rounded_minutes(value["return_minutes"]) - rounded_minutes(value["minutes"]) >= 1):
                notes.append(f"Обратно с пляжа дольше: {approximate(value['return_minutes'], minutes=True)} мин вместо {approximate(value['minutes'], minutes=True)} мин.")
            if value.get("return_ascent_m"):
                notes.append(f"На обратном пути подъём {approximate(value['return_ascent_m'])} м.")
            if value.get("has_steps"):
                notes.append("На пути к пляжу есть лестницы.")
            if value.get("crossings"):
                notes.append("На пути к пляжу есть переходы; откройте маршрут перед выбором отеля.")
    return list(dict.fromkeys(notes))[:8]


def disappointments(facts: list[Fact], parsed: ParsedQuery | None = None, *, hotel: Hotel | None = None) -> list[str]:
    parsed = parsed or _default_query(facts)
    return disappointments_from_assessment(facts, parsed, assess_hotel(facts, parsed, hotel=hotel))
