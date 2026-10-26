# S7: независимый quality / security / reproducibility audit

Дата: 26.10.2026. Первый этап: только чтение кода, данных и GET API; изолированные синтетические проверки без записи в живую БД. На следующем этапе оркестратор поручил S7 исправление dev toolchain и audit script в явно выделенной зоне; результат описан ниже. Мутаций живого API S7 не выполнял.

Снимок main при начале аудита: `a83901add8631c7a93659d8950ec36c6d256392d` плюс текущие правки оркестратора. Финальное geo проверено из `okno-wt-geo`, commit `2379c023a5790558ac96816e21ec155486dc36aa`. Интеграция и исправления продолжаются; статусы ниже не являются разрешением финального релиза.

## Найденные дефекты

P0 не найден. Четыре воспроизводимых P1 переданы оркестратору сразу после проверки.

### P1-1. Повторный POI collect конфликтует с immutable ledger

**Статус:** закрыто. Исправлено в main `4479b6e`; независимые unit-проверки и два разрешённых collect на живой БД после startup bootstrap прошли. Подробности ниже.

- Место: `backend/tools/registry.py:152`–155, вызовы `backend/agents/orchestrator.py:33`, `backend/api/studio.py:93`; первоначальные POI создаёт `backend/seed.py:91`–104.
- Триггер: повторно выполнить `poi_nearby` для одного отеля / kind / radius на неизменном снимке. Даже первый полный ручной collect после seed повторяет уже существующие POI.
- Проверка: два изолированных вызова дали один ID `poi-8ba736dc89f57cf9c8bacef3`, при разных run_id. `Repository.insert_claims` использует обычный INSERT.
- Последствие: `UniqueViolation`, прогон / backfill становится failed после частично записанных предшествующих шагов.
- Минимальное исправление: существующее неизменное наблюдение переиспользовать как cache hit либо выдавать новый ID с supersedes; добавить проверку повторного полного collect после bootstrap.

### P1-2. Composite derive соединяет несовместимые номера

**Статус:** закрыто после дополнительного исправления main `0de6777`. Разные facade / floor / room_type / month корректно разделены; обнаруженный при retest `4479b6e` обход для двух общих hotel-wide inputs также исправлен и независимо перепроверен. Подробности ниже.

- Место: `backend/tools/registry.py:163`–169, 179–187.
- Триггер: в студии составить `sea_view.available and quiet_night.available`; sea_view подтверждён только для W, quiet_night — только для E.
- Проверка: изолированные входные facts с confidence 0.9 дали `available=true`, confidence 0.9 и общий hotel scope. Совместимого номера во входах нет.
- Последствие: новая характеристика обходит same-room инвариант ranker; поиск может показать «все условия подтверждены» для невозможного сочетания.
- Минимальное исправление: вычислять выражение на пересечении facade × floor × room_type × month; до реализации — abstain при room-scoped inputs, которые нельзя безопасно свести к одному scope. Наследовать scope, TTL, evidence и synthetic входов.

### P1-3. Качественное отрицание шумоизоляции превращается в positive

**Статус:** закрыто. Исправление S4 `57e8656` и последующие fixes интегрированы в main; S7 перепроверил обе отрицательные фразы → available=false, «не плохая» → abstain, сохранение отрицания после предложения о двойных окнах и неоднозначные ночные утверждения. Прежний synthetic corpus 62/62 PASS; targeted main regressions также PASS.

- Место: `backend/agents/answer_parser.py:53`–65, 161–166; подтверждение — `backend/api/hotelier.py:181`–185.
- Триггер: ответ «Шумоизоляция плохая.» или «У нас слабая шумоизоляция.».
- Проверка: обе фразы дали `soundproofing.available=true`, confidence 0.85, без unknown / clarification.
- Последствие: после подтверждения отрицательная по смыслу цитата становится confirmed hotelier_override и положительным результатом поиска.
- Минимальное исправление: явно обработать плохая / слабая / низкая и аналогичные формы; неоднозначные оценки переводить в unknown с уточнением. Регрессии нужны на обе приведённые фразы.

### P1-4. JSON Schema разрешает серверную загрузку произвольного $ref

**Статус:** закрыто. Исправления `fbf997d` и S4 `31de91d` проверены в main: 9 unsafe schemas на двух границах отвергнуты, попыток сети 0; local reference проходит. Подробности ниже.

- Место: `backend/agents/spec_compiler.py:14`, `backend/api/studio.py:41`, `backend/agents/orchestrator.py:31`, `backend/api/hotelier.py:148`.
- Триггер: value_schema с `$ref: http://127.0.0.1:12345/...` проходит validate_spec; при проверке значения jsonschema использует стандартный registry с автоматической загрузкой URI.
- Проверка: `validate_spec` принял такой schema. При `jsonschema.validate` с перехваченным `urllib.request.urlopen` зарегистрирована одна попытка HTTP GET. Живые API не мутировались, внешний адрес не запрашивался.
- Последствие: публичная demo Studio позволяет инициировать запросы с сервера к внутренним URL; URI со схемой file также попадают в стандартный загрузчик. Белый список инструментов не закрывает этот путь.
- Минимальное исправление: рекурсивно запрещать все non-local `$ref` и `$dynamicRef`, включая nested schemas, либо использовать общий validator с `referencing.Registry` без remote retrieve. Проверить все места валидации пользовательских schemas. Регрессия должна доказать отсутствие попытки сети.

### P2. Метрики покрытия и смешанные периоды

- `backend/quality.py:91`–92 включает synthetic facts в обычное coverage, несмотря на комментарий выше. Изолированный единственный synthetic fact дал known_pairs=1 / total_pairs=1. После demo-change это незаметно меняет покрытие реальных данных. **Статус:** оркестратор разделяет real / synthetic coverage.
- `backend/agents/answer_parser.py:214`–224 в исходном снимке: «Днём шумно, ночью тихо.» давало `quiet_night=false`, confidence 0.85. **Статус:** исправлено в S4 `57e8656`, S7 проверил модуль из git: эта фраза → true, «Ночью шумно, днём тихо.» → false. Финальная интеграция проверяется оркестратором.
- В `backend/quality.py:151` status становится `measured` уже при наличии группы, даже если evaluated=0 и все labels — abstentions. Числовые accuracy/Brier/ECE при этом корректно null. Рекомендуется назвать состояние точнее; это не измеренная accuracy.

## Что инженерно проверено

- `135 passed`: `pytest -q tests backend/tests`; `16 passed`: `pytest -q geo`; `ruff check backend geo pipeline scripts tests` — PASS. Это проверки контрактов и известных примеров, не эмпирическая точность отелей.
- 3 364 open claims и 20 website claims проходят Pydantic Claim. Локальные артефакты существуют; все evidence source_ids зарегистрированы; пропусков provenance не найдено.
- Финальный geo: 20 340 уникальных Claim для 250 отелей; ledger совпадает с claims во всех 250 JSON; evidence.output совпадает с value; все 8 input SHA-256 совпали с финальными входами.
- После интеграции main `2abfd36`: все 510 файлов `data/geo` отслеживаются Git и побайтово совпадают по SHA-256 с проверенным финальным worktree S2; пропусков / расхождений нет.
- Все 250 NPZ открылись с `allow_pickle=False`; массивы только числовые. Все 250 JSON evidence artifacts существуют. Максимальный файл geo — 19 964 021 байт, ниже 50 МБ.
- Quiet_night во всём финальном geo имеет confidence 0.38. По коду и регрессиям positive остаётся partial, negative — unknown; гарантии сна нет.
- Основной ranker пересекает facade / floor / room_type / month. Проверены противоположные фасады, непересекающиеся этажи, разные категории номера и месяцы. Общая цитата о виде не подтверждает конкретный номер; отсутствие / stale / disputed не превращается в yes.
- Resolver фильтрует spec_version, collect проставляет текущую версию. `Repository.replace_facts` удаляет и вставляет facts отеля одной транзакцией. Изолированная проверка на реальных 12 старых sea_view диапазонах demo-hotel, включая 5 positive, плюс synthetic replacement с supersedes_ids всех старых: один новый negative fact, ноль активных старых positive, ноль старых supporting IDs.
- AST derive запрещает calls / imports / subscripts / private attributes; отсутствуют eval / exec. Параметры инструментов и имена таблиц ограничены whitelist, SQL значения bind.
- GET без bearer к кабинету — 401. Прямые URL simulator profiles / llm_cache и encoded traversal — 404. `/.env` отдаёт HTML SPA, не файл окружения. Утечки ключей в доступных проверенных ответах не найдено.
- Compose: 8080, linux/amd64, app UID 10001, healthchecks и depends_on service_healthy; отдельные persistent volumes Postgres / runtime. Durable geo копирует JSON / NPZ в runtime до записи ссылок. В startup-коде только локальные миграции, seed и resolver, сетевых загрузок данных / LLM нет.
- Сохранённые отчёты аудитов зависимостей: runtime Python 0 vulnerabilities, npm production 0, pipeline dependencies 0; scanner_errors=false. S7 не запускал повторный сетевой dependency audit.
- GET `/health` и `/api/status` PASS: 250 реальных отелей, 16 активных specs, offline. Версия seed `sochi-2026-10-26-ab-v2`.

## Разметка, leakage и неизмеренные риски

- 20 unique weak_website labels, 20 соответствующих claims и artifacts; цепочки label → artifact → claim совпали 20/20. Максимум 25 слов цитаты. В fetch log 127 запросов, 32 автоматических candidates; после review осталось 20.
- Домены меток совпадают с website исходных hotel records. S7 не перепроверял личности владельцев / адреса через новый веб-сбор. Групповые апартаменты, цитаты отзывов и общие формулировки остаются слабым evidence.
- `backend/quality.py:110`–124 исключает quote-backed и synthetic facts при оценке weak_website. Цитата не оценивает саму себя через Resolver. На момент GET quality: manual_label_count=0, weak evaluated=0, abstentions=20, accuracy/Brier/ECE=null — это честная абстенция до финального geo import.
- Согласие с weak website — согласие с рекламной цитатой, не empirical accuracy. Для hotel-wide вида это лишь существование хотя бы части видовых номеров. Photo / panorama manual ground truth пока 0.
- Не измерены: реальная точность LOS, шумовых диапазонов и пеших маршрутов; калибровка confidence на независимых сильных метках; nDCG@5; качество живого LLM provider. Monte Carlo измеряет устойчивость модели к допущениям.
- Чистый clone / пустой Docker cache / воспроизводимость финального orchestration, живые мутации Passport / change / Studio и сохранность после recreate проверяет оркестратор. S7 намеренно не запускал их параллельно со smoke / видео. Pending final docs и известный worker404 до rebuild не записаны как новые дефекты.

## Повторная проверка

Первый retest на main `2bddbff` поверх `4479b6e`, с SSRF fix `fbf997d` и S4 `31de91d`; финальный targeted retest после дополнительного исправления — на `0de6777c2d44203ce89240dc07d74626db6db9b3`. Код и тесты S7 не менял; по отдельному разрешению выполнены ровно два POST `/api/runs` для `night_pharmacy`, без удаления или подмены claims.

```sh
.venv/bin/python -m pytest -q tests/test_registry_invariants.py tests/test_contracts.py \
  tests/test_resolver.py tests/test_derive.py backend/tests/test_hotelier.py \
  backend/tests/test_api_integration.py backend/tests/test_search.py
```

Первый targeted run: 56 tests, 104 subtests PASS. Финальный run с поисковыми регрессиями: **128 tests, 104 subtests PASS**. Дополнительные независимые synthetic checks:

- Composite: opposite facade, disjoint floor intervals, разные room_type, разные month и broad-positive view + specific second input → abstain. Совместимые W / этажи 2–5 и W / 3–6 / сентябрь → W / 3–5 / сентябрь, с сохранённым synthetic=true.
- Parser: «Шумоизоляция плохая» / «У нас слабая шумоизоляция» → false; «не плохая» → unknown; «Шумоизоляция плохая. Окна двойные» сохраняет false и windows=double; «Днём шумно, ночью тихо» → true; «Ночью тихо, но шумно» → unknown.
- SSRF: http, https, file, protocol-relative URI, relative URI, `$dynamicRef`, `$id`, nested property reference и nested allOf reference проверены и через CharacteristicSpec, и напрямую через `validate_value`. Все 18 unsafe случаев отвергнуты, `urllib.request.urlopen` не вызван ни разу. Корректный `#/$defs/...` reference принят. Hotelier regression дополнительно доказывает отказ до любых claims / sources writes.

**Живая БД, P1-1:** после GET `/health` 200 выбран не demo-hero, `osm-node-10118233110` (Swissotel Resort Сочи Камелия). Startup bootstrap уже содержал `poi-dd76c2418f474c10c9024059`.

| Проверка | Run ID | Новый Claim ID | Результат |
| --- | --- | --- | --- |
| Первый повторный collect | e58e9097-a2d9-4134-989d-a137568380de | e24d9ec7-949d-4010-97bc-a579134c1633 | completed, supersedes bootstrap |
| Второй повторный collect | 31d5218e-a462-4fdb-a44a-2ad2cbbcd5c6 | 56f95718-2491-4a2b-93b8-bea632d819da | completed, supersedes оба старых |

Число POI claims 1 → 2 → 3; final Fact.supporting_claim_ids содержит только последний ID. Все три наблюдения сохраняют `observed_at=2026-10-25T21:21:21Z`, `valid_until=2027-01-23T21:21:21Z`; значение не выдано за новую загрузку. Synthetic=false соответствует повторному расчёту по реальному неизменному OSM snapshot. Удалений не было.

**Дополнение к P1-2, найденное при первом retest:** `backend/tools/scoped_inputs.py:45` в `4479b6e` проверял общий положительный view только когда merged scope уже имел комнатные поля. Два hotel-wide inputs `sea_view.available=true` и `quiet_night.available=true` с confidence 0.85 проходили вместе. Независимая явно synthetic проверка derive → Resolver → ranker дала один `quiet_sea` claim, hotel scope, confidence 0.85, `group=all`, synthetic=true. Это объявляло совместимый номер на основе существования вида где-то в отеле. Сообщено оркестратору сразу. **Исправлено в `0de6777`:** положительный view без room scope теперь отвергается независимо от остальных входов; исходный synthetic случай даёт 0 claims и явное warning об отсутствии совместимых данных.

**P2, найденный при первом retest:** `backend/tools/registry.py:184` падал на `max()` пустого inputs для AST-разрешённого `expr='True'`: names пуст, `compatible_inputs` возвращал пустой tuple в одном сочетании. Публикация такого плана завершала бы backfill ошибкой вместо понятной абстенции. **Исправлено в `0de6777`:** empty names → пустой список сочетаний; независимая проверка `expr='True'` даёт 0 claims + warnings, без исключения.

Дополнительно проверено изменение semantics group=all в `0de6777`: предварительный match может считаться supported только при успешном сравнении, полных ограничениях и confidence ≥ 0.6; порог verdict=yes остаётся 0.7. Независимая проверка quiet_night confidence 0.38: positive → almost / partial, negative → unknown, гарантия сна не появляется. Hotel-wide sea_view confidence 0.85 остаётся partial и не даёт group=all. При наличии supported estimate summary явно предупреждает о предварительном расчёте.

Итого финального retest: **все четыре S7 P1 закрыты**; constant-only P2 также закрыт. Другие отмеченные P2 и неизмеренные риски сохраняют свои отдельные статусы. Эмпирическая точность и калибровка не измерялись, synthetic результаты не выданы за полевую проверку.

## Дополнительный аудит dev toolchain

По поручению оркестратора проверен `requirements-dev.lock` без установки пакетов, dependency resolution и изменения lock:

```sh
.venv/bin/pip-audit --require-hashes --no-deps --disable-pip \
  -r requirements-dev.lock -f json --progress-spinner off
```

Результат: exit 1, **10 advisories в 3 пакетах**, scanner завершился штатно. `pip-tools==7.4.1` не имеет находок в этом результате. Дополнительно к запрошенным pip / setuptools найден уязвимый pytest.

| Pin | Advisory | Alias | Fix versions из pip-audit |
| --- | --- | --- | --- |
| pip 24.0 | PYSEC-2026-196 | CVE-2026-8643, GHSA-wf93-45jw-7689 | 26.1.2 |
| pip 24.0 | PYSEC-2026-1795 | CVE-2025-8869, GHSA-4xh5-x5gv-qwph | 25.3 |
| pip 24.0 | PYSEC-2026-1796 | CVE-2026-1703, GHSA-6vgw-5pg2-w6jp | 26.0 |
| pip 24.0 | PYSEC-2026-2875 | CVE-2026-3219, GHSA-58qw-9mgm-455v | 26.1 |
| pip 24.0 | PYSEC-2026-2876 | CVE-2026-6357, GHSA-jp4c-xjxw-mgf9 | 26.1 |
| pip 24.0 | PYSEC-2026-3721 | CVE-2026-13346, GHSA-qwm4-qh6w-59xr | 26.2 |
| setuptools 75.1.0 | PYSEC-2025-49 | CVE-2025-47273, GHSA-5rjg-fvgr-3xxf | 78.1.1 |
| setuptools 75.1.0 | PYSEC-2026-3447 | CVE-2026-59890, GHSA-h35f-9h28-mq5c | 83.0.0 |
| setuptools 75.1.0 | GHSA-xmj3-9gf7-h52w | — | 84.1.0 |
| pytest 8.3.5 | PYSEC-2026-1845 | CVE-2025-71176, GHSA-6w46-j5rx-g56g | 9.0.3 |

Минимальные суммарные версии, закрывающие именно перечисленные advisories: pip 26.2, setuptools 84.1.0, pytest 9.0.3. Это границы из vulnerability service, не подтверждение совместимости toolchain с pip-tools и текущими тестами. Оркестратор отдельно подбирает и проверяет совместимую связку.

Контекст: pip advisories касаются установки / распаковки пакетов; один fallback tarfile advisory смягчается Python с PEP 706, но остаётся находкой исходного lock. Setuptools имеет path traversal при старых путях установки, обход MANIFEST exclude при Unicode normalization на macOS и Windows-specific legacy wheel issue. Pytest advisory касается predictable temporary directories на UNIX. Эти пакеты находятся в dev toolchain; результат не меняет ранее полученный нулевой runtime audit. На диагностическом этапе установки и исправления не выполнялись.

### Устранение dev advisories по отдельному поручению

Обновлены только dev pins и их hash lock: [pip 26.2.2](https://pypi.org/pypi/pip/26.2.2/json), [pip-tools 7.6.2](https://pypi.org/pypi/pip-tools/7.6.2/json), [setuptools 84.2.1](https://pypi.org/pypi/setuptools/84.2.1/json), [pytest 9.1.1](https://pypi.org/pypi/pytest/9.1.1/json), [pip-audit 2.10.1](https://pypi.org/pypi/pip-audit/2.10.1/json). Официальная PyPI metadata проверена: версии non-yanked, Python 3.12 поддерживается, vulnerabilities пусты. Для bootstrap использованы конкретные universal wheels с SHA-256 из PyPI и `--require-hashes`; затем установлен полный dev lock. Об установке в общую `.venv` оркестратор был предупреждён до начала и уведомлён после завершения.

Совместимость compiler проверена фактически:

```sh
.venv/bin/pip-compile --allow-unsafe --generate-hashes --strip-extras \
  --index-url https://pypi.org/simple --output-file requirements-dev.lock requirements-dev.in
.venv/bin/python -m pip install --only-binary=:all: --require-hashes \
  --index-url https://pypi.org/simple -r requirements-dev.lock
.venv/bin/python -m pip check
make audit
.venv/bin/python -m pytest -q tests backend/tests geo
.venv/bin/ruff check scripts/audit.py
```

- Повторная компиляция дала побайтово тот же lock, SHA-256 `c1677ccb971a1a9c541e31b0a7546ca026acb3e1139b48008ce2a552417deffa`.
- `pip check` PASS. Новый pytest: **161 tests и 80 subtests PASS**. Ruff PASS.
- `make audit`: **runtime Python 0, dev Python 0, pipeline Python 0, npm full 0, npm production 0**; все пять scanners exit 0, `scanner_errors=false`, `passed=true`. Исходные 10 dev advisories устранены; оставшихся находок нет.
- Runtime / pipeline lock-файлы не изменены. Dev transitives изменились только из-за нового pip-audit: добавлены tomli / tomli-w, убран toml.
- `scripts/audit.py` теперь всегда запускает все пять независимых scanners; сохраняет отдельный JSON каждого и общий summary с SHA-256 входных locks. Python сканируется с `--require-hashes --disable-pip --strict`, без установки или dependency resolution. Неполный / невалидный JSON, scanner error, timeout, skipped dependency, противоречие exit code и результата блокируют успех. Изменение lock во время scan также блокирует результат.
- Изолированные проверки: 11 сценариев scanner error / skipped dependency / malformed JSON / timeout PASS; полный путь `main()` с синтетическим сбоем dev scanner возвращает exit 1 и записывает summary `scanner_errors=true`, `passed=false`. Сеть и запись артефактов в этих отрицательных проверках были подменены, успешные реальные отчёты не затёрты.
- Политика npm сохранена: блокируют High / Critical, при текущем запуске все уровни дают 0. Политика Python блокирует любую найденную уязвимость во всех трёх locks.
