"""Whitelisted local tools. Plans cannot execute code or arbitrary network requests."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import transform
from shapely.strtree import STRtree

from backend.contracts import Claim, Evidence, ToolCall, ToolContext, ToolResult, ToolSpec, uid
from backend.contracts.validation import validate_value as validate
from backend.settings import DATA
from backend.tools.derive import MissingInput, evaluate_expression
from geo.coordinates import TO_UTM
from geo.io import exists, read_json

# Explicit straight-line thresholds behind derived "available" flags; the same
# numbers appear in search criteria labels, so the guest sees what "рядом" means.
PARKING_NEAR_M = 500
EV_CHARGING_NEAR_M = 1000
POI_METHOD = "local_poi_lookup@2"
# A kind's method version changes when its value semantics change; older claims
# for the same hotel and characteristic are superseded, never edited.
POI_METHODS = {"car": "local_poi_lookup@3", "nightlife": "local_poi_nightlife@1"}
POI_CATEGORY_METHOD = "local_poi_category@1"
# "В гуще событий": bars, pubs, clubs, cafes and restaurants within 500 m.
NIGHTLIFE_RADIUS_M = 500
NIGHTLIFE_MIN_VENUES = 30
NIGHTLIFE_VENUES = {"bar", "pub", "nightclub", "biergarten", "cafe", "restaurant"}
NIGHTLIFE_BARS = {"bar", "pub", "nightclub", "biergarten"}


def _promenade(properties: dict) -> bool:
    return properties.get("highway") == "pedestrian" or "набережн" in str(properties.get("name") or "").lower()


# Generic OSM categories for declarative "<place> nearby" characteristics:
# id -> (Russian label, local dataset in data/seed, tag predicate).
POI_CATEGORIES = {
    "supermarket": ("Супермаркет или продукты", "pois", lambda p: p.get("shop") in {"supermarket", "convenience", "greengrocer"}),
    "pharmacy": ("Аптека", "pois", lambda p: p.get("amenity") == "pharmacy"),
    "cafe": ("Кафе", "pois", lambda p: p.get("amenity") == "cafe"),
    "restaurant": ("Ресторан", "pois", lambda p: p.get("amenity") == "restaurant"),
    "bar": ("Бар, паб или клуб", "pois", lambda p: p.get("amenity") in NIGHTLIFE_BARS),
    "bus_stop": ("Остановка общественного транспорта", "pois",
                 lambda p: p.get("highway") == "bus_stop" or p.get("public_transport") in {"platform", "stop_position", "station"}
                 or p.get("railway") in {"station", "halt", "tram_stop"}),
    "park": ("Парк или сквер", "pois", lambda p: p.get("leisure") in {"park", "garden"}),
    "atm": ("Банкомат", "pois", lambda p: p.get("amenity") == "atm" or p.get("atm") == "yes"),
    "playground": ("Детская площадка", "pois", lambda p: p.get("leisure") == "playground"),
    "fitness": ("Спортзал или фитнес-клуб", "pois", lambda p: p.get("leisure") in {"fitness_centre", "sports_centre"} or p.get("sport") == "fitness"),
    "marketplace": ("Рынок", "pois", lambda p: p.get("amenity") == "marketplace"),
    "beach": ("Пляж", "beaches", lambda p: True),
    "promenade": ("Набережная или пешеходная улица", "roads", _promenade),
    "rental": ("Прокат велосипедов, самокатов или авто", "pois",
               lambda p: p.get("amenity") in {"bicycle_rental", "car_rental", "boat_rental", "scooter_rental"} or p.get("shop") == "rental"),
    "fuel": ("Заправка", "pois", lambda p: p.get("amenity") == "fuel"),
}


def poi_method(kind_or_params) -> str:
    if isinstance(kind_or_params, dict):
        if kind_or_params.get("category"):
            return POI_CATEGORY_METHOD
        kind_or_params = kind_or_params.get("kind", "pharmacy")
    return POI_METHODS.get(kind_or_params, POI_METHOD)


@lru_cache(maxsize=8)
def feature_index(data_dir: str, name: str):
    path = Path(data_dir)/"seed"/(name+".geojson")
    if not exists(path):
        return [], [], None
    features = read_json(path).get("features", [])
    geometries, selected = [], []
    for feature in features:
        try:
            geometry = shape(feature["geometry"])
            if not geometry.is_empty:
                geometries.append(transform(TO_UTM.transform, geometry))
                selected.append(feature)
        except (ValueError, TypeError):
            continue
    return selected, geometries, STRtree(geometries)


class Registry:
    def __init__(self, repo, data_dir: Path = DATA):
        self.repo, self.data_dir = repo, Path(data_dir)
        self._hotels = {}
        self._known_claims = None
        self.specs = {}
        self.handlers = {}
        schema = {"type":"object", "properties":{"target":{"type":"string","enum":["sea","mountains","landmark","beach"]},
                  "kind":{"type":"string","enum":["pharmacy","family","car","nightlife"]},"radius_m":{"type":"number","minimum":50,"maximum":5000},
                  "category":{"type":"string","enum":sorted(POI_CATEGORIES)},"near_m":{"type":"number","minimum":50,"maximum":5000},
                  "open_at":{"type":"string","enum":["02:00"]},"expr":{"type":"string","maxLength":2000}},"additionalProperties":False}
        for id, description in {
            "viewshed_facade":"Лучевая видимость по DSM, фасаду и этажу, Monte Carlo и список препятствий",
            "sun_window":"Положение солнца и профиль горизонта по месяцам",
            "noise_facade":"Приближённая модель наружного шума по фасадам",
            "walk_route":"Реальный направленный пешеходный граф, Тоблер, подъёмы и переходы",
            "poi_nearby":"Локальные OSM POI и ограничения покрытия",
            "climate":"Заранее собранный архив Open-Meteo воздуха и моря",
            "air_quality":"Сохранённая модельная оценка CAMS/Open-Meteo",
            "connectivity":"Локальные реальные tiles Ookla",
            "wikidata_lookup":"Проверенная точная связь здания с Wikidata",
            "photos_nearby":"Геофото Commons с автором и лицензией",
            "hotelier_ask":"Создать вопрос по неизвестным внутренним свойствам, не угадывать ответ",
            "derive":"Безопасное логическое выражение над известными фактами",
        }.items():
            self.specs[id] = ToolSpec(id=id,description=description,parameters_schema=schema,
                                      prior_reliability=.45 if id=="noise_facade" else .7)
        self.handlers = {id:self._cached for id in self.specs}
        self.handlers.update(poi_nearby=self._poi, derive=self._derive, hotelier_ask=self._ask)

    def list(self):
        return list(self.specs.values())

    def validate(self, call: ToolCall):
        if call.tool not in self.specs:
            raise ValueError(f"Инструмент {call.tool} отсутствует в реестре")
        validate(call.params, self.specs[call.tool].parameters_schema)

    def execute(self, context: ToolContext, call: ToolCall) -> ToolResult:
        self.validate(call)
        return self.handlers[call.tool](context, call)

    def _cached(self, context, call):
        canonical = {"sun_window":"sunset_view","noise_facade":"quiet_night","walk_route":"beach_walk",
                     "climate":"climate","air_quality":"air_quality","connectivity":"connectivity",
                     "wikidata_lookup":"building_history","photos_nearby":"nearby_photos"}.get(call.tool)
        if call.tool == "viewshed_facade":
            canonical = "mountain_view" if call.params.get("target")=="mountains" else "sea_view"
        records = self.repo.find("claims",hotel_id=context.hotel_id,characteristic_id=canonical)
        allowed = {"geo_engine"} if call.tool in {"viewshed_facade","sun_window","noise_facade","walk_route"} else None
        selected = []
        superseded = {id for record in records for id in ([record["supersedes"]] if record.get("supersedes") else [])+record.get("supersedes_ids",[])}
        latest = {}
        for record in records:
            claim = Claim.model_validate(record)
            if claim.id in superseded or allowed and claim.source not in allowed or claim.valid_until<=datetime.now(UTC):
                continue
            if claim.scope.month and claim.scope.month!=context.month:
                continue
            key = (claim.source,claim.method,claim.scope.model_dump_json())
            if key not in latest or claim.observed_at>latest[key].observed_at:
                latest[key] = claim
        for claim in latest.values():
            selected.append(claim.model_copy(update={"id":uid(),"characteristic_id":context.characteristic_id,"run_id":context.run_id,"supersedes":claim.id if context.characteristic_id == canonical else None,"supersedes_ids":[]}))
        return ToolResult(claims=selected,artifacts={"cache_hit":True},warnings=[] if selected else ["Нет подтверждённых входных данных для инструмента; значение неизвестно"])

    def _ask(self, context, call):
        return ToolResult(warnings=["Свойство требует ответа отельера; вопрос формируется Interviewer"])

    def _poi(self, context, call):
        hotel = self._hotels.get(context.hotel_id) or self.repo.get("hotels",context.hotel_id)
        if not hotel:
            return ToolResult(warnings=["Отель не найден"])
        self._hotels[context.hotel_id] = hotel
        center = Point(hotel["lon"],hotel["lat"])
        metric_center = transform(TO_UTM.transform,center)
        category = call.params.get("category")
        dataset = POI_CATEGORIES[category][1] if category else "pois"
        if self._nearby(dataset, metric_center, 1) is None:
            return ToolResult(warnings=["OSM POI не загружены"])
        near = call.params.get("near_m", 500)
        radius = call.params.get("radius_m", min(5000, max(1000, 2*near)) if category else 1200)
        candidates = self._nearby(dataset, metric_center, radius)
        kind = call.params.get("kind", {"family_nearby":"family","car_access":"car"}.get(context.characteristic_id,"pharmacy"))
        selected = []
        artifacts = ["/data/seed/" + dataset + ".geojson"]
        if category:
            label, _, predicate = POI_CATEGORIES[category]
            matches = [(d,f) for d,f in candidates if predicate(f["properties"])]
            selected = matches[:1]
            nearest = round(matches[0][0]) if matches else None
            # Nothing mapped within the search radius is unknown, never "absent".
            value = {"available": None if nearest is None else nearest<=near, "distance_m": nearest, "near_m": near,
                     "count_near": sum(d<=near for d,_ in matches), "category": category, "category_label": label,
                     "osm_id": matches[0][1]["id"] if matches else None, "name": matches[0][1]["properties"].get("name") if matches else None}
            kind = f"category:{category}:{near}"
        elif kind == "pharmacy":
            pharmacies = [(d,f) for d,f in candidates if f["properties"].get("amenity")=="pharmacy"]
            opened = [(d,f) for d,f in pharmacies if f["properties"].get("opening_hours", "").strip()=="24/7"]
            selected = opened[:1]
            value = {"available": True if opened else None, "minutes":None,"distance_m":round(opened[0][0]) if opened else None,
                     "osm_id":opened[0][1]["id"] if opened else None,"opening_hours":"24/7" if opened else None,
                     "coverage":sum(bool(f["properties"].get("opening_hours")) for _,f in pharmacies)/len(pharmacies) if pharmacies else 0}
        elif kind == "family":
            playgrounds = [(d,f) for d,f in candidates if f["properties"].get("leisure")=="playground"]
            hospitals = [(d,f) for d,f in candidates if f["properties"].get("amenity")=="hospital"]
            selected = playgrounds[:1]+hospitals[:1]
            value = {"available":True if playgrounds else None,"playground_distance_m":round(playgrounds[0][0]) if playgrounds else None,
                     "playground_osm_id":playgrounds[0][1]["id"] if playgrounds else None,
                     "hospital_distance_m":round(hospitals[0][0]) if hospitals else None,"crossings":[]}
        elif kind == "nightlife":
            venues = [(d,f) for d,f in candidates if d<=NIGHTLIFE_RADIUS_M and f["properties"].get("amenity") in NIGHTLIFE_VENUES]
            bars = [(d,f) for d,f in venues if f["properties"].get("amenity") in NIGHTLIFE_BARS]
            any_bar = [(d,f) for d,f in candidates if f["properties"].get("amenity") in NIGHTLIFE_BARS]
            promenades = self._nearby("roads", metric_center, 1500, lambda p: _promenade(p))
            selected = bars[:3] + promenades[:1]
            artifacts.append("/data/seed/roads.geojson")
            value = {"available": len(venues)>=NIGHTLIFE_MIN_VENUES, "venues_500m": len(venues), "bars_500m": len(bars),
                     "cafes_restaurants_500m": len(venues)-len(bars), "nearest_bar_m": round(any_bar[0][0]) if any_bar else None,
                     "promenade_distance_m": round(promenades[0][0]) if promenades else None,
                     "radius_m": NIGHTLIFE_RADIUS_M, "min_venues": NIGHTLIFE_MIN_VENUES}
        else:
            parking = [(d,f) for d,f in candidates if f["properties"].get("amenity")=="parking" and f["properties"].get("access") not in {"no","private"}]
            ev = [(d,f) for d,f in candidates if f["properties"].get("amenity")=="charging_station"]
            selected = parking[:1]+ev[:1]
            parking_distance = round(parking[0][0]) if parking else None
            ev_distance = round(ev[0][0]) if ev else None
            # No mapped parking within the search radius is a data gap (None), not "no parking".
            value = {"available":None if parking_distance is None else parking_distance<=PARKING_NEAR_M,
                     "parking_distance_m":parking_distance,"parking_osm_id":parking[0][1]["id"] if parking else None,
                     "ev_available":None if ev_distance is None else ev_distance<=EV_CHARGING_NEAR_M,
                     "ev_distance_m":ev_distance,"parking_threshold_m":PARKING_NEAR_M,"ev_threshold_m":EV_CHARGING_NEAR_M,
                     "pedestrian_zone":None}
        observed = max((feature["properties"].get("observed_at","2026-10-25T21:21:21Z") for _,feature in selected),default="2026-10-25T21:21:21Z")
        observed = datetime.fromisoformat(observed.replace("Z","+00:00"))
        limitations = ["Расстояния по прямой, не время пешего маршрута", "Отсутствие объекта или opening_hours в OSM не доказывает отсутствие услуги", "Подъезд к фактическому входу, детский профиль больницы и доступность для коляски требуют отдельного подтверждения"]
        if kind == "nightlife":
            limitations.append(f"«В гуще событий» означает не меньше {NIGHTLIFE_MIN_VENUES} баров, пабов, клубов, кафе и ресторанов OSM в {NIGHTLIFE_RADIUS_M} м по прямой; часы работы и громкость заведений не проверяются")
        if category:
            limitations.append(f"«Рядом» означает объект категории OSM не дальше {near} м по прямой; закрытые или не отмеченные в OSM объекты не учитываются")
        if kind == "car":
            limitations.append(f"«Рядом» означает общедоступную парковку OSM не дальше {PARKING_NEAR_M} м и зарядку не дальше {EV_CHARGING_NEAR_M} м по прямой; собственная парковка отеля и свободные места не подтверждаются")
        evidence = Evidence(kind="calculation",label="Поиск по локальному снимку OpenStreetMap",source_ids=["osm_geofabrik"],artifact=artifacts[0],
                            inputs={"hotel_id":context.hotel_id,"radius_m":radius,"kind":kind,"artifacts":artifacts,"matched_osm_ids":[feature["id"] for _,feature in selected]},output=value,
                            limitations=limitations)
        method = POI_CATEGORY_METHOD if category else poi_method(kind)
        identity = hashlib.sha256(json.dumps([method,context.hotel_id,context.characteristic_id,kind,radius,observed.isoformat(),value],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
        evidence.id = "ev-poi-"+identity
        measured_distance = any(isinstance(value.get(key), int | float) for key in ("distance_m", "parking_distance_m", "ev_distance_m", "playground_distance_m", "hospital_distance_m", "venues_500m"))
        claim = Claim(id="poi-"+identity,hotel_id=context.hotel_id,characteristic_id=context.characteristic_id,value=value,source="osm_geofabrik",method=method,evidence=[evidence],confidence=.65 if value["available"] or measured_distance else .35,
                      observed_at=observed,valid_until=observed+timedelta(days=90),run_id=context.run_id)
        outdated = self._outdated_poi_claims(context.hotel_id, context.characteristic_id, method)
        if outdated:
            claim = claim.model_copy(update={"supersedes_ids": outdated})
        existing = self._known_claims.get(claim.id) if self._known_claims is not None else self.repo.get("claims", claim.id)
        if existing is not None:
            # Reuse the observation date, not its primary key. Append-only claims
            # retain an explicit supersession chain across manual collections.
            previous = self.repo.find("claims", hotel_id=context.hotel_id, characteristic_id=context.characteristic_id)
            equivalent = [row["id"] for row in previous if row.get("method") == claim.method and row.get("value") == value]
            claim = claim.model_copy(update={"id": uid(), "supersedes": existing["id"], "supersedes_ids": list(dict.fromkeys([*equivalent, *outdated]))})
        return ToolResult(claims=[claim], artifacts={"cache_hit": existing is not None})

    def _nearby(self, dataset: str, metric_center, radius: float, predicate=None):
        """Features of a local dataset within a straight-line radius, nearest first; None if absent."""
        features, geometries, tree = feature_index(str(self.data_dir.resolve()), dataset)
        if tree is None:
            return None
        found = []
        for index in tree.query(metric_center.buffer(radius)):
            index = int(index)
            distance = geometries[index].distance(metric_center)
            if distance <= radius and (predicate is None or predicate(features[index]["properties"])):
                found.append((distance, features[index]))
        found.sort(key=lambda item: item[0])
        return found

    def _outdated_poi_claims(self, hotel_id: str, characteristic_id: str, method: str) -> list[str]:
        """IDs of earlier POI method versions for this hotel/characteristic."""
        if self._known_claims is not None:
            if getattr(self, "_poi_index", None) is None:
                index = {}
                for row in self._known_claims.values():
                    if str(row.get("method", "")).startswith("local_poi_"):
                        index.setdefault((row["hotel_id"], row["characteristic_id"]), []).append(row)
                self._poi_index = index
            rows = self._poi_index.get((hotel_id, characteristic_id), [])
        else:
            rows = self.repo.find("claims", hotel_id=hotel_id, characteristic_id=characteristic_id)
        return sorted(row["id"] for row in rows if str(row.get("method", "")).startswith("local_poi_") and row.get("method") != method)

    def _derive(self, context, call):
        expression = call.params.get("expr")
        if not expression:
            raise ValueError("derive requires expr")
        import ast

        from backend.tools.scoped_inputs import compatible_inputs
        names = sorted({node.id for node in ast.walk(ast.parse(expression, mode="eval")) if isinstance(node, ast.Name)})
        combinations = compatible_inputs(self.repo.find("facts", hotel_id=context.hotel_id), names, context.month)
        claims = []
        for scope, facts in combinations:
            supporting = [fact.model_dump(mode="json") for fact in facts]
            values = {fact.characteristic_id: fact.value for fact in facts}
            try:
                result = evaluate_expression(expression, values)
            except MissingInput:
                continue
            value = {"available": result, "month": context.month, "minutes": values.get("beach_walk", {}).get("minutes"), "water_c": values.get("climate", {}).get("water_c")}
            source_ids = sorted({source for fact in facts for evidence in fact.evidence for source in evidence.source_ids})
            observed = max(fact.updated_at for fact in facts)
            deadlines = [fact.valid_until for fact in facts if fact.valid_until]
            if not deadlines:
                continue
            valid_until = min(deadlines)
            limitations = list(dict.fromkeys(["Составное условие наследует ограничения и уверенность входных данных"] + [limit for fact in facts for ev in fact.evidence for limit in ev.limitations]))
            evidence = Evidence(kind="calculation", label="Логическое условие над совместимыми входными фактами", source_ids=source_ids or ["geo_engine"], url="/api/runs/"+context.run_id,
                                inputs={"expression": expression, "values": values, "fact_ids": [fact["id"] for fact in supporting], "scope": scope.model_dump(mode="json")}, output=value, limitations=limitations)
            claims.append(Claim(hotel_id=context.hotel_id, characteristic_id=context.characteristic_id, scope=scope, value=value, source="geo_engine", method="safe_expression@1", evidence=[evidence], confidence=min(fact.confidence for fact in facts), observed_at=observed, valid_until=valid_until, run_id=context.run_id, synthetic=any(fact.synthetic for fact in facts)))
        return ToolResult(claims=claims, warnings=[] if claims else ["Недостаточно совместимых свежих входных фактов; общий номер не подтверждён"])
