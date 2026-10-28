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
| `connectivity` | `{available, wifi_reported, internet_access, fee, download_mbps, upload_mbps, latency_ms, tests, kind, quarter}` | download_mbps >= 30 |
| `building_history` | `{available, wikidata_id, inception, architecture, text}` | available=true |
| `nearby_photos` | `{available, photos:[{url, thumbnail, title, author, license, source_url}]}` | available=true |
| `nightlife` | `{available, venues_500m, bars_500m, promenade_distance_m}` | venues_500m gte 30 |
| `morning_sun` | `{available, visible_minutes, exposure_index, visible_minutes_min, visible_minutes_max, exposure_index_min, exposure_index_max, value_semantics}` | available eq true |
| `evening_heat` | `{available, visible_minutes, exposure_index, visible_minutes_min, visible_minutes_max, exposure_index_min, exposure_index_max, value_semantics}` | exposure_index lte 0.35 |
| `railway_noise` | `{available, night_db_min, night_db_max, day_db_min, day_db_max}` | night_db_max lte 50 |
| `beach_surface` | `{available, distance_m, surface, beach_id, selection_kind}` | surface eq sand |
| `accessible_route` | `{available, distance_m, has_steps, wheelchair_suitable}` | available eq true |
| `green_nearby` | `{available, distance_m}` | distance_m lte 500 |

`morning_swim` — пример публикации из студии, а не предустановленная характеристика. Для всех 23 текущих спецификаций authoritative schema и поисковые настройки находятся в YAML.

Наблюдение по фасаду/этажам — отдельный Claim со `scope`; не сводить морскую и тихую стороны к одному номеру. Ranker обязан проверять совместимость scope для всех обязательных условий.
Неизвестные POI/часы работы не означают «нет». Характеристики с `null` требуют вопроса/ограничения.
`Fact.evidence` — копия evidence поддерживающих claims для быстрого UI; первичная цепочка — supporting_claim_ids.

Компактный профиль дополнительно сохраняет `Fact.origin_kinds`: небольшой набор `hotelier`, `demo_hotelier`, `calculation`, `open_data`, `simulation`, `mock`, полученный только из исходного evidence. `demo_hotelier` требует соответствующего типа свидетельства и источника симулятора, общий synthetic-флаг его не доказывает. Полные цитаты и расчёты по-прежнему отложены (`evidence=[]`, `evidence_deferred=true`); origin_kinds не заменяет supporting_claim_ids и полное доказательство. В исходной материализации пустое поле не сериализуется, прежние входные данные и их SHA сохраняются.

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
| GET | /api/hotelier/{id} | `{hotel,facts,claims,questions,conflicts,passports,strengths,search_demand,demand_synthetic:false}`; спрос — число запросов из журнала поиска с этим условием, где отель был в выдаче |
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

- `GET /api/quality` дополнительно возвращает filter_comparison (версионированный модельный отчёт) и provenance актуальной БД: known/non_synthetic/synthetic/mixed/unknown_stale_disputed, unit=materialized_facts. Mixed — подмножество synthetic; это не доля отелей и не полевая точность.

## Компактные профили и ленивые доказательства

`HotelDetail` имеет необязательные флаги `evidence_deferred` и `geometry_deferred` (по умолчанию false). Они отличают компактную проекцию от отсутствующих данных. Компактный профиль сохраняет ID, значения, scope, confidence, статус, supporting/conflicting claim IDs; evidence в компактном профиле может быть пустым при evidence_deferred=true, полные inputs/output, ограничения и claims загружаются отдельно. Полные подписи и URL/artifact/quote/message references доступны через GET /api/hotels/{hotel_id}/facts/{fact_id}/evidence; пустой deferred evidence не означает отсутствие доказательства. supporting IDs и непустые conflicting IDs сохраняются. Пустые optional defaults, null scope-поля и false synthetic могут отсутствовать; resolution стратегии доступна в полном evidence, а в preview не заявляется. Значения, confidence, статус, даты актуальности и ID фактов сохраняются. JSON остаётся валидным по общей модели Evidence. Внутренняя поисковая проекция сохраняет facade_azimuth_deg и window_half_angle_deg для объяснения бокового заката. Публичный compact HotelDetail откладывает evidence целиком; точная нормаль UI поступает из отдельно загруженной Geo, если она единственная и непротиворечивая. Derived verdicts и предупреждения считаются до проекции доказательств. `FactEvidence` возвращает полный `fact` и его `claims`; hotel_id и принадлежность fact проверяются вместе. Геометрия имеет отдельный маршрут `/api/hotels/{id}/geo`. Полная проекция по умолчанию сохраняет совместимость API.

`SearchHit.why_not_higher` и `similarity_reasons` — списки коротких текстов, подкреплённых verdict/evidence ссылками. Неизвестные поля и неподтверждённые условия не становятся выполненными ради похожести.


## Дополнительные геопризнаки

`data/seed/geo_trait_claims.jsonl.gz` — append-only ledger типизированных Claim; манифест `geo_trait_manifest.json` фиксирует physical SHA-256, версии методов и покрытие. Источники — существующий Source registry, никаких новых runtime-загрузок. Solar v2 содержит `building_association` в evidence.inputs, условную привязку nearest_30m и пониженную confidence. Contiguous floor ranges сохраняют минимумы/максимумы минут и экспозиции; скаляр для поиска выбирается консервативно. Доступность маршрута nullable даже при известном отсутствии ступеней.

После изменения геометрии старые morning_sun/evening_heat/railway_noise supersede новыми unknown claims, если они рассчитаны по другому варианту. Это не отрицательный результат и не новое реальное наблюдение: событие synthetic. Уже сохранённый расчёт того же именованного варианта сохраняется при его копировании в runtime. Tool Registry использует только текущую версию метода и authoritative artifact события; при необходимости пересчитывает солнце по нему. Неподдерживаемый новый расчёт железнодорожного экранирования остаётся unknown.

## Контекст вопроса отелю

`AskRequest.scope` — необязательный срез, о котором спрашивает гость. `Question.requested_scopes` содержит до 8 уникальных пожеланий к одному pending-вопросу hotel/characteristic. Это запрос на уточнение, не сведения о существовании окна/номера и не область автоматически подтверждённого факта. Отсутствие scope сохраняет прежнее поведение. AnswerParser извлекает область только из дословного ответа: простое «да» не наследует этаж, сторону или месяц из вопроса.

### Сообщённое наличие интернета

`data/seed/connectivity_observations.json` содержит небольшой OSM-срез с точной идентичностью объектов, исходными тегами, датами и хэшами; `connectivity_claims.jsonl.gz` загружается тем же append-only seed-loader. `wifi_reported` и `internet_access` не устанавливают `available`, скорость или работоспособность сети. Все `download_mbps/upload_mbps/latency_ms` в этом срезе — null. Поисковое поле характеристики остаётся `download_mbps`, поэтому эти 24 наблюдения не закрывают условие скорости. `make connectivity-data` повторяет извлечение без сети.

### Изменения открытых экранов

`GET /api/revision` возвращает `{instance: string, revision: integer}` с `Cache-Control: no-store`, без идентификаторов гостей и содержимого запросов. Один процесс приложения повышает generation после изменения facts, каталога, вопросов/ответов, конфликтов, событий и паспортов. Журнал поиска и трассы не повышают generation. Видимая вкладка проверяет сигнал раз в 3 секунды; завершение локальной операции обновляет данные сразу. Это process-local механизм одного runtime, не распределённая подписка и не гарантия для прямых SQL-записей.

### Проверка идентичности исходных объектов

`data/seed/catalogue_reviews.json` — локальная политика `hotel_identity_reviews_v1`, отдельная от неизменных raw hotel seeds и claims. Каждая запись содержит точный `hotel_id`, `disposition=hold_for_identity_review`, причину, source/method, evidence, expert_uncalibrated confidence, observed_at/valid_until и ограничения. Hold означает необходимость проверки идентичности или координат, а не отсутствие реальной гостиницы. Истечение срока не возвращает объект в подбор автоматически. Корректный `reviews: []` допустим для другого региона.

`backend.catalogue` валидирует файл на старте; при повреждении после успешной загрузки сохраняет последнюю валидную политику. На холодном старте отсутствие или неверный формат дают явную ошибку конфигурации. IDs находятся в данных, а не в коде. Search/ranking/similarity, compare и создание паспорта исключают holds; профиль по прямому ID сохраняет review metadata в `Hotel.tags.catalogue_review`, не выдавая рекомендацию номера. `/api/status.hotel_count` считает доступные кандидаты; `source_object_count` и `held_object_count` показывают исходный объём и ограничения. Статистика модели по исходному seed и frozen weak labels остаётся отдельным измерением.


## Групповой запрос и описания положений (версия 2026-10-27)

`ParsedQuery.rooms: RoomRequest[]` по умолчанию пуст: прежний однономерный путь сохраняется. RoomRequest: label, must/nice, scope, no_data/no_data_must, unmapped, clarifications. RoomScope содержит необязательные facade, floor_min, floor_max, month, room_type и проверяет границы; это пожелание/модельный срез, поэтому фасад не обязателен для floor_max. Scope доказательств не ослабляется. До12 запросов, это не количество людей или доступных комнат.

Общие критерии отеля остаются в ParsedQuery.must/nice и проверяются один раз. Room-only IDs определяет `query_match.ROOM_CHARACTERISTICS`. У каждого запроса свои совместимые facade/floor/month; `all` требует выполнения всех обязательных комнатных и общих условий. Unknown и no_data_must не дают all. Число гостей не превращается в число номеров. Наличие, вместимость, цена и соседство неизвестны. Одинаковое положение двух запросов не означает две свободные комнаты.

SearchHit и HotelDetail добавляют room_plan[] и optional room_profile. RoomPlanItem: label, scope, recommendation, verdicts, status(yes/partial/no/unknown), profile, caveats. `RoomProfile`: id, scope, title, description, clauses[], caveats, conditional. Clause: text, characteristic_id?, fact_ids[], evidence_refs[], status, synthetic. Каждая утвердительная фраза подкрепляется свежими facts; unknown может иметь пустые refs. Это положения в здании, не реальные типы номеров. Endpoint `GET /api/hotels/{id}/rooms?month=` возвращает компактный список; опциональные facade/floor_min/floor_max фильтруют выбранный срез. Доказательства остаются ленивыми через существующий fact evidence API.

PassportItem получает optional room_label/room_index; ExpectationPassport — room_requests[]/room_plan[]. Старые поля и API остаются совместимыми. Подтверждение нескольких пожеланий не является гарантией количества свободных комнат. Групповая ориентация/этажи не должны объединяться в один общий scope при проверке критичных пунктов.

Окна во двор не выводятся из отсутствия дорог или направления фасада: при отсутствии явного факта courtyard такое требование остаётся неизвестным. Общий месяц/даты копируются в комнатные оценки; `floor_max` хранится в scope.

`GET /api/hotels/{id}/rooms?month=9` возвращает `RoomProfile[]` без полных доказательств. Необязательные `facade`, `floor_min`, `floor_max` выбирают один срез. При неизвестной этажности результат остаётся условным уровнем 1; запрошенный высокий этаж не становится фактом. Невалидные месяц/сторона/границы дают 422, неизвестный объект — 404, удержанный из-за проверки каталога — 422. Полные доказательства доступны по существующему `/facts/{fact_id}/evidence`.


## Советы по сторонам и этажам (2026-10-27)

`GET /api/hotels/{id}/room-advice?month=9` → `RoomAdvice[]` (до 4 советов). Это детерминированные советы для гостя по согласованным свежим фактам, не номерной фонд. Advice: id, characteristic_id, title, text, options[], fact_ids[], evidence_refs[], status yes/partial, synthetic, caveats[]. Option: scope(RoomScope), fact_ids[], evidence_refs[], status yes/partial, synthetic. Каждая сторона/этаж и каждое число должны подтверждаться refs соответствующей option; неизвестное, спорное или противоречивый внутренний этаж не дают утверждения. Options предназначены для открытия RoomLab; группировка текста не смешивает несовместимые стороны в один номер. Статус `partial` и demo-метки остаются видимыми, подробные ограничения находятся на втором уровне. Пустой список означает, что совет по сторонам пока не подтверждён. Существующий /rooms остаётся совместимым, но полезность положений определяется до лимита.


## Проверенные фотографии (2026-10-27)

`GET /api/hotels/{id}/photos` → `HotelPhoto[]`; 404 для неизвестного отеля, 422 для удержанного объекта. Это чтение подготовленного локального индекса без сетевых вызовов к поставщикам. Статический `/data/seed/hotel_photos.json` сохраняется для существующего интерфейса; его JSON Schema — `HotelPhotoIndex`.

`photos[id]` содержит записи `HotelPhoto`: kind, url, local, page_url, title, author, license, license_url, match, match_text, distance_m, width, height, bytes, sha256, verified_by_eye, captured_year, captured_at, image_id. Уровни обложки: building (Commons) → official_site → street (Mapillary) → схема и внешняя ссылка. `room` — до восьми независимых фото интерьера номера, никогда не обложка и не доказательство стороны, этажа или вида. `verified_by_eye=true` обязателен. У official_site/room хранятся только URL на домене сайта отеля, local=false, author/license/license_url=null. Street хранится локальным WebP ≤640 px и ≤60000 Б, с автором, CC BY-SA 4.0, ссылкой на кадр, годом съёмки и image_id; токен в записи не допускается. Building сохраняет прежние лицензии и миниатюры.

`counts` считает лучший уровень обложки (building/official_site/street/none); `room_hotel_count` и `room_photo_count` считаются отдельно, поскольку интерьер не доказывает внешний вид здания. Неизвестное отсутствие фото не заменяется фотографией соседнего объекта. Галерея номеров загружает изображения только после нажатия; Яндекс Карты открываются внешней ссылкой только по действию гостя. TypeScript допускает неполные legacy-поля визуального fallback; подтверждённый seed и API проходят строгую Pydantic-валидацию.


## Происхождение ответа и отображаемые имена (2026-10-27)

CharacteristicSpec.display_name — единое имя для интерфейсов; name_ru и synonyms сохраняют прежнее значение для разбора. Отсутствующее display_name означает name_ru. Изменение имени не меняет методику характеристики или её version.

CriterionVerdict.basis: calculation/open_data/hotelier/mixed/unknown описывает происхождение supporting evidence; synthetic остаётся независимым признаком. hotelier_confirmed по умолчанию false: true допустим только для yes на свежем непротиворечивом ответе отеля с дословной цитатой без synthetic/simulation/mock. Статусы, score и порядок поиска от этих новых полей не меняются. Расчётное yes не называется подтверждением отеля, демо-ответ обозначается отдельно.

Answer.disposition: parsed (есть проверяемые поля), unknown (отель прямо сообщил, что не знает), clarification (не удалось достаточно разобрать ответ). Для unknown подтверждение закрывает вопрос без выдуманных claims. Частичный parsed сохраняет только обоснованные поля и уточнение неизвестного. Cabinet может дополнительно вернуть answers и answered_questions для истории; создание нового ответа не означает автоматическое изменение паспорта.

Численная confidence сохраняется в API и служебных экранах. Гостевой интерфейс показывает слова: от 0.8 — «надёжно», от 0.6 до 0.8 — «примерно», ниже 0.6 — «мало данных»; synthetic не получает отметку надёжного подтверждения. Математические вероятности расчёта не являются confidence источника.


Публичные GET /api/runs, /api/runs/{id} и /api/runs/{id}/stream отдают обезличенную проекцию search-прогонов, включая авторизованный просмотр: текст запроса, разбор, подписи людей, rationale, evidence refs и ошибки с исходной фразой не передаются. Сохраняются идентификаторы, порядок шагов, время, длительность, режим и число результатов. Исходная трасса в хранилище не изменяется. Интервью по-прежнему требуют доступа к соответствующему отелю.

Отправка вопроса в гостевом интерфейсе требует отдельного подтверждения. После успешного POST /api/questions идентификатор вопроса сохраняется локально по hotel/characteristic/scope; отмена и ошибка не помечают вопрос отправленным. Отметка видна после перезагрузки и в других кнопках того же контекста. Она означает успешную отправку, а не обещанный срок ответа.


У official_site/room в принятом индексе обязательны положительные bytes ≤ 2 000 000 и SHA-256; url использует HTTPS. HTTP → HTTPS допускается только при идентичности байтов. Размеры и подтверждения записываются при offline-подготовке; runtime не выполняет внешнюю проверку. Формат существующих HotelPhoto/HotelPhotoIndex не расширен.


## Подсказки и краткие служебные результаты

`QuerySuggestion {label, criterion, required=true, fragment?}` — предложение явного добавления условия. `ParsedQuery.suggestions` и `RoomRequest.suggestions` по умолчанию пусты, максимум два предложения. Наличие подсказки не добавляет criterion в must/nice, не меняет ranking и не означает наличие факта. Для группы подсказка остаётся у своего адресата. Существующий запрос без этих полей совместим.

`HotelierSummary {hotel_id, pending_count}` — количество уже существующих pending-вопросов для доступного отеля. Не число уникальных гостей; получение сводки не создаёт вопросы.

`DryRunResult` сохраняет прежние spec_id/results/run_id/valid и добавляет total/processed/matched_count; строки results могут содержать hotel_name/matched. total означает доступный каталог, processed — реально проверенную выборку, matched_count — подходящие в этой выборке. Три проверенных отеля нельзя показывать как полный прогон 247 объектов.


Пробный прогон возвращает только реально проверенные строки: максимум три допущенных отеля с именами. `matched=true` означает свежий `yes` после resolver и assess_hotel для search-условия спецификации. `false` может быть no/partial/unknown и сам по себе не является отрицательным фактом. Подсчёт не сохраняет claims/facts и не публикует характеристику; трасса предпросмотра сохраняется отдельно.

Frozen ranking inputs допускают только добавление одной отдельной строки `display_name` в YAML, если после её удаления SHA-256 исходных байтов ровно совпадает с зафиксированным. Любое изменение порога, evidence, списка файлов, многострочного или дублирующего поля остаётся несовместимым. Список файлов с таким изменением имени включается в отчёт; labels не пересоздаются. Совпадение materialized input_facts проверяется и явно отражается в метрике.

## Адресное дополнение seed v1

Исходные `hotels.json`, `hotels.csv.gz`, `sources.json` и `sources.csv.gz` остаются неизменными. Дополнение не участвует в вычислении ranking gold.

- `data/seed/admin_districts.geojson.gz`: FeatureCollection с `schema_version=1`, полным Source в `source` и четырьмя исходными полигонами OSM. У feature — `id=osm-relation-…`, geometry, properties с исходным name, коротким display_name, osm_id, source_url и retrieved_at. Сохранённый снимок позволяет работать без сервиса геокодирования; runtime в сеть не обращается.
- `data/seed/hotel_addresses.json`: envelope `schema_version=1`, `method=osm_building_containment_and_district_boundaries_v1`, `region`, `sources: Source[]`, `entries: []`, `counts`, `limitations`. Entry содержит `hotel_id`, `lat`, `lon`, `base_address`, `address`, `precision` (`source`/`building`/`street`/`area`), `district`, `district_display_name`, `source`, `method`, `evidence`, `confidence`, `observed_at`, `valid_until`. Evidence содержит ссылки на снимки, id полигона района, id здания и исходные addr:street/addr:housenumber, если они использованы. Полные входные записи/поля и их контрольные суммы допустимы в evidence.
- Улица и дом берутся только из связанного здания OSM, содержащего координаты отеля; соседнее здание и ближайшая дорога адресом не становятся. При отсутствии адресных тегов даётся район, содержащий точку. Нет однозначного района — сохраняется исходное неизвестное. Вход в отель не считается проверенным.
- При старте обновляется только адресная проекция соответствующего id и координат. Новый или изменённый вручную адрес не перезаписывается. Доступны `tags.address_precision`, `tags.address_district` и `tags.address_evidence`; остальные данные отеля сохраняются. Изменения БД выполняются с параметрами и сравнением ожидаемого payload, в порядке id. Дополнительный Source регистрируется отдельно, без перезаписи исходных frozen-таблиц.

## Подготовленные дополнительные наблюдения

`Scope.landmark_id` — необязательный идентификатор объекта из `data/landmarks/sochi.yaml`, шаблон `[a-z][a-z0-9_]{1,63}`. Это цель проверки видимости, отдельная от стороны и этажа номера. Разные достопримечательности не являются противоречащими утверждениями об одном виде. В критерии `scope.landmark_id` выбирает конкретную цель; отсутствие выбирает любой подтверждённый объект. У обычных срезов поле отсутствует в сериализованном ответе, даже при `exclude_none=false`, поэтому прежние ключи срезов и frozen input facts не меняются.

Инкрементальные данные имеют префикс `data/seed/tz_` и отдельный `tz_manifest.json`. Каждая стадия содержит Source[], JSONL.gz с Claim, сжатые observations и manifest с SHA/порогами/ограничениями. Unknown-наблюдение не превращается в отрицательное. Физические входы для runtime — только сохранённые seed/geo/spec/landmark артефакты; PBF и токены в образ не нужны. Подготовленные расчёты не становятся активными до публикации YAML и подключения loader. Исходные файлы seed/geo сохраняются; новые данные добавляются отдельными стадиями.

Аэропорт: расстояния/коридор — геометрическая оценка, не измерение шума и не расписание рейсов. Транспортная связность OSM не подтверждает расписание; неизвестные 0/1 пересадки не означают, что маршрута нет. Архивные construction-теги без специальной свежей проверки не являются утверждением о текущей стройке и не понижают оценку тишины. Историческая дата сохраняет исходный тип тега или свойства; дата открытия не переименовывается в дату постройки.

## Подготовленные сведения о месте

`backend.tz_seed.prepare_seed` проверяет `data/seed/tz_manifest.json`, размеры и SHA-256 всех перечисленных артефактов, модели Source/Claim, ссылки на отели и характеристики, срок действия и хеши входов evidence. Проверка происходит до записывающей транзакции. В runtime не нужны PBF, сеть, ключи или пакет pipeline. Отсутствующий дополнительный набор допустим; повреждённый набор останавливает запуск с причиной. Загрузка Source и Claim только вставляет отсутствующие ID: уже выученные alpha/beta, ответы и действия гостей не перезаписываются.

Дополнительное наблюдение существующей характеристики имеет `method=tz_supplement:<id>@1` и `evidence.inputs.supplementary_observation=true`. Оно хранит собственные поля, доказательства и надёжность отдельно от прежнего факта. Например, рассчитанный пеший путь до площадки не наследует уверенность прямого расстояния и не заменяет неизвестное отсутствие маршрута прямой линией.

`GET /api/hotels/{id}/geo` дополнительно принимает `month` (1–12, по умолчанию 9), `facade` (сторона из Side) и `floor` (1–100). Прежние вызовы без параметров сохранены. Ответ может содержать `landmark_targets` и `landmark_rays` (GeoJSON FeatureCollection), `landmark_metadata` (сведения о целях с неизвестной высотой) и `warnings`. Линии — сохранённые проверки видимости конкретной цели, стороны и этажа; без выбранного этажа или после изменения окружения линии не выдаются. Наличие точки на карте не доказывает видимость. Фишт с известным контуром, но без подтверждённой высоты остаётся неизвестной целью, без луча и без положительного/отрицательного расчёта.

Компактный профиль сохраняет значения, статусы, стороны, этажи, месяцы, цель и причины неизвестности. Длинные индексы маршрута/препятствий, годовые климатические samples и исходные параметры расчёта доступны через `GET /api/hotels/{id}/facts/{fact_id}/evidence`. Поисковый snapshot сохраняет ссылки на evidence, источники и цитаты; повторяющиеся ограничения метода загружаются этим же запросом. Score, порядок, verdict и provenance при такой проекции не меняются. Общие оговорки и фактические ограничения запроса остаются в карточке.

Паспорт хранит цель вида в scope.landmark_id. Разные цели могут относиться к одной стороне/этажу и физически совместимы, но положительный факт по одной цели не подтверждает другую. Изменение окружения помечает прежний расчёт цели stale до пересчёта; уведомление паспорта проверяется по новому состоянию до публикации события.

SearchRequest.compact по умолчанию false сохраняет прежнюю диагностическую выдачу до limit. Гостевой frontend передаёт compact=true: limit — верхняя граница числа карточек; выдаётся полный упорядоченный префикс, помещающийся в 390 000 Б JSON. Это оставляет запас до 400 000 Б, сохраняет все сведения карточки и настоящий total. Счётчик интерфейса использует фактически выданное число. Если не помещается одна карточка, API отвечает 413 с русским объяснением; данные и доказательства не обрезаются. Score, группа и порядок префикса не меняются.

CriterionVerdict.context_fact_ids/context_evidence_refs содержат отдельные сведения, влияющие на оценку или оговорку, например близость аэропорта при запросе тишины. Они не подтверждают основной критерий и не участвуют в выборе совместимой стороны/этажа; fact_ids/evidence_refs сохраняют только основания самого условия. Гостевой «Риск по карте» лениво открывает полный факт контекста с источниками. Надёжность и статус основного условия не подменяются свойствами контекста.

## Подтверждение гипотезы и исправление ответа (2026-10-28)

`Question.hypothesis_update` — необязательное значение `FieldUpdate` одного факта с его собственным scope, которому соответствует показанный текст гипотезы. Это контекст вопроса, не цитата отельера. Короткое «Да» подтверждает именно эту гипотезу, включая её отрицательное значение; цитатой нового ответа остаётся дословное «Да». Неуказанные сведения и численные измерения таким ответом не выводятся.

`POST /api/answers/{answer_id}/revise` переоткрывает исходный вопрос того же отеля и характеристики; требует права на отель. Новая версия ответа сохраняет историю и замещает прежние наблюдения через `supersedes_ids`. Другие вопросы не закрываются, лишняя строка очереди не создаётся.

`POST /api/hotelier/{hotel_id}/questions/{characteristic_id}` принимает `HotelierQuestionRequest {scope?, fact_id?}`: представитель отеля может открыть форму по расчётному факту. Факт должен относиться к этому отелю и характеристике. Это действие кабинета, а не фиктивный вопрос гостя.

Аннотация `value_schema.x-hotelier` задаёт содержательные поля (`substantive_fields`) и ограниченное правило наличия (`availability_from: {field, value, unless?}`). Непустые явные значения, включая `false` и `0`, являются сообщёнными данными; они не превращают неизвестные соседние поля в известные. Для разрешённых видов животных `available=true` выводится только из явного списка и при отсутствии нерешённого ограничения периода. Удалённые или неизвестные поля не выдумываются.

Советы `/room-advice` дополнительно могут содержать один совет по номерам из ответа отеля: итого не более пяти элементов, включая прежние четыре расчётные цели. Номера и их признаки сохраняются словами отеля и с собственными `fact_ids`; пустой `scope` такого совета не назначает сторону, этаж или положение в RoomLab. Ответ отеля о номерах 201–210 не доказывает их этажность, наличие мест или соседство. При исправлении ответа прежняя версия и её цитата остаются в истории, действующие claims заменяются только в контексте того же вопроса.

Будущий срок поездки за пределами `valid_until` создаёт нейтральную перепроверку паспорта, а не событие угрозы. Дата перепроверки одна на паспорт. Реальное противоречие, изменение источника или истечение уже действующих данных сохраняют отдельную оценку риска. Выбранные сторона, этаж и месяц, а также номер и комментарий подтверждения отеля сохраняются при повторном чтении.

`SearchHit.why_higher` — необязательная человеческая причина преимущества лидера. Если преимущество обусловлено демонстрационным ответом, происхождение прямо указано в тексте; это не новое доказательство значения и не изменение score.
