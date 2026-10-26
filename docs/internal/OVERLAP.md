# Resolver: пересечения областей наблюдений

26.10.2026. База проверки — `8668f2f`; исправление находится в ветке `feat/overlap`.

## Поведение

Resolver разбивает реальные пересечения этажных диапазонов до голосования. Например, два равновесных наблюдения `W, 1–5, true` и `W, 5+, false` дают `1–4: computed true`, `5: disputed` с Conflict и `6+: computed false`. Прежние source priors, правила независимости источников, confidence и hotelier override сохранены. Одинаковые значения на пересечении не создают конфликт.

Исходные claims не меняются. Для вычисления используются копии с теми же ID, value, evidence и сроками, но с суженным scope. Scope каждого Fact является подмножеством областей всех его supporting/conflicting claims. Публичные evidence и original claim IDs сохраняют проверяемую цепочку.

Разные facade, month и room_type не объединяются. `None` означает wildcard. Для категорий сохраняется общий Fact по умолчанию и добавляется узкий resolved Fact. Это позволяет сохранить сведения о других месяцах и типах номеров, когда Scope не умеет выразить «все, кроме suite». Не создаются выдуманные типы номеров или фасады; годовое наблюдение не размножается без необходимости по 12 месяцам. При отсутствии пересечения сохраняются прежние scope и Fact ID.

Общее положительное заявление о виде «в отеле где-то есть вид» остаётся квалифицируемым описанием. Оно не голосует о каждом номере. Явное `universal_scope=true` участвует в действительном пересечении и может создать value conflict.

Query matcher сначала сравнивает действительные области, затем статусы. Узкий disputed/unknown больше нельзя обойти широким yes. Кандидаты, совместимые со всеми must scopes, имеют приоритет перед неподходящими этажами с пустым unknown. Для derive добавлен консервативный guard: широкий Fact не создаёт составной claim поверх узкого спорного либо переопределяющего Fact. Явно ограниченные области вне спора остаются доступны. Невыразимое исключение suite приводит к abstain.

После supersedes или истечения наблюдений `resolve_hotel` и `resolve_all` помечают отсутствующие в новом активном наборе прежние open/asked conflicts как resolved. История, claim_ids и question_id сохраняются. Действующий asked conflict сохраняет свой статус и вопрос; новый спор остаётся активным.

Demo-check проверяет materialized synthetic Fact с confidence ≥ 0,6 и scope внутри immutable supporting claims. Confidence исходного fixture Claim по-прежнему ровно 0,85; exact quote, simulation kind, synthetic/source и настоящая geo confidence 0,38 обязательны. Пороги Masha, проверка совместимости всех must и negative guard Brevis сохранены.

## Проверки

```sh
.venv/bin/python -m pytest backend/tests tests/test_resolver.py tests/test_demo_check.py tests/test_registry_invariants.py -q
.venv/bin/ruff check backend/agents/resolver.py backend/agents/query_match.py backend/tools/scoped_inputs.py backend/tests/test_search.py tests/test_resolver.py scripts/demo_check.py tests/test_demo_check.py tests/test_registry_invariants.py
git diff --check
```

Результат: **253 tests и 125 subtests PASS**, Ruff и diff-check PASS. Единственное предупреждение — уже существующий Starlette/httpx deprecation.

Known-answer проверки покрывают пересекающиеся и непересекающиеся этажи/фасады/месяцы/типы номеров, открытые границы этажей, тройное пересечение, одинаковые значения, разные и повторные источники, weighted auto resolution, expired/superseded claims, узкий disputed/unknown в поиске, retirement истории conflicts в single/bulk MemoryRepo. Проверен реальный текст AnswerParser «С 5-го этажа на запад закат виден круглый год» против сентябрьского отрицательного geo-наблюдения на 1–5 этажах. Derive проверен на обход широким положительным Fact, безопасные области вне спора и неизменный morning_swim.

## Замер на versioned seed без сети и БД

Фиксированное время Resolver: `2026-10-26T15:00:00Z`. Загрузили четыре versioned ledger файла, каталог и sources; 750 POI claims рассчитаны штатным Registry по локальному versioned OSM snapshot. Динамические ответы живого стенда не включались. Сравнили старый Resolver из `git show 8668f2f:backend/agents/resolver.py` и новый на одних входах; при сравнении Fact payload исключён только `updated_at`. Скрипт и JSON этого локального прогона сохранены в `/tmp/okno-overlap-eval.py` и `/tmp/okno-overlap-eval.json` рабочего окружения.

| Показатель | До | После |
|---|---:|---:|
| Отелей / claims | 250 / 24 497 | 250 / 24 497 |
| Materialized facts | 25 884 | 25 884 |
| Добавленных / удалённых Fact ID | — | 0 / 0 |
| Изменённых facts при прежнем ID | — | 3 quiet_night |
| Conflicts | 109 | 109, все auto_resolved |
| Resolver, локально | 0,643 с | 0,705 с |
| Geo-only Resolver | 0,599 с | 0,649 с |

Geo-only: **23 109 materialized facts**, включая unknown placeholders, совпали по ID и всем сравниваемым полям. Нет массового изменения обычных sunset/noise scopes.

Три изменившихся quiet Facts теперь учитывают согласующиеся geo и synthetic наблюдения, confidence каждого **0,6888917526**; synthetic=true сохраняется. Исходные simulator claims остаются 0,85, исходные real geo claims/facts — 0,38. Masha сохраняет **3 all**; проверены evidence и совместимость всех must. Три локальных прогона ranker: **84,1 / 85,1 / 78,6 мс**. Это время локального ранжирования, не HTTP latency.

Brevis в семейном запросе: **almost**, beach verdict **unknown**, real route `has_steps=true`, confidence **0,66**. Negative guard проходит.

SHA-256 входных ledgers:

| Файл | SHA-256 |
|---|---|
| `data/geo/geo_claims.jsonl.gz` | `ec9f41f9a7e9078bf8752db6dc4156c37d8d3bbda34f143a85daf292e6b11aa5` |
| `data/seed/open_claims.jsonl.gz` | `2ee8ceba50debc2a8da83afe36b47c68a3e5177a174841de7d80cae5903d94f2` |
| `data/eval/website_claims.jsonl` | `5a053503d8cd2039e0fdd46e923aeae06a8a304400b295aecfa64c078d483d9a` |
| `data/seed/demo_hotelier_claims.jsonl` | `414c01621354ea074ec3c61250432e472e81274cfb792e2ccf2b64e3c6eb6606` |

Остаются интеграционная HTTP-проверка `make demo-check` после root rebuild и независимый retest S7. Контракты, DB, claims/seed, geo и frontend этим пакетом не менялись. Числа тестируют семантику и производительность на сохранённом снимке; эмпирическая точность реальных видов и шума ими не измеряется.
