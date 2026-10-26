# REVIEW003: synthetic-demo ответы отельера

Это отдельная вымышленная демонстрация pipeline, а не подтверждение свойств реальных гостиниц. Настоящая геометрия, маршрут и расчёт заката используются как входы. Заявление о тишине вымышлено и на каждом уровне помечено `synthetic=true`.

- Публичный ledger: `/data/seed/demo_hotelier_claims.jsonl`, ровно три `Claim`.
- Приватные вымышленные truth и дословные answers: `data/hotelier_profiles/demo_acceptance.json`. Каталог profiles не публикуется через `/data`.
- Обычные `synthetic.json` и `simulate_answer(seed=42)` не изменены. Новые fixtures выбирают фиксированные ID и scopes без случайного распределения.

| Отель | ID | Scope synthetic quiet_night | Реальный маршрут, минуты / confidence | Реальный закат в сентябре, confidence |
|---|---|---|---|---|
| Cascade | `osm-node-11866202895` | SW, этаж 3 | 2.42 / 0.66 | 0.90 |
| Приморская | `overture-037ee676-5776-46bd-a4ac-38c828f2a99d` | W, этаж 5 | 6.53 / 0.66 | 0.90 |
| Brevis apartments | `osm-way-201474330` | W, этаж 7 | 9.67 / 0.66 | 0.90 |

У каждого synthetic claim `scope.type=floor_range`, `floor_min=floor_max`, `month=9`. Значение содержит только `available=true` и `facade`. Полей дБ, измеренных уровней или доказанной шумоизоляции нет. `source=hotelier_simulator`, `method=simulated_hotelier_quote@1`, `confidence=0.85`, `evidence.kind=simulation`. Каждая цитата начинается словами «Синтетический ответ для демонстрации:» и дословно совпадает с приватным answer и `evidence.inputs.raw_text`. Публичный artifact содержит эту же цитату и явные ограничения. Несуществующих URL переписки и идентификаторов реальных ответов нет.

ID claim детерминирован: `demo-hotelier-` + первые 24 hex SHA-256 от UTF-8 JSON списка `[scenario_id, hotel_id, characteristic_id, scope_without_nulls]` с `sort_keys=true`, `ensure_ascii=false`, separators `(',', ':')`. ID evidence использует ту же строку с суффиксом `|quote` и префикс `demo-evidence-`. Scenario ID — `review003-synthetic-hotelier-v1`. `observed_at` — фактическое UTC-время создания fixture, `valid_until` — ровно через 90 суток. Даты не обновляются молча на рестарте; для следующего периода нужен явно созданный новый fixture с новой версией.

Проверка без БД: `Claim`/value-schema validation, чтение публичного ledger и приватных answers, затем `resolve_claims` и `assess_hotel` с исходными geo/open claims. Получены совместимые contexts SW/3, W/5, W/7 в сентябре: quiet_night = yes, 0.85, synthetic; sunset_view = yes, 0.90, real; beach_walk = supported comparison / partial, 0.66, real. Исходные facade facts quiet_night остаются с confidence 0.38 и `synthetic=false`.

В acceptance нужен явно названный `synthetic-demo` режим. Путь к пляжу с confidence 0.66 остаётся приблизительным, а порог yes=0.7 не меняется. Допуск «✓ или ≈ с confidence ≥0.6» относится только к успешно выполненному сравнению и совместимому номеру; unknown, неполные ограничения питомцев и несовместимые scopes не должны считаться совпадением. Синтетическая тишина не является эмпирической точностью, измерением шума или реальным обещанием гостиницы. Перед бронированием нужны реальные подтверждения отеля.

Loader, регистрация source и acceptance semantics подключаются оркестратором. Fixture не отправляет внешних сообщений и не создаёт настоящей брони.

## Приёмочная CLI

Запуск: `.venv/bin/python scripts/demo_check.py`. Адрес берётся из `OKNO_URL` (по умолчанию `http://localhost:8080`), либо из `--url`. Таймаут каждого HTTP-запроса — 10 секунд, без автоматических повторов и скрытого прогрева поиска. Режим всегда явно назван `synthetic-demo`. JSON-отчёт сохраняется в `/tmp/okno-demo-check.json`; путь можно задать `--report` или `OKNO_DEMO_REPORT`. Любая проваленная проверка возвращает exit code 1 с названием проверки и причиной. Сохранённый отчёт не является заранее подготовленным PASS.

CLI не создаёт claims, ответы, паспорта или события: использует GET и обычный публичный POST поиска, который может сохранять стандартный поисковый trace. Приёмка не вызывает simulate/confirm/refresh и не очищает БД. Loader и Makefile подключает оркестратор.

Фиксированные русские запросы и ожидаемые распознанные ID находятся в `data/eval/persona_acceptance.json`. Маша проверяется точной исходной фразой: минимум три `group=all`, все обязательные verdicts ✓/≈ с confidence ≥0.6 и provenance, совместимые фасад/этаж/месяц, минимум одна явная оговорка по стороне или этажу, API и полное время HTTP-поиска строго меньше 2 секунд. Каждая из трёх dedicated synthetic гостиниц дополнительно проверяется через detail: synthetic flags на fact/claim/verdict, source, simulation evidence, точная цитата, правильный scope и доступный публичный artifact; исходный geo noise остаётся отдельным реальным fact/claim с confidence 0.38. Герой `osm-node-10058556917` должен иметь verdict evidence refs по морю, закату, шуму и пути к пляжу, цепочку claim/fact, доступные локальные artifacts и `/geo` с маршрутом, фасадами, горизонтом, солнечным путём и метаданными расчёта.

Для остальных пяти персон требуется минимум три непустых объяснимых карточки: хотя бы одно выполненное или предварительное условие с provenance. Неизвестное обязательное условие не допускается в `group=all`. Эти проверки не означают полное совпадение со всеми пожеланиями. В частности, бары/ночная жизнь пока не имеют отдельной характеристики: party-query и отчёт прямо показывают это ограничение и `unmapped`; CLI не вставляет искусственные parsed criteria ради прохождения. Близость пляжа включена в тексты персон как естественное пожелание, а не добавляется после разбора.

Небольшие отрицательные и граничные тесты: `.venv/bin/python -m pytest tests/test_demo_check.py -q`. Они проверяют строгую границу 2 секунд, confidence 0.6, недостаточное число карточек, unknown в all, несовместимые scopes, утрату synthetic/provenance, изменение исходного confidence, отсутствие маршрута/горизонта и HTML fallback вместо artifact.

## Семь завершённых pets-диалогов и два живых preview/confirm

`data/seed/demo_hotelier_dialogues.json` — отдельный публичный envelope версии 1: `synthetic=true`, 9 questions (7 answered + 2 pending), 7 confirmed answers, 7 claims, 7 completed interview runs и 28 steps. Это вымышленные политики для реальных ID гостиниц, а не реальные ответы, наличие услуги или бронь. Source всех claims — `hotelier_simulator`, evidence — `simulation`, каждый claim имеет точную цитату, `message_id` существующего Answer и публичный artifact этого envelope. Никакие Wi-Fi, виды, дБ или собственная парковка здесь не придумываются. Найденная рядом парковка остаётся отдельным реальным POI-расчётом и не означает подтверждённый въезд в отель.

Завершённые demo-политики назначены явно в приватном `data/hotelier_profiles/pets_dialogue_profiles.json`: Cascade, Приморская, Brevis apartments, Цитрус, Терминал Адлер, Парадиз и Империя. В каждой разрешены собаки не менее 30 кг; вид питомца, точный предел веса и доплата за ночь приведены в ответе и получены существующим AnswerParser. Confidence 0.85 относится к синтетической фикстуре. TTL — 90 дней от фактического UTC-времени генерации. Детерминированные question/answer/run/step IDs привязаны к scenario `review004-pets-dialogues-v1` и hotel ID; claim/evidence IDs создаёт существующая идемпотентная confirm-функция. Facts после Resolver имеют `status=computed`, `synthetic=true`; у Claim поля status нет. Человекочитаемый результат — **«Ответ симулятора подтверждён»**, без выдачи за реальное подтверждение гостиницей.

Воспроизведение/проверка: `python -m data.hotelier_profiles.build_dialogues` валидирует существующий envelope, типы, parser replay, raw quotes, TTL, truth, связи и pending-сценарии. `--write` заново запускает настоящие `parse_answer` и `confirm_answer` на тестовом MemoryRepo, проверяет отсутствие claims до confirm и идемпотентность, затем готовит envelope до его первой записи в seed DB. Shared HTTP/БД и внешние службы не вызываются. Изменённая политика требует новой версии fixture; loader оркестратора сохраняет append-only семантику.

Для живой демонстрации в кабинете оставлены pending pets-вопросы:

- `osm-node-10058556917` — Островок: обычный simulator seed 42, private profile `demo-west`, собаки/кошки до 30 кг, 1000 ₽/ночь.
- `osm-node-10574252909` — Relax club exsclusive: seed 42, private profile `demo-restricted`, вымышленный запрет питомцев.

Оба результата заранее объявлены synthetic truth, не выведены из реальных удобств. Войти `hotelier / hotelier123`, выбрать один из этих отелей, открыть помеченный demo-вопрос, нажать «Симулировать», просмотреть цитаты/поля и отдельно подтвердить. До confirm нового claim нет. Обычные два simulator profiles не менялись. Папка profiles и builder остаются приватными; public envelope содержит лишь самодостаточные synthetic диалоги и provenance, без публикации приватного truth-каталога.

## Паспорт и ограничения маршрута

Подтверждение и уведомления используют один `_comparison`: основное сравнение времени/значения объединено с публичным S5 helper `route_constraints(fact, criterion)` через трёхзначный AND. Известные лестницы или переходы отклоняют соответствующий запрет, отсутствующие теги остаются unknown. `no_stairs`/`no_crossings` сохраняются в `passport_items.criteria`; даже при основном `field=available` неизвестный тег не разрешает обойти ограничение локальным generic confirm. Известное несоответствие числовому порогу остаётся отказом, даже если другой route tag неизвестен. Изменение подтверждённого маршрута на `has_steps=true` или missing переводит критичное ожидание в `at_risk` через тот же predicate и только локальное уведомление.

Проверки включают stairs true/false/missing/null, crossings empty/nonempty/missing, основной available вместо minutes, числовое несоответствие при чистом маршруте, сохранение flags, а также реальные notify_passports на тестовом Repository. Shared DB и стенд не изменяются.
