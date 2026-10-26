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


@lru_cache(maxsize=4)
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
                geometries.append(geometry)
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
                  "kind":{"type":"string","enum":["pharmacy","family","car"]},"radius_m":{"type":"number","minimum":50,"maximum":5000},
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
        features, geometries, tree = feature_index(str(self.data_dir.resolve()),"pois")
        if tree is None:
            return ToolResult(warnings=["OSM POI не загружены"])
        center = Point(hotel["lon"],hotel["lat"])
        metric_center = transform(TO_UTM.transform,center)
        radius = call.params.get("radius_m",1200)
        candidates = []
        for index in tree.query(center.buffer(radius/75000)):
            index = int(index)
            distance = transform(TO_UTM.transform,geometries[index]).distance(metric_center)
            if distance<=radius:
                candidates.append((distance,features[index]))
        candidates.sort(key=lambda item:item[0])
        kind = call.params.get("kind", {"family_nearby":"family","car_access":"car"}.get(context.characteristic_id,"pharmacy"))
        selected = []
        if kind == "pharmacy":
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
        else:
            parking = [(d,f) for d,f in candidates if f["properties"].get("amenity")=="parking" and f["properties"].get("access") not in {"no","private"}]
            ev = [(d,f) for d,f in candidates if f["properties"].get("amenity")=="charging_station"]
            selected = parking[:1]+ev[:1]
            value = {"available":None,"parking_distance_m":round(parking[0][0]) if parking else None,
                     "parking_osm_id":parking[0][1]["id"] if parking else None,"ev_distance_m":round(ev[0][0]) if ev else None,
                     "pedestrian_zone":None}
        observed = max((feature["properties"].get("observed_at","2026-10-25T21:21:21Z") for _,feature in selected),default="2026-10-25T21:21:21Z")
        observed = datetime.fromisoformat(observed.replace("Z","+00:00"))
        evidence = Evidence(kind="calculation",label="Поиск по локальному снимку OpenStreetMap",source_ids=["osm_geofabrik"],artifact="/data/seed/pois.geojson",
                            inputs={"hotel_id":context.hotel_id,"radius_m":radius,"kind":kind,"matched_osm_ids":[feature["id"] for _,feature in selected]},output=value,
                            limitations=["Расстояния по прямой, не время пешего маршрута", "Отсутствие объекта или opening_hours в OSM не доказывает отсутствие услуги", "Подъезд к фактическому входу, детский профиль больницы и доступность для коляски требуют отдельного подтверждения"])
        identity = hashlib.sha256(json.dumps([context.hotel_id,context.characteristic_id,kind,radius,observed.isoformat(),value],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
        evidence.id = "ev-poi-"+identity
        claim = Claim(id="poi-"+identity,hotel_id=context.hotel_id,characteristic_id=context.characteristic_id,value=value,source="osm_geofabrik",method="local_poi_lookup@1",evidence=[evidence],confidence=.65 if value["available"] else .35,
                      observed_at=observed,valid_until=observed+timedelta(days=90),run_id=context.run_id)
        existing = self._known_claims.get(claim.id) if self._known_claims is not None else self.repo.get("claims", claim.id)
        if existing is not None:
            # Reuse the observation date, not its primary key. Append-only claims
            # retain an explicit supersession chain across manual collections.
            previous = self.repo.find("claims", hotel_id=context.hotel_id, characteristic_id=context.characteristic_id)
            equivalent = [row["id"] for row in previous if row.get("method") == claim.method and row.get("value") == value]
            claim = claim.model_copy(update={"id": uid(), "supersedes": existing["id"], "supersedes_ids": equivalent})
        return ToolResult(claims=[claim], artifacts={"cache_hit": existing is not None})

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
