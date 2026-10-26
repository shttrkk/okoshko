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
