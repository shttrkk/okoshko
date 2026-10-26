# Статус О!кно

Обновлено: 27.10.2026 03:43 МСК. Интеграционные проверки выполняются после каждого изменения.

`[x]` — проверено; `[~]` — реализовано частично или есть явно указанное ограничение; `[ ]` — ещё не выполнено.

## Интерфейс (26.10, вечер)

- Типографика поднята до читаемой (минимум 11–12 px, основной текст 15–16 px); карта больше не двигается при наведении, зум — Ctrl/⌘ + колесо.
- Профиль отеля сгруппирован по характеристикам: закат «фасад × месяц», вид и шум по сторонам, климат по месяцам; сравнение использует те же сводки.
- Реальные противоречия источников показаны блоком «Где данные спорят»; запрос без условий даёт обзор, а не «условия сошлись».
- Фото: 46 отелей с проверенными фото (8 Commons, 38 сайтов отелей по ссылке), остальные — подписанный вид 3D-модели здания по OSM.
- Автоматический обход кнопок и внутренних ссылок на всех страницах — без ошибок консоли и HTTP.

## Контрольные точки

- Сквозной сценарий уже записан: 11 реальных UI-сцен, 178,9 с, отчёт `docs/checks/demo_video_20261026.json`.
- 27.10 12:00 — повторная контрольная точка; 28.10 18:00 — feature freeze; 29.10 10:00 — дедлайн удалённого submission.
- Порядок исправлений: P0 → P1 → P2. Push выполняет владелец репозитория. История не переписывается.

## Проверки

| ID | Статус | Требование | Проверено / следующий шаг |
|---|---|---|---|
| A1 | [x] | Прототип агентной системы, которая собирает уникальные характеристики отелей **и номеров** (CASE: Формулировка) | 250 отелей, 20 340 гео-claims с фасадами и этажами; 250/250 geo-артефактов валидны. Чистый клон 3e3cc92: API и браузер PASS. |
| A2 | [x] | Источники — открытые данные (OSM, карты высот, публичные датасеты и API) **и отельеры** | OSM, Overture, Copernicus, Open-Meteo, Commons, Wikidata и другие реально использованные источники в /sources. Петля отельера явно синтетическая: 7 завершённых диалогов в сиде, 2 pending. |
| A3 | [x] | Систематизировать, хранить и показывать пользователю | PostgreSQL JSONB, типизированные API; browser S6: поиск, карточка, evidence, compare, cabinet PASS. |
| A4 | [x] | Список отелей, данные, инструменты агентов, логику, интерфейс собираем сами | pipeline/rebuild.py: источники → DEM/DSM → geo → seed; Docker pipeline, hashed lock. Сочи 175 A + 75 B; второй регион изолирован. |
| A5 | [x] | «Агент, который просит LLM угадать — не решение». Нужно знать: откуда агент берёт значение, насколько уверен, как проверить, когда устареет | Claim validation + NOT NULL/checks + immutable trigger; rollback SQL-тест и S7 audit всех geo/open/website claims PASS. Срок действия задаётся одним правилом (ttl_days характеристики; георасчёты — раз в квартал); поставленные claims переизданы с `supersedes_ids`, ни один не истекает до 01.12.2026 (time-travel тесты на 30.10 и 30.11). |
| A6 | [x] | Один регион, проработанный глубоко | Сочи/Адлер, 250 реальных объектов, region YAML и bbox в UI. Неизвестные значения сохранены; близость к морю не подменяет вид из окна. |
| A7 | [x] | Запуск по README без многочасовой загрузки; демо-данные заранее собраны и лежат в репо | Clean clone `9ac03ec`: без .env, уникальный project/пустые volumes, prune, Docker-only PATH. Health 174,98 с, full 249,07 с, smoke/demo-check/browser PASS. |
| A8 | [x] | Если нужен ключ LLM — описать в README и приложить `.env.example` | .env.example LLM_MODE=offline; unit test запрещает сеть без ключа; live provider опционален. |
| B1 | [x] | **Сбор характеристик**: агент сам решает, какие источники и инструменты нужны для конкретной характеристики, и получает значение | Tool Registry + Planner/Executor; S6 browser /agent: running→33 durable SSE steps→completed. Первый старт сам выполняет обычный collect для «Гамма сириус»: 36 шагов planner → tools → resolver → interviewer видны на `/agent` без ручных действий. План в offline берётся из методов YAML; LLM может только переупорядочить разрешённые шаги. |
| B1.1 | [x] | Хотя бы одна характеристика вычисляется по геоданным, а не находится в тексте | DSM/LOS, солнце, приближённый наружный шум, пешие маршруты. Known-answer unit tests и воспроизводимые evidence/input SHA; расчёт не является измерением из номера. |
| B2 | [x] | **Диалог с отельером**: если данных не хватает, агент задаёт вопрос, принимает ответ в свободной форме и раскладывает по полям; отельера можно имитировать | Preview → дословные цитаты → confirm → immutable claims. 7 завершённых synthetic seed-диалогов; tenant isolation и запрет LLM authority fields проверены тестами. |
| B3 | [x] | **Хранение и достоверность**: у каждого значения есть источник, уверенность и дата получения | Typed provenance + SQL constraints; evidence UI проверен S6. Эмпирическая калибровка пока недоступна. |
| B3.1 | [x] | Если источники противоречат друг другу, система это замечает и разбирается | Resolver замечает два типа реальных противоречий: общее «да» сайта/отельера против всех рассчитанных фасадов «нет» (каждый ≥ 0,6, ≥ 3 фасада) и расстояние до пляжа с сайта, более чем вдвое и на 300 м меньше пешего маршрута. На seed — 4 конфликта (Фламинго, Гамма сириус — вид на море; Паруса, Sunrise — пляж): факты disputed, поиск их не подтверждает, вопрос отельеру с reason=conflict, примечание цитирует обе стороны. Плюс 101 квалификация области действия. Метрики слабых меток не меняются (15/21/0,8). |
| B4 | [x] | **Расширяемость**: новую характеристику можно добавить без переписывания системы, например описав её текстом | В записанном UI: «искупаться до завтрака» → compile/dry-run/publish → 250/250 backfill → новый поисковый чип. 251 durable steps, run 08508096-70a5-46aa-93ff-f048f60b6436. «В гуще событий» добавлена одним YAML (`data/characteristics/nightlife.yaml`) и считается для всех 250 отелей при старте. Офлайн-компилятор студии понимает шаблоны «<место> рядом / в N минутах / ближе N м» для 15 категорий OSM; прочий текст получает ошибку со списком шаблонов. |
| B5 | [x] | **Поиск по запросу**: пользователь своими словами описывает, что ему важно («тихий номер с видом на закат недалеко от моря»), получает подходящие отели | make demo-check: Маша ≥3 all с выполненными must/confidence≥0,6, 5 персон и негативные guards. В свежем клоне PASS; три ответа о тишине явно synthetic, реальная noise confidence=0,38. Добавлены: парковка ≤ 500 м и зарядка ≤ 1000 м с порогом в метке, «в гуще событий» / «подальше от баров», «похожий на <отель>» из текста, группа `browse` для нераспознанных запросов и `parsed.no_data` для характеристик без данных в регионе. |
| C1 | [x] | **Объяснение выбора**: почему подходит и в чём не дотягивает («вид на закат есть, но только с 5-го этажа») | Scope-compatible verdicts и evidence refs; tests + browser evidence PASS, confidence не повышаем искусственно. |
| C2 | [x] | **Сравнение** нескольких отелей или номеров по собранным характеристикам | Сравнение 2–4 отелей; GET/POST API и UI. Edited ParsedQuery сохраняет must/nice, пороги, flags, месяц и даты. |
| C3 | [x] | **Кабинет отельера**: видит, что система узнала; подтверждает/исправляет; отвечает на вопросы агента | Signed demo principals: admin и два tenant-аккаунта. Свой отель, quotes/confirm/idempotence; чужой кабинет/паспорт/private trace →403. Live browser smoke `63fc7b7`: 23 HTTP checks, native SSE/logout и tenant selectors PASS. |
| C4 | [x] | **Актуализация**: повторная проверка устаревших данных; реакция на изменения (рядом построили дом — закрыли вид) | FreshnessWatcher и restart recovery; неизменные input SHA не продлевают TTL. На видео: synthetic новый дом → настоящий cached LOS пересчёт → было/стало → 5 уведомлений паспортам. Синтетический «новый 16-этажный дом» перед «Империей» поставляется с seed: было/стало и уведомление демо-паспорту видны сразу; кнопка симуляции работает для любого отеля. |
| C5 | [~] | **Метрики качества**: своя размеченная выборка; точность характеристик, покрытие, доля вопросов к отельеру | 40 слабых website-меток на 40 объектах, 4 частичных parking labels. Manual=0; conditional weak accuracy=0,8 на 15 оценённых/21 abstention, не полевая точность. nDCG пока не измерен. /quality отдельно показывает synthetic parser suite, model baseline и provenance. |
| D1 | [x] | Путешественник: быстро найти отель, чтобы на месте всё оказалось так, как хотел | Запрос → критерии → выдача → evidence/карта → предварительный паспорт без выдуманных дат; реальный браузерный скринкаст 178,9 с. Поиск обычно 0,9–1,4 с, formal p95 не заявлен. |
| D2 | [~] | Островок: знать больше, чем в карточках → точнее подбор, рост конверсии, меньше отмен | Бизнес-сравнение воспроизводимо: радиус ≤500 м даёт 132 объекта; 60 поддержанных моделью compatible море+закат, из них 20 вне радиуса (4 предварительных). Влияние на конверсию/отмены не измерено. |
| D3 | [x] | Отельер: сильные стороны видны нужным гостям; отвечать на вопрос один раз | Кабинет показывает сильные стороны, вопросы и паспорта; confirmed answer переиспользуется Resolver для всех гостей. Индикатор спроса и ответы demo-аккаунтов synthetic. Спрос в кабинете и приоритет вопросов считаются по журналу поиска (запросы с условием, где отель был в выдаче), без синтетической оценки; кабинет возвращает последние такие запросы. |
| E1 | [ ] | Код только в одном репозитории команды в org `ostrovok-hackathon-2026` | URL организации владелец пришлёт после готовности. Текущий origin личный; push в репозиторий организации выполняет владелец. |
| E2 | [x] | Обязательные файлы: `README.md`, `docker-compose.yml` (в корне), `Dockerfile`(ы), `.env.example` | Файлы есть; `docker compose config` и успешная сборка. |
| E3 | [ ] | Тег `submission` на последнем коммите основной ветки в дедлайн | Локальный submission tag ставится на финальный проверенный main; удалённый push делает только владелец. Пока тег не объявлен сданным. |
| E4 | [x] | Нет приватных зависимостей/реестров | `npm ci`, pip --require-hashes из публичных реестров прошли. |
| E5 | [~] | Демо-видео (скринкаст) на общедоступном хостинге, открывается в инкогнито | Локальный demo/okno-demo.mp4: 178,9 с, 1920×1080, 10 205 367 B; 11 сцен, 0 browser/errors/external requests. Публичную ссылку/проверку доступа добавим после получения от владельца. |
| E6 | [~] | README по шаблону: описание, ссылка на видео, быстрый старт от `git clone` до `localhost:8080`, ресурсы, зависимости, переменные окружения с дефолтами, сидирование, маршруты (`/`, `/health`), тестовые учётные данные | Разделы README, быстрый запуск, ресурсы, env, сидирование, маршруты, demo credentials и reuse API добавлены. Ссылки repo/video намеренно плейсхолдеры. |
| E7 | [x] | Всё в Docker; приложение слушает 8080 внутри; проброс `8080:8080` | Compose запущен; `/health` доступен на localhost:8080. |
| E8 | [x] | Multi-stage build, лёгкий образ, без dev-инструментов в рантайме | Multi-stage Docker; runtime image 315 522 627 B (~301 MiB), amd64; UID10001 проверен в чистом клоне. Node/компиляторов в runtime нет. |
| E9 | [x] | Процесс под непривилегированным пользователем | `docker compose exec -T app id`: uid=10001(app), gid=10001. |
| E10 | [x] | Секреты в `.env`/secrets, реальные секреты не закоммичены | scripts/audit_repository.py --history: 0 secret candidates, 0 tracked env/raw/video, 0 tracked files>50 MB. Это эвристический scan, не универсальный DLP. |
| E11 | [x] | `/health` в приложении + healthcheck в Compose; `depends_on` с `condition: service_healthy` | app и db healthy; depends_on service_healthy; `/health` 200. |
| E12 | [x] | `platform: linux/amd64` (собираем на Apple Silicon) | `docker image inspect`: amd64; platform у обоих сервисов. |
| E13 | [x] | Запуск: `docker compose up --build` без аргументов; без ручных доустановок | Exact `docker compose up --build` (`9ac03ec`): no.env, Docker-only PATH, новый project, preexisting volumes=[], PASS; report clean_clone_20261026_noenv.json. Compose больше не использует `env_file` с `required: false` (нужен Compose ≥ 2.24): настройки передаются интерполяцией с дефолтами `.env.example`, `docker compose config` проверен с `.env` и без. |
| E14 | [x] | Развёртывание ≤10 минут на современном ноутбуке | 174,98с до /health с пустым builder cache; полный smoke/demo-check/browser 249,07 с, PASS. Отчёты docs/checks/clean_clone_20261026_noenv*.json. |
| E15 | [x] | Версии базовых образов и зависимостей зафиксированы (не `latest`) | Закреплённые образы; package-lock + requirements.lock c хэшами; greenlet закреплён для amd64. |
| E16 | [x] | Логи в stdout/stderr; `docker compose logs app` показывает причины отказа | `docker compose logs app`: миграция, статус LLM, uvicorn в stdout. |
| E17 | [x] | Сидирование/миграции автоматически на старте (или 1–2 команды в README) | Seed в репозитории, COPY/bootstrap автоматически. Свежая БД startup 46 с; нет сетевых вызовов в boot. Seed-dialogue refs защищены regression-тестом. |
| E18 | [x] | Большие артефакты/модели не в гите; LLM через OpenAI-совместимое API | Сырые PBF/DEM и видео ignored; GeoJSON gzip, NPZ lossless LZMA. Самый большой текущий tracked file 19 964 021 B; полная история сохранена, soft pack target 150 MB не достигнут. |
| E19 | [x] | POSIX-совместимые команды в рантайме контейнера (не bash-специфика) | CMD python; RUN стандартный sh, bash в runtime не используется. |
| E20 | [~] | Рекомендуется: Makefile (`make up/down/seed`), `docker-compose.override.yml`, CI (docker build + линтер), ссылка на развёрнутый сервис | Makefile, dev override, CI только workflow_dispatch, make check/audit/demo-check. Внешний хостинг не настроен и не требуется для локального старта. |

## Текущие проверки и ограничения

- `make check` на clean clone `9ac03ec`: lint/test/audit/smoke/demo-check PASS за 33,49 с; 365 tests +125 subtests. Четыре Python locks и три npm scopes: 0 advisories/scanner errors.
- `scripts/audit_repository.py --history`: 12/12 release guards PASS, полная история сохранена.
- `pytest`: 365 passed + 125 subtests; `ruff`, frontend lint/build PASS после integration 3293368.
- Security: schema whitelist, no shell/network tools from text, exact secret echo filter, signed tenant scope, SSE cookie и logout. Production SSO, capability revocation и универсальная DLP не заявлены; см. SECURITY.md.
- Второй регион интегрирован c7c8ba3/f721305: Геленджик, 30 объектов, 2 376 geo +418 open claims, 14 стадий/1 140,93 с. Geo 373,25 с; warm mean 11,795 с/отель; lifetime RSS 1,81 GB. Sochi seed/geo/tiles не изменялись; readonly/network-none validation обоих регионов PASS.
- Clean clone `9ac03ec` без .env: 174,979 с до health; prune 1,595 с; smoke/demo-check/browser PASS, 249,074 с суммарно. Python/Node отсутствовали в PATH запуска; QA выполнена после готовности продукта.
- pytest-cov и make demo-voice интегрированы; 85% line coverage без кода тестов. README provenance и timing обновлены. Голос и публичные URL предоставляет владелец позже; плейсхолдеры сохраняются.
- Рабочие reviews/Telegram/blueprint/research/AGENTS исключены из tracked tree и build context. Локальные ignored copies и внешний архив okno-working-docs/20261026 сохранены; полная git-история не переписана. Канал новых локальных ревью остаётся доступным.
- Ручная разметка отсутствует; nDCG и полевая точность не придуманы. Реальный независимый третий тип конфликта высот не подтверждён.

## Последние интеграции

- 26.10 19:10: чистый клон 3e3cc92, empty build cache, 139,49 с до health; API smoke, 12 demo-check guards, браузер PASS.
- 26.10 19:20: Итерация проверок принята; сохранение истории, no.env Docker-only gate и нейтральные PRODUCT/ARCHITECTURE документы.
- 26.10 20:12: main 3293368: tenant auth, live-mock injection guards, private Changes projections; Quality model baseline/provenance; timed VOICEOVER и lossless mix helper; frontend XSS regression.

- 26.10 20:20: c7c8ba3/f721305, Геленджик — перенос pipeline и реальные resource measurements. Root Ruff и оба dry-run PASS.

- 26.10 20:25: рабочие материалы архивированы вне product и убраны из tracked tree; нейтральные PRODUCT/ARCHITECTURE/CONTRACTS остаются.

- 26.10 20:33: no.env clean clone `9ac03ec` PASS; образ 315 522 627 B / amd64 / UID 10001. Последующие правки — документация и отчёты, исполняемый продукт не менялся.

- 26.10 20:38: для повторения начального сценария локальный active Compose project — `okno-demo`; прежний `okno` остановлен с сохранёнными volumes/claims/passports. Файл .env локальный/ignored; чистый клон не зависит от этой настройки.

- 26.10 20:42: `make demo-check` на активном `okno-demo`: 12/12 PASS, Маша — 3 all; карточки — 0,642/0,691 с (2 samples, не p95). Сводка `docs/checks/release_acceptance_20261026.json`; после tested `9ac03ec` менялись только документация и отчёты.

- 26.10 21:55: исправления по аудиту требований интегрированы в main; 442 теста и 125 subtests, Ruff PASS, покрытие строк 86%. Стенд `okno-demo` пересобран на существующих томах: старт около 44 с, 4 противоречия, трасса сбора из 36 шагов, сценарий нового дома с 1 уведомлением; `make demo-check` 12/12 PASS. Оценка этажности для 84 отелей без этажей не выполнялась: у 48 нет контура здания, у 35 нет высоты и этажности ни в одном источнике, поэтому оценка была бы выдуманной.

## Ночная итерация

- Исходный `60cd4de`: 450 tests + 125 subtests, 13 demo-check, Chrome desktop/mobile PASS. Поиск warm p95 = 1869 мс; Brevis detail 1 766 069 B / 454 мс p50; compare 3 = 5 040 959 B / 1858 мс p50.
- Компактная проекция и ленивые evidence: Brevis 285 423 B / 113 мс, compare 3 = 800 503 B / 290 мс (единичные wall samples, не p95). Полная проекция API сохранена.
- Языковой корпус: 239 запросов + 120 негативных примеров зафиксированы до настройки. Полный разбор 51,05% → 91,21%, precision/recall критериев 99,01% / 99,01%, ложные критерии 33,33% → 0%. Исходный holdout: 60% полный разбор / 92% критерии; новые слова всё ещё могут дать unmapped.
- Дополнительные 50 запросов написаны отдельно без знакомства с парсером. Первый строгий результат: 40% → 50%, precision/recall 54,55% / 53,33%. Этот набор выявил новые ошибки единиц, числительных и отрицаний; часть эталонов требует семантического разбора. Первый результат и хэши сохранены, эталоны не меняются. Общая точность на произвольной речи не заявляется как 91%.
- Evidence weak ranking gold: 30 запросов (20 development / 10 holdout), human_labeled=false, frozen input/provenance. Weak nDCG agreement: dev 0,9392 → 0,9950 (15/20 judged queries), holdout 0,9541 → 1,0000 (9/10). Это согласие с метками из фактов, не человеческая оценка; после изменений scorer отчёт пересчитывается по тому же gold.
- 12 декоративных WebP: 1 199 348 B суммарно, каждый ≤160 000 B; в UI не подключены.
- Реальная локальная qwen2.5:3b: 239 запросов, бюджет 2,2 с; 236 обращений, 232 timeout, 4 live, 3 regional bypass. Качество filtered/fallback пути равно offline, выигрыша не показано. Отдельный Docker bridge контроль JSON PASS / 2352 мс; пользовательский .env и default offline сохранены.
- Обязательное условие без региональных данных сохраняется в no_data_must и не разрешает группу all. Сходство сохраняет альтернативы, но не считает неизвестный hard подтверждённым. Интерфейс различает hard/nice без данных; числовые границы одной характеристики редактируются отдельно.
- .env и demo/okno-demo.mp4 не менялись; push и переписывание истории не выполнялись.

- 27.10.2026 01:03: feat(geo): add evidence-backed solar and local environment traits; 617 passed, 1 warning, 125 subtests passed in 69.58s (0:01:09); Ruff и frontend lint PASS.

- 27.10.2026 01:04: fix(geo): preserve active scenarios and complete input provenance; 619 passed, 1 warning, 125 subtests passed in 48.56s; Ruff и frontend lint PASS.

- 27.10.2026 01:05: fix(geo): keep beach surface tied to the walking destination; 620 passed, 1 warning, 125 subtests passed in 48.26s; Ruff и frontend lint PASS.

- 27.10.2026 01:06: fix(geo): reject ambiguous solar building associations; 624 passed, 1 warning, 125 subtests passed in 48.13s; Ruff и frontend lint PASS.

- 27.10.2026 01:11: feat(frontend): connect hotel facts maps and related profiles; 624 passed, 1 warning, 125 subtests passed in 48.77s; Ruff и frontend lint PASS.

- 27.10.2026 01:13: feat(frontend): explore scoped room models without changing trip requirements; 624 passed, 1 warning, 125 subtests passed in 31.80s; Ruff и frontend lint PASS.

- 27.10.2026 01:14: test(search): freeze independently authored external intent cases; 624 passed, 1 warning, 125 subtests passed in 45.32s; Ruff и frontend lint PASS.

- Дополнительный ledger: 30 460 claims / 3 748 065 B, 6 YAML, солнечные методы v2. Солнце — 199 объектов (198 non-synthetic + 1 synthetic), пляж с известной поверхностью — 152, парк/сад — 229, rail noise — 73. Wheelchair: 235 маршрутов с доказательствами, подтверждённых доступных маршрутов 0. Height additions 0, исходные NPZ и Hotel.floors сохранены. Перед публикацией выполняется полный набор проверок и пересборка.

- Поставка дополнительного ledger прошла 629 tests + 125 subtests, Ruff и frontend lint; rebuild, demo-check 13 и browser smoke PASS. make eval: 53 838 известных facts, 143 synthetic (0,2656%), 40 слабых меток. Точность на поле не заявлена. Дополнительный Chrome-прогон связей и выбора окна PASS: 16 сценариев, без ошибок браузера.

- После расширения данных compact Brevis вырос до 917 032 B (356 facts): цель <400 КБ временно не выполнена. Следующая итерация переносит повторяющиеся ограничения и artifact URI в ленивое evidence; полные источники сохраняются.

- Расширенная compact карточка Brevis: 393762 B, 356 facts; JSON прошёл HotelDetail validation. Повторные limitations и пустые optional evidence поля догружаются, references и геометрические углы сохранены. Full pytest/Ruff/frontend lint, demo-check 13, browser smoke и 16 сценариев связей/выбора окна PASS.

- 27.10.2026 01:58: fix(search): retain intervals and reject unsupported measurements; 645 passed, 1 warning, 125 subtests passed in 52.05s; Ruff и frontend lint PASS.

- 27.10.2026 02:01: fix(search): interpret numeric and route paraphrases conservatively; 679 passed, 1 warning, 125 subtests passed in 49.65s; Ruff и frontend lint PASS.

- 27.10.2026 02:02: fix(resolver): require complete floor and month coverage for view disputes; 700 passed, 1 warning, 125 subtests passed in 48.91s; Ruff и frontend lint PASS.

- 27.10.2026 02:05: fix(cache): retire trait values when model inputs become invalid; 712 passed, 1 warning, 125 subtests passed in 47.12s; Ruff и frontend lint PASS.

- 27.10.2026 02:06: fix(coverage): retain typed partial fields independently of availability; 736 passed, 1 warning, 125 subtests passed in 50.94s; Ruff и frontend lint PASS.

- Полностью отложенное evidence: Brevis 252 939 B / 356 facts, Sochi Black Sea 389 040 B / 490 facts, Apartments v Mayake 358 057 B / 507 facts. Model roundtrip PASS; полные источники и даты открываются по факту. Проверки: 736 tests +125 subtests, Ruff, frontend lint, rebuild, demo-check13, browser smoke и16 сценариев связей PASS.

- 27.10.2026 02:37: fix(search): distinguish property requests from quoted mentions; 775 passed, 1 warning, 125 subtests passed in 53.66s; Ruff и frontend lint PASS.

- 27.10.2026 02:38: fix(search): exclude neutral request words from unmapped; 782 passed, 1 warning, 125 subtests passed in 50.63s; Ruff и frontend lint PASS.

- 27.10.2026 02:39: fix(search): separate reported states and depicted properties; 813 passed, 1 warning, 125 subtests passed in 48.83s; Ruff и frontend lint PASS.

- 27.10.2026 02:42: fix(ranking): require a confirmed hotel floor domain for room geometry; 833 passed, 1 warning, 125 subtests passed in 48.20s; Ruff и frontend lint PASS.

- 27.10.2026 02:55: feat(contracts): carry requested room scopes with hotel questions; 833 passed, 1 warning, 125 subtests passed in 60.56s (0:01:00); Ruff и frontend lint PASS.

- 27.10.2026 02:56: test(search): freeze independent validation intents and irrelevant contexts; 833 passed, 1 warning, 125 subtests passed in 54.76s; Ruff и frontend lint PASS.

- 27.10.2026 02:57: test(search): freeze independent release intent validation cases; 833 passed, 1 warning, 125 subtests passed in 51.50s; Ruff и frontend lint PASS.

- 27.10.2026 02:58: docs(checks): measure six traits on cached Gelendzhik region; 833 passed, 1 warning, 125 subtests passed in 47.17s; Ruff и frontend lint PASS.

- 27.10.2026 03:19: fix(search): resolve bounded English similarity requests; 865 passed, 1 warning, 125 subtests passed in 55.20s; Ruff и frontend lint PASS.

- 27.10.2026 03:24: feat(map): expose exact fact-linked nearby place points; 919 passed, 1 warning, 125 subtests passed in 55.71s; Ruff и frontend lint PASS.

- 27.10.2026 03:28: feat(data): add offline OSM property internet observations; 932 passed, 1 warning, 125 subtests passed in 53.89s; Ruff и frontend lint PASS.

- 27.10.2026 03:32: fix(frontend): refresh open fact views after data changes; 993 passed, 1 warning, 125 subtests passed in 57.71s; Ruff и frontend lint PASS.

- 27.10.2026 03:37: feat(frontend): keep selected room scope in hotel questions; 1017 passed, 1 warning, 125 subtests passed in 51.27s; Ruff и frontend lint PASS.

- 27.10.2026 03:43: feat(quality): separate weak ranking and authored intent measurements; 1060 passed, 1 warning, 125 subtests passed in 56.40s; Ruff и frontend lint PASS.
