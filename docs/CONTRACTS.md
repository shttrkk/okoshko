# О!кно: общие контракты v1

Машинная схема: `backend/contracts/models.py`; HTTP API: `docs/openapi.json`.
JSON Schema всех общих моделей: `docs/schemas.json`. Все пути ниже относительны корню репозитория.

## 1. Инварианты

- JSON: snake_case, ISO 8601 UTC с часовым поясом, даты `YYYY-MM-DD`.
- ID: строки, отели `osm-node-123` / `osm-way-123` / `overture-<id>`; не менять после merge.
- Координаты GeoJSON — `[lon, lat]`, WGS84. Вычисления расстояний — UTM 37N (EPSG:32637).
- Румбы `N NE E SE S SW W NW`; азимут по часовой стрелке от севера.
- Российская нумерация этажей, высота глаза `base+(floor-1)*height+1.5` м.
- `Claim.value` может быть bool, number или объектом, но должен соответствовать `CharacteristicSpec.value_schema`.
- Каждый Claim имеет `source`, `method`, непустой `evidence`, `confidence` [0,1], `observed_at`, `valid_until`.
- `Evidence` содержит доступный URL или локальный `artifact` (путь `/data/...`), а также `inputs`/`output` и ограничения.
- Локальные артефакты должны лежать в репозитории. Ссылка на отсутствующий расчёт не evidence.
- Claims неизменяемы. Обновление — новый claim с `supersedes` или `supersedes_ids` (несколько старых диапазонов при изменении геометрии), затем Resolver атомарно заменяет materialized facts отеля. Старые границы этажей не остаются активными.
- Unknown — `Fact(value=null,status=unknown,confidence=0)` без выдуманного claim.
- Synthetic — отдельный флаг у claim/fact/conflict/answer/change; источник симулятора `hotelier_simulator`.
- Расчёт шума — приближённая модель наружного шума, не измерение дБ и не характеристика шумоизоляции.
- LLM выбирает whitelist инструментов и разбирает текст; свободный код и неподтверждённые значения запрещены.
- Стартап без сетевых вызовов. `/api/status.mode` — `offline|cache|live`; offline всегда работоспособен.

## 2. Семантика характеристик

Канонические ID и ключи объектов `value`:

| ID | Значение | Поиск по умолчанию |
|---|---|---|
| `sea_view` | `{available, min_floor, facade, sea_width_deg, sea_fraction, blocking_building_ids, probability}` | available=true |
| `sunset_view` | `{available, min_floor, facade, months, azimuth_deg, visible_minutes, probability}` | available=true, месяц из запроса |
| `mountain_view` | `{available, min_floor, facade, peaks, width_deg, probability}` | available=true |
| `quiet_night` | `{available, facade, night_db_min, night_db_max, day_db_min, day_db_max, index, sources}` | available=true |
| `beach_walk` | `{available, minutes, return_minutes, distance_m, ascent_m, return_ascent_m, has_steps, crossings, beach_name, route_ref}` | minutes <= 10 |
| `night_pharmacy` | `{available: bool|null, minutes, osm_id, opening_hours, coverage}` | available=true |
| `family_nearby` | `{available, playground_distance_m, playground_osm_id, hospital_distance_m, crossings}` | available=true |
| `car_access` | `{available: bool|null, parking_distance_m, parking_osm_id, ev_distance_m, pedestrian_zone}` | available=true |
| `climate` | `{month, air_c, water_c, sunshine_hours, precipitation_mm, period}` | water_c >= 20 |
| `soundproofing` | `{available: bool|null, windows, blackout: bool|null, air_conditioning: bool|null}` | available=true |
| `room_orientation` | `{available, facade, min_floor, max_floor, rooms, balcony}` | available=true |
| `pets` | `{available: bool|null, species, max_weight_kg, fee_rub_night, walking_area}` | available=true, вес из запроса |
| `air_quality` | `{pm2_5, european_aqi, period, model}` | european_aqi <= 40 |
| `connectivity` | `{download_mbps, upload_mbps, latency_ms, tests, kind, quarter}` | download_mbps >= 30 |
| `building_history` | `{available, wikidata_id, inception, architecture, text}` | available=true |
| `nearby_photos` | `{available, photos:[{url, thumbnail, title, author, license, source_url}]}` | available=true |
| `morning_swim` | `{available, minutes, water_c, month}` | available=true (studio example) |

Наблюдение по фасаду/этажам — отдельный Claim со `scope`; не сводить морскую и тихую стороны к одному номеру. Ranker обязан проверять совместимость scope для всех обязательных условий.
Неизвестные POI/часы работы не означают «нет». Характеристики с `null` требуют вопроса/ограничения.
`Fact.evidence` — копия evidence поддерживающих claims для быстрого UI; первичная цепочка — supporting_claim_ids.

## 3. Seed: CSV.gz + проверяемые артефакты

Каждая таблица загружается из `data/seed/<table>.csv.gz`: UTF-8, CSV RFC4180, ровно две колонки `id,payload`. `payload` — JSON объекта с таким же id. Сжатие gzip, `mtime=0` для воспроизводимости. COPY во временную таблицу, затем INSERT; порядок: sources, hotels, characteristics, agent_runs, claims, facts, прочие.

- Обязательные: `hotels.csv.gz`, `sources.csv.gz`. Claim и Fact валидируются моделями до экспорта.
- Pipeline экспортирует hotels/sources/buildings/osm_features/hotel_facades/room_types; geo stage создаёт `geo_claims.jsonl.gz` как промежуточный ledger. Bootstrap объединяет claims и материализует facts.
- Исходные записи открытых данных, версии, URL, retrieval, bbox и SHA-256 — `data/seed/manifest.json`; счётчики и использованные лицензии — там же.
- Pipeline дополнительно экспортирует `hotels.json`, `buildings.geojson`, `pois.geojson`, `roads.geojson`, `railways.geojson`, `beaches.geojson`, `coastline.geojson`, `walk_graph.json.gz` в `data/seed/` для S2.
- Граф: `{nodes:[{id,lon,lat,elevation_m?}],edges:[{u,v,length_m,highway,geometry,steps,tunnel,bridge,crossing,access}]}`. Рёбра могут быть направленными; запреты доступа соблюдаются.
- Geo: `data/geo/<hotel_id>.npz` и `data/geo/<hotel_id>.json`: фасады, этажи, профиль горизонта, сектор моря, солнечные пути по месяцам, шум, маршрут и ссылки на исходники. Всё < 50 МБ на файл.
- `.npz` только numeric/bool, `allow_pickle=False`. Metadata в соседнем JSON.
- Source registry — только реально скачанные и использованные источники (`used=true`); список планов не считать покрытием.
- Базовые mock поля честно помечать `basic_fields_source=mock_basic_card`; имена и координаты реальных объектов не выдумывать.
- Метки качества: `data/eval/labels.csv`, поля id,hotel_id,characteristic_id,scope,value,label_type,evidence_url,quote,labeler,observed_at. Истинную точность не заменять проверкой самосогласованности.

## 4. Хранилище и сервисный интерфейс Python

DB: таблицы из миграции `0001`. `id text PK,payload jsonb` — проверенный Pydantic JSON, с generated NOT NULL provenance-колонками для claims и геометрией PostGIS hotels. Это типизированные JSON-документы с SQL-ограничениями, не произвольный key/value.

Интерфейс `backend/db/repository.py`:

```python
class Repository:
    def all(self, table: str) -> list[dict]: ...
    def get(self, table: str, id: str) -> dict | None: ...
    def find(self, table: str, **equal) -> list[dict]: ...
    def upsert(self, table: str, value: dict) -> dict: ...
    def insert(self, table: str, value: dict) -> dict: ...
    def add_claim(self, claim: Claim | dict) -> dict: ...

def get_repo() -> Repository: ...  # dependency FastAPI
```

Интерфейс синхронный, psycopg3. Локальный кэш допускается с инвалидацией, запись всегда в Postgres; рестарт не теряет паспорт/ответ/характеристику. Все имена таблиц whitelist, SQL-параметры bind. In-memory Repository допускается только в тестах. Claims insert-only.

Интерфейсы диалога:

```python
# backend/agents/interviewer.py
def generate_questions(hotel_id: str, repo) -> list[Question]: ...
# backend/agents/answer_parser.py
def parse_answer(question: Question, text: str, *, synthetic: bool=False, llm=None) -> Answer: ...
# backend/sim/hotelier.py
def simulate_answer(question: Question, repo, *, seed: int=42) -> Answer: ...
```

FastAPI `router` определён в `backend/api/hotelier.py`, `backend/api/passports.py`; `prefix='/api'`. Resolver вызывать `backend.agents.resolver.resolve_hotel(hotel_id,repo)` после подтверждения ответа. Функция реализована в Resolver. Никаких сетевых писем/мессенджеров: уведомления локально в паспорте.

Интерфейсы поиска:

```python
def parse_query(request: SearchRequest, catalog: list[CharacteristicSpec], llm=None) -> ParsedQuery: ...
def rank_hotels(hotels: list[Hotel], facts: list[Fact], parsed: ParsedQuery,
                specs: list[CharacteristicSpec]) -> list[SearchHit]: ...
def recommend_room(facts: list[Fact], parsed: ParsedQuery | None=None) -> str: ...
def disappointments(facts: list[Fact], parsed: ParsedQuery | None=None) -> list[str]: ...
def similar_hotels(reference_id: str, hotels: list[Hotel], facts: list[Fact],
                   specs: list[CharacteristicSpec]) -> list[SearchHit]: ...
```

Публичный geo runtime вход: `geo.service.compute_hotel(hotel: dict, data_dir: Path, *, new_building: dict|None=None) -> dict`, где результат `{claims:[Claim JSON],geo:{...}}`. Допустим другой внутренний API, но этот адаптер обязателен. Не додумывать реальные входы при их отсутствии: return unknown с limitation. Tool registry оборачивает этот сервис.

## 5. HTTP API (все на одном origin)

Объекты запросов/ответов из моделей, ошибки `{detail: ...}`, HTTP 404/422/409. Неизвестные объекты возвращают 404; недопустимый контракт — 422; конфликт состояния — 409.

| Method | Path | Input → output |
|---|---|---|
| GET | /health | `{status:ok}` только когда DB и seed готовы |
| GET | /api/status | `{name,mode,version,hotel_count,characteristic_count,seed_version}` |
| GET | /api/hotels | `Hotel[]` |
| POST | /api/search | SearchRequest → SearchResult |
| GET | /api/hotels/{id} | HotelDetail |
| GET | /api/hotels/{id}/geo | geo JSON из раздела 6 |
| GET | /api/compare?ids=a,b,c | `{hotels:HotelDetail[],characteristics:CharacteristicSpec[]}` |
| POST | /api/hotelier/login | `{username,password}` → `{token,username,role,hotel_ids,synthetic:true}` |
| POST | /api/hotelier/logout | → `{logged_out:true,synthetic:true}`, удаление read-only SSE cookie |
| GET | /api/hotelier/{id} | `{hotel,facts,claims,questions,conflicts,passports,strengths,demand_synthetic:true}` |
| POST | /api/questions | `{hotel_id,characteristic_id}` → Question (идемпотентно для pending пары) |
| POST | /api/questions/{id}/answer | `{text,confirm:false,synthetic}` (demo server принудительно synthetic=true) → Answer; предпросмотр, без claims до confirm |
| POST | /api/questions/{id}/simulate | → Answer с synthetic=true, без сохранения claims до confirm |
| POST | /api/answers/{id}/confirm | → Answer с confirmed=true и created_claim_ids; повтор не дублирует |
| POST | /api/passports | PassportCreate → ExpectationPassport |
| GET | /api/passports?hotel_id=... | ExpectationPassport[] |
| GET | /api/passports/{id} | ExpectationPassport |
| POST | /api/passports/{id}/action | PassportAction → ExpectationPassport |
| GET | /api/characteristics | CharacteristicSpec[] |
| POST | /api/characteristics/compile | CompileRequest → `{spec:CharacteristicSpec,yaml_text,plan:ToolCall[],warnings,mode}` |
| POST | /api/characteristics/dry-run | CharacteristicSpec → `{spec_id,results:[{hotel_id,claims,warnings}],run_id,valid}` |
| POST | /api/characteristics/publish | CharacteristicSpec → `{spec,run_id,processed,total,status}` |
| GET | /api/tools | ToolSpec[] |
| POST | /api/runs | RunRequest → AgentRun |
| GET | /api/runs | AgentRun[] (последние 50, newest first) |
| GET | /api/runs/{id} | AgentRun с steps |
| GET | /api/runs/{id}/stream | SSE `event:step`, `data:<AgentStep JSON>`; затем `event:done`, data с run_id/status |
| GET | /api/changes | ChangeEvent[] |
| POST | /api/changes/simulate | `{hotel_id?:str}` → ChangeEvent, изменение fixture с пересчётом |
| POST | /api/changes/refresh | `{processed,run_id}` истекшие valid_until |
| GET | /api/sources | Source[] |
| GET | /api/quality | contents `data/eval/results.json` + актуальное число ручных меток |
| GET | /api/quality/label | `{hotel,facts,evidence_links,existing_labels}[]` |
| POST | /api/quality/label | LabelRequest → сохранённая метка с id |

Демо login возвращает signed bearer с role/hotel_ids и HttpOnly SameSite=Strict cookie Path=/api/runs для EventSource. Кабинет, ответы, список паспортов и изменения требуют Bearer; tenant видит только свой hotel_id. Demo admin может запускать changes simulate/refresh. Public changes удаляет private notification IDs, оставляя aggregate notification_count. Private interview runs доступны только своему tenant/admin, публичные search/collect traces остаются открыты. Logout удаляет SSE cookie; restart отзывает локальные demo sessions. Это демонстрационные credentials, не production IAM. Гостевой GET паспорта по UUID остаётся capability; expiry/revocation не реализованы. Все ответы demo principals synthetic независимо от запроса. Студия — публичный demo-only sandbox с whitelist tools, без shell/произвольной сети.

## 6. Geo JSON для фронта

```json
{"hotel_id":"osm-way-123","center":[39.72,43.58],"building":{"type":"Feature","properties":{"height":27,"height_source":"levels"},"geometry":{"type":"Polygon","coordinates":[]}},"facades":[{"code":"W","azimuth_deg":270,"geometry":{"type":"LineString","coordinates":[]},"min_floor_sea":5,"min_floor_sunset":5,"night_db_min":44,"night_db_max":56,"confidence":0.65}],"horizon":[{"azimuth_deg":270,"terrain_deg":0.2,"buildings_deg":1.1,"sea":true}],"sun_paths":[{"month":9,"points":[{"azimuth_deg":275,"elevation_deg":2}]}],"view_cones":{"type":"FeatureCollection","features":[]},"route":{"type":"FeatureCollection","features":[]},"crossings":{"type":"FeatureCollection","features":[]},"monte_carlo":{"iterations":100,"probability":0.72,"min_floor_p80":6},"limitations":["Приближённая высота части зданий"]}
```

Числа здесь — контрактный пример, не реальные измерения. Runtime возвращает actual result; UI использует evidence API. `docs/fixtures/` помечены synthetic и доступны для разработки, API production не использует эти fixtures как реальные данные.

## 7. Версионирование и совместимость

Общие модели валидируются Pydantic на HTTP, seed и внутренних границах. Изменения wire-схем сопровождаются обновлением OpenAPI, JSON Schema и контрактных fixtures через `make contracts`. Новые необязательные поля имеют безопасные defaults; идентификаторы существующих отелей, claims и источников сохраняются. Миграции БД применяются автоматически перед bootstrap.

Изменение вычислительного метода создаёт новую версию method и новые claims с supersedes; оно не переписывает исторические наблюдения. Изменение хранения артефакта с сохранением логических bytes не меняет геометрический результат. Проверки совместимости — parser/geo known-answer tests, seed validation, API smoke и demo-check.

## 8. Уточнения интеграционной приёмки

- Seed geometry хранится только `*.geojson.gz` (gzip mtime0), logical URI остаётся `*.geojson`, сервер отдаёт Content-Encoding:gzip. В geo evidence `inputs` SHA256 определяется по исходным несжатым GeoJSON bytes; для explicit `.gz` графа и NPZ hash остаётся по physical bytes. `geo.io.read_bytes/exists/read_json` реализуют fallback. Pipeline manifest содержит physical hash и uncompressed_sha256; числа/геометрии при сжатии не меняются. Дубли geometry CSV не используются и удалены.
- `POST /api/hotels/{id}/evaluate` принимает целый ParsedQuery, `POST /api/compare` принимает `{ids:[2..4 unique],parsed:ParsedQuery|null}`. Условия edited chips не теряются из-за повторного разбора исходного текста.
- CriterionVerdict имеет `synthetic` и `disputed`; UI показывает их явно. SearchHit.all допускает ✓ либо≈ с выполненным сравнением/confidence≥0.6, unknown ограничения не засчитываются. Точное yes требует≥0.7. Нет обещания реального наличия свободного номера.
- В поставке есть ровно3 source=hotelier_simulator claims, public `data/seed/demo_hotelier_claims.jsonl`: вымышленные ответы о тишине конкретных facade/floor/month. Обязателен synthetic=true и kind=simulation, это не полевая ground truth.

- `data/seed/demo_hotelier_dialogues.json`: synthetic/version1 envelope содержит typed questions/answers/claims/agent_runs/agent_steps;7 confirmed synthetic answers +2 pending. Loader проверяетrefs/flags/источник/отели иinsert-only, не сбрасывает уже отвеченные вопросы. Fact остаётсяcomputed/synthetic; статусреальногоподтверждениянеприсваивается.
- Для parking/family числовое наблюдение можетбытьcomputed при available=null. Это знание конкретного расстояния, не утверждение о въезде/услуге. Сравнение available продолжаетunknown.

- `GET /api/quality` дополнительно возвращает filter_baseline (версионированный модельный отчёт) и provenance актуальной БД: known/non_synthetic/synthetic/mixed/unknown_stale_disputed, unit=materialized_facts. Mixed — подмножество synthetic; это не доля отелей и не полевая точность.
