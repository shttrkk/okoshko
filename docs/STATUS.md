# Статус О!кно

Обновлено: 26.10.2026 20:24 МСК. Интегрированы scoped demo auth, защита LLM и Quality baseline. Python: 365 tests + 125 subtests PASS; frontend lint/build PASS. Следующий P0 — повтор чистого запуска без `.env`.

`[x]` — проверено; `[~]` — реализовано частично или есть явно указанное ограничение; `[ ]` — ещё не выполнено.

## Контрольные точки

- Сквозной сценарий уже записан: 11 реальных UI-сцен, 178,9 с, отчёт `docs/checks/demo_video_20261026.json`.
- 27.10 12:00 — повторная контрольная точка; 28.10 18:00 — feature freeze; 29.10 10:00 — дедлайн удалённого submission.
- Текущая волна: REVIEW005, P0 → P1 → P2. Все push выполняет владелец. История не переписывается.

## Проверки

| ID | Статус | Требование | Проверено / следующий шаг |
|---|---|---|---|
| A1 | [x] | Прототип агентной системы, которая собирает уникальные характеристики отелей **и номеров** (CASE: Формулировка) | 250 отелей, 20 340 гео-claims с фасадами и этажами; 250/250 geo-артефактов валидны. Чистый клон 3e3cc92: API и браузер PASS. |
| A2 | [x] | Источники — открытые данные (OSM, карты высот, публичные датасеты и API) **и отельеры** | OSM, Overture, Copernicus, Open-Meteo, Commons, Wikidata и другие реально использованные источники в /sources. Петля отельера явно синтетическая: 7 завершённых диалогов в сиде, 2 pending. |
| A3 | [x] | Систематизировать, хранить и показывать пользователю | PostgreSQL JSONB, типизированные API; browser S6: поиск, карточка, evidence, compare, cabinet PASS. |
| A4 | [x] | Список отелей, данные, инструменты агентов, логику, интерфейс собираем сами | pipeline/rebuild.py: источники → DEM/DSM → geo → seed; Docker pipeline, hashed lock. Сочи 175 A + 75 B; второй регион изолирован. |
| A5 | [x] | «Агент, который просит LLM угадать — не решение». Нужно знать: откуда агент берёт значение, насколько уверен, как проверить, когда устареет | Claim validation + NOT NULL/checks + immutable trigger; rollback SQL-тест и S7 audit всех geo/open/website claims PASS. |
| A6 | [x] | Один регион, проработанный глубоко | Сочи/Адлер, 250 реальных объектов, region YAML и bbox в UI. Неизвестные значения сохранены; близость к морю не подменяет вид из окна. |
| A7 | [x] | Запуск по README без многочасовой загрузки; демо-данные заранее собраны и лежат в репо | Первый clean clone с пустым build cache: 139,49 с до health, smoke/demo-check/browser PASS. Повтор без .env и с Docker-only PATH готовится. |
| A8 | [x] | Если нужен ключ LLM — описать в README и приложить `.env.example` | .env.example LLM_MODE=offline; unit test запрещает сеть без ключа; live provider опционален. |
| B1 | [x] | **Сбор характеристик**: агент сам решает, какие источники и инструменты нужны для конкретной характеристики, и получает значение | Tool Registry + Planner/Executor; S6 browser /agent: running→33 durable SSE steps→completed. |
| B1.1 | [x] | Хотя бы одна характеристика вычисляется по геоданным, а не находится в тексте | DSM/LOS, солнце, приближённый наружный шум, пешие маршруты. Known-answer unit tests и воспроизводимые evidence/input SHA; расчёт не является измерением из номера. |
| B2 | [x] | **Диалог с отельером**: если данных не хватает, агент задаёт вопрос, принимает ответ в свободной форме и раскладывает по полям; отельера можно имитировать | Preview → дословные цитаты → confirm → immutable claims. 7 завершённых synthetic seed-диалогов; tenant isolation и запрет LLM authority fields проверены тестами. |
| B3 | [x] | **Хранение и достоверность**: у каждого значения есть источник, уверенность и дата получения | Typed provenance + SQL constraints; evidence UI проверен S6. Эмпирическая калибровка пока недоступна. |
| B3.1 | [~] | Если источники противоречат друг другу, система это замечает и разбирается | Resolver обнаруживает противоречия и пересечения scope, создаёт вопрос и делит диапазоны. 109 реальных scope qualifications; 3 независимых реальных value-conflict типа не подтверждены. OSM-height vs DSM на тех же OSM входах не выдаём за независимую проверку. |
| B4 | [x] | **Расширяемость**: новую характеристику можно добавить без переписывания системы, например описав её текстом | В записанном UI: «искупаться до завтрака» → compile/dry-run/publish → 250/250 backfill → новый поисковый чип. 251 durable steps, run 08508096-70a5-46aa-93ff-f048f60b6436. |
| B5 | [x] | **Поиск по запросу**: пользователь своими словами описывает, что ему важно («тихий номер с видом на закат недалеко от моря»), получает подходящие отели | make demo-check: Маша ≥3 all с выполненными must/confidence≥0,6, 5 персон и негативные guards. В свежем клоне PASS; три ответа о тишине явно synthetic, реальная noise confidence=0,38. |
| C1 | [x] | **Объяснение выбора**: почему подходит и в чём не дотягивает («вид на закат есть, но только с 5-го этажа») | Scope-compatible verdicts и evidence refs; tests + browser evidence PASS, confidence не повышаем искусственно. |
| C2 | [x] | **Сравнение** нескольких отелей или номеров по собранным характеристикам | Сравнение 2–4 отелей; GET/POST API и UI. Edited ParsedQuery сохраняет must/nice, пороги, flags, месяц и даты. |
| C3 | [x] | **Кабинет отельера**: видит, что система узнала; подтверждает/исправляет; отвечает на вопросы агента | Signed demo principals: admin и два tenant-аккаунта. Свой отель, quotes/confirm/idempotence; чужой кабинет/паспорт/private trace →403. Совместный live browser smoke новой auth-сборки ожидается. |
| C4 | [x] | **Актуализация**: повторная проверка устаревших данных; реакция на изменения (рядом построили дом — закрыли вид) | FreshnessWatcher и restart recovery; неизменные input SHA не продлевают TTL. На видео: synthetic новый дом → настоящий cached LOS пересчёт → было/стало → 5 уведомлений паспортам. |
| C5 | [~] | **Метрики качества**: своя размеченная выборка; точность характеристик, покрытие, доля вопросов к отельеру | 40 слабых website-меток на 40 объектах, 4 частичных parking labels. Manual=0; conditional weak accuracy=0,8 на 15 оценённых/21 abstention, не полевая точность. nDCG пока не измерен. /quality отдельно показывает synthetic parser suite, model baseline и provenance. |
| D1 | [x] | Путешественник: быстро найти отель, чтобы на месте всё оказалось так, как хотел | Запрос → критерии → выдача → evidence/карта → предварительный паспорт без выдуманных дат; реальный браузерный скринкаст 178,9 с. Поиск обычно 0,9–1,4 с, formal p95 не заявлен. |
| D2 | [~] | Островок: знать больше, чем в карточках → точнее подбор, рост конверсии, меньше отмен | Бизнес-сравнение воспроизводимо: радиус ≤500 м даёт 132 объекта; 60 поддержанных моделью compatible море+закат, из них 20 вне радиуса (4 предварительных). Влияние на конверсию/отмены не измерено. |
| D3 | [x] | Отельер: сильные стороны видны нужным гостям; отвечать на вопрос один раз | Кабинет показывает сильные стороны, вопросы и паспорта; confirmed answer переиспользуется Resolver для всех гостей. Индикатор спроса и ответы demo-аккаунтов synthetic. |
| E1 | [ ] | Код только в одном репозитории команды в org `ostrovok-hackathon-2026` | URL организации владелец пришлёт после готовности. Текущий origin личный; root его не меняет и не пушит. |
| E2 | [x] | Обязательные файлы: `README.md`, `docker-compose.yml` (в корне), `Dockerfile`(ы), `.env.example` | Файлы есть; `docker compose config` и успешная сборка. |
| E3 | [ ] | Тег `submission` на последнем коммите основной ветки в дедлайн | Локальный submission tag ставится на финальный проверенный main; удалённый push делает только владелец. Пока тег не объявлен сданным. |
| E4 | [x] | Нет приватных зависимостей/реестров | `npm ci`, pip --require-hashes из публичных реестров прошли. |
| E5 | [~] | Демо-видео (скринкаст) на общедоступном хостинге, открывается в инкогнито | Локальный demo/okno-demo.mp4: 178,9 с, 1920×1080, 10 205 367 B; 11 сцен, 0 browser/errors/external requests. Публичную ссылку/проверку доступа добавим после получения от владельца. |
| E6 | [~] | README по шаблону: описание, ссылка на видео, быстрый старт от `git clone` до `localhost:8080`, ресурсы, зависимости, переменные окружения с дефолтами, сидирование, маршруты (`/`, `/health`), тестовые учётные данные | Разделы README, быстрый запуск, ресурсы, env, сидирование, маршруты, demo credentials и reuse API добавлены. Ссылки repo/video намеренно плейсхолдеры. |
| E7 | [x] | Всё в Docker; приложение слушает 8080 внутри; проброс `8080:8080` | Compose запущен; `/health` доступен на localhost:8080. |
| E8 | [x] | Multi-stage build, лёгкий образ, без dev-инструментов в рантайме | Multi-stage Docker; runtime image 315 513 186 B (~301 MiB) до последней auth/quality пересборки. Node/компиляторов в runtime нет. |
| E9 | [x] | Процесс под непривилегированным пользователем | `docker compose exec -T app id`: uid=10001(app), gid=10001. |
| E10 | [x] | Секреты в `.env`/secrets, реальные секреты не закоммичены | scripts/audit_repository.py --history: 0 secret candidates, 0 tracked env/raw/video, 0 tracked files>50 MB. Это эвристический scan, не универсальный DLP. |
| E11 | [x] | `/health` в приложении + healthcheck в Compose; `depends_on` с `condition: service_healthy` | app и db healthy; depends_on service_healthy; `/health` 200. |
| E12 | [x] | `platform: linux/amd64` (собираем на Apple Silicon) | `docker image inspect`: amd64; platform у обоих сервисов. |
| E13 | [x] | Запуск: `docker compose up --build` без аргументов; без ручных доустановок | Первый clean clone exact docker compose up --build PASS. Уточнённый REVIEW005 gate: без .env, Docker-only PATH и уникальные пустые volumes — следующий прогон. |
| E14 | [x] | Развёртывание ≤10 минут на современном ноутбуке | 139,49 с до /health с пустым build cache; весь smoke/demo-check/browser прогон 212,29 с. Отчёты docs/checks/clean_clone_20261026*.json. |
| E15 | [x] | Версии базовых образов и зависимостей зафиксированы (не `latest`) | Закреплённые образы; package-lock + requirements.lock c хэшами; greenlet закреплён для amd64. |
| E16 | [x] | Логи в stdout/stderr; `docker compose logs app` показывает причины отказа | `docker compose logs app`: миграция, статус LLM, uvicorn в stdout. |
| E17 | [x] | Сидирование/миграции автоматически на старте (или 1–2 команды в README) | Seed в репозитории, COPY/bootstrap автоматически. Свежая БД startup 46 с; нет сетевых вызовов в boot. Seed-dialogue refs защищены regression-тестом. |
| E18 | [x] | Большие артефакты/модели не в гите; LLM через OpenAI-совместимое API | Сырые PBF/DEM и видео ignored; GeoJSON gzip, NPZ lossless LZMA. Самый большой текущий tracked file 19 964 021 B; полная история сохранена, soft pack target 150 MB не достигнут. |
| E19 | [x] | POSIX-совместимые команды в рантайме контейнера (не bash-специфика) | CMD python; RUN стандартный sh, bash в runtime не используется. |
| E20 | [~] | Рекомендуется: Makefile (`make up/down/seed`), `docker-compose.override.yml`, CI (docker build + линтер), ссылка на развёрнутый сервис | Makefile, dev override, CI только workflow_dispatch, make check/audit/demo-check. Внешний хостинг не настроен и не требуется для локального старта. |

## Текущие проверки и ограничения

- `make audit`: 4 Python locks + 3 npm scopes, 0 advisories/scanner errors; после добавления coverage будет повторён.
- `scripts/audit_repository.py --history`: 12/12 release guards PASS, полная история сохранена.
- `pytest`: 365 passed + 125 subtests; `ruff`, frontend lint/build PASS после integration 3293368.
- Security: schema whitelist, no shell/network tools from text, exact secret echo filter, signed tenant scope, SSE cookie и logout. Production SSO, capability revocation и универсальная DLP не заявлены; см. SECURITY.md.
- Второй регион интегрирован c7c8ba3/f721305: Геленджик, 30 объектов, 2 376 geo +418 open claims, 14 стадий/1 140,93 с. Geo 373,25 с; warm mean 11,795 с/отель; lifetime RSS 1,81 GB. Sochi seed/geo/tiles не изменялись; readonly/network-none validation обоих регионов PASS.
- Требуется повтор clean clone: без .env, новый project/пустые volumes, Docker-only PATH, prune, smoke/demo-check/browser. Предыдущий прогон был с .env.example→.env.
- pytest-cov и make demo-voice интегрированы; 85% line coverage без кода тестов. README provenance обновляется. Голос и публичные URL предоставляет владелец позже; плейсхолдеры сохраняются.
- Рабочие reviews/Telegram/blueprint/research/AGENTS исключены из tracked tree и build context. Локальные ignored copies и внешний архив okno-working-docs/20261026 сохранены; полная git-история не переписана. Канал новых локальных ревью остаётся доступным.
- Ручная разметка отсутствует; nDCG и полевая точность не придуманы. Реальный независимый третий тип конфликта высот не подтверждён.

## Последние интеграции

- 26.10 19:10: чистый клон 3e3cc92, empty build cache, 139,49 с до health; API smoke, 12 demo-check guards, браузер PASS.
- 26.10 19:20: REVIEW005 принят; сохранение истории, no.env Docker-only gate и нейтральные PRODUCT/ARCHITECTURE документы.
- 26.10 20:12: main 3293368: tenant auth, live-mock injection guards, private Changes projections; Quality model baseline/provenance; timed VOICEOVER и lossless mix helper; frontend XSS regression.

- 26.10 20:20: c7c8ba3/f721305, Геленджик — перенос pipeline и реальные resource measurements. Root Ruff и оба dry-run PASS.

- 26.10 20:25: рабочие материалы архивированы вне product и убраны из tracked tree; нейтральные PRODUCT/ARCHITECTURE/CONTRACTS остаются.
