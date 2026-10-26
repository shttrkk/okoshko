# Границы безопасности локального демо

Недоверенные входы: текст поиска, ответ отельера, описание/YAML характеристики,
тексты и теги открытых источников, JSON провайдера LLM и содержимое LLM cache.
Они являются данными. Они не дают права выполнять код, выбирать источник факта,
повышать уверенность или действовать от имени другого отеля.

## Учётные записи и область доступа

Все учётные записи ниже публичные и демонстрационные. Их ответы всегда получают
`synthetic=true` на сервере, даже при `synthetic=false` в теле запроса.

| Логин | Пароль демо | Роль | Отели |
| --- | --- | --- | --- |
| `hotelier` | `hotelier123` | `demo_admin` | Все отели; явно привилегированный сценарий демо |
| `cascade` | `cascade-demo-2026` | `hotelier` | Cascade, `osm-node-11866202895` |
| `brevis` | `brevis-demo-2026` | `hotelier` | Brevis apartments, `osm-way-201474330` |

`POST /api/hotelier/login` по-прежнему принимает `{username, password}`. Ответ:
`{token, username, synthetic: true, role, hotel_ids}`. Пустой `hotel_ids` у
`demo_admin` означает его явную роль; tenant с пустым списком не получает доступ
ко всем отелям. Signed token содержит username, role, hotel_ids, expiry и nonce;
HMAC проверяется до использования scope и сверяется с серверной картой аккаунтов.
Срок — 12 часов, перезапуск процесса отзывает все сессии. Ключ подписи случайный,
не сохраняется в репозитории и не передаётся модели.

| Маршрут | Граница |
| --- | --- |
| `GET /api/hotelier/{hotel_id}` | Bearer; только свой hotel scope или demo admin |
| `POST /api/questions/{id}/answer`, `/simulate` | Bearer; scope берётся из сохранённого Question до LLM/записи |
| `POST /api/answers/{id}/confirm` | Bearer; scope через Answer → Question до любых записей, включая повтор confirm |
| `GET /api/passports?hotel_id=...` | Bearer; чужой hotel ID даёт 403; без hotel ID возвращаются только разрешённые отели |
| `POST /api/passports/{id}/action` | Bearer; scope сохранённого паспорта до любых записей |
| `GET /api/passports/{unguessable_uuid}` | Существующая гостевая read capability по UUID; авторизованный чужой tenant получает 403 |
| `GET /api/runs`, `/{id}`, `/{id}/stream` | Interview runs видны только владельцу scope/admin; публичный список их скрывает; прямой чужой ID даёт 403, без входа — 401 |
| `GET /api/changes` | Публичные события/карта, `notifications=[]`, реальный `payload.notification_count`; private уведомления только по Bearer: admin все, tenant через scope связанного паспорта |
| `POST /api/changes/simulate`, `/refresh` | Только demo admin по Bearer, до обращения к сервису глобальных изменений |

Login также устанавливает `okno_run_session`: HttpOnly, SameSite=Strict,
Path=`/api/runs`, 12 часов. Это позволяет same-origin EventSource читать закрытый
interview stream без Bearer заголовка. Cookie не отправляется к кабинету,
подтверждению ответов или паспортам и не заменяет их Bearer авторизацию. В cookie
и Bearer используется одна проверка подписи/scope; явный некорректный Bearer не
заменяется валидным cookie. Для HTTP localhost cookie не имеет флага Secure.
`POST /api/hotelier/logout` удаляет cookie с тем же Path; UI также удаляет локальный
Bearer token. Скопированный Bearer остаётся действителен до expiry/перезапуска:
это локальный logout, не серверный отзыв stateless сессии.

Каталог, facts, опубликованные claims с цитатами и вычислительные/search traces
остаются публичными данными демо. Полный неподтверждённый ответ отельера и runtime
interview trace через эти маршруты не публикуются. Подтверждённые цитаты становятся
частью публичного evidence: не следует вводить в них персональные или секретные
данные. Публичный synthetic seed диалогов отдельно помечен вымышленным и не является
закрытой перепиской. Создание гостевого паспорта и вопроса остаётся публичным.
Public Changes projection удаляет весь список индивидуальных уведомлений, чтобы
UUID гостевого паспорта, item IDs и текст гостя не обходили закрытый listing.
Исходный ChangeEvent и его история при чтении не изменяются. Общее число
уведомлений — агрегат, а не искусственный ноль из пустого публичного списка.

## LLM и подтверждение фактов

`backend/llm/client.py` принимает только ограниченный JSON ответ. Tool/function
calls и refusal отклоняются, даже если рядом есть валидный JSON. Схемы запрещают
лишние поля. Ответ отельера использует внутреннюю extraction схему без source,
confidence, confirmed, IDs и дат; известные характеристики и разрешённые ключи
value проверяются отдельно. Каждая предложенная цитата должна дословно входить
в предложенный raw_text. Даже валидное предложение не заменяет локальное
извлечение из фактического пользовательского ответа и его бизнес-уточнения.

В поиске допускаются только активные characteristic IDs каталога. Локально
распознанные отрицания, пороги, room scope, ограничения маршрута и даты не могут
быть удалены моделью. Это ограничение плана, а не доказательство безошибочного
понимания произвольного русского текста.

До возврата/новой записи LLM cache проверяются известные значения секретов из
окружения, включая JSON-escaped строки после декодирования и свободные текстовые
поля валидной схемы. Совпавший результат не возвращается, не кэшируется и не
передаётся в JSON repair. Существующая загрязнённая cache запись игнорируется;
удаление старых файлов/записей эта проверка не выполняет. Логи содержат только
название задачи, режим и HTTP status, без provider content или credential values.
Это exact-value guard для известных runtime секретов, не универсальная DLP и не
защита от всех преобразований/кодировок неизвестных секретов.

Confirm заново запускает детерминированное извлечение и сравнивает все updates
с сохранённым preview до первой durable записи. Также действует явный whitelist
полей для `pets`, `room_orientation`, `soundproofing`, `quiet_night`, `sea_view`,
`sunset_view`, `mountain_view`; `value.source` и `value.confidence` не разрешены.
Сервис задаёт source, method, confidence `.85` (заданный prior, не измеренная
точность), время наблюдения из server-created
preview и TTL спецификации. Публичные demo principals создают только
`hotelier_simulator` claims. Внутренний тест с отдельным trusted principal
`synthetic=false` проверяет будущую границу адаптера; такого публичного входа нет.

## Спецификации, данные и UI

Реестр инструментов локальный и явный. Неизвестные инструменты, произвольные
network/shell параметры и AST узлы вне whitelist отклоняются до исполнения.
JSON Schema `$ref`/`$dynamicRef` не могут читать HTTP/file ресурсы; локальные ссылки
сохраняются. Данные OSM/сайтов не становятся исполняемыми инструкциями.
Backend возвращает обычный текст/JSON, не HTML из ответа отельера. Browser XSS
smoke — отдельная проверка UI; её результат нельзя вывести из backend unit tests.
Отдельный browser прогон S6 с route-fulfilled синтетическими hotel.name,
quote/raw answer/parsed JSON и `javascript:` ссылками прошёл без выполнения
payload, injected elements, dialogs или внешних запросов; сервер не изменялся.

## Проверки и пределы

Воспроизводимый набор без настоящего провайдера или общей БД:

```sh
pytest -q tests/test_llm.py backend/tests/test_security.py tests/test_schema_safety.py tests/test_derive.py
```

MockTransport проверяет extra fields, tool calls, чужие IDs, подмену source,
confirmed/confidence, цитаты и canary в валидных полях/JSON escape/cache.
In-memory API тесты проверяют A → B отказ до записи, omit-hotel scope, passport
capability, private interview listing/SSE, синтетичность demo и signed expiry.
Logout удаляет cookie; explicit tenant/invalid Bearer не использует старый admin
cookie. Change feed не раскрывает guest capability, а global mutations требуют
admin до вызова сервиса.
Отдельно проверяются 18 недопустимых tool/AST вариантов и внешние schema refs.
Это синтетические регрессии границ, не эмпирическая оценка стойкости к атакам.

Публичные пароли, включая admin, намеренно известны всем: это демонстрация RBAC,
не аутентификация реальной гостиницы. Для production нужны доверенный SSO/tenant
provisioning, приватные аккаунты, HTTPS/Secure cookies, управляемые ключи и отзыв
сессий, rate limits, аудит доступа и политика хранения гостевых данных. UUID
паспорта — bearer read capability без отдельного отзыва/срока; знающий ссылку
может прочитать паспорт без входа. Публичные Studio/collect/quality demo
операции не составляют production административную границу; их доступ и лимиты
нужно ограничить в интеграции. Эти ограничения не скрываются за tenant тестами.
