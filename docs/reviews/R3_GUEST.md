# R3 guest: паспорт, ввод и гостевые объяснения

База: `main 89c8ff6`; worktree `okno-wt-r3-guest`, ветка `feat/panel-r3-guest`.
Прочитаны все round3 Ольги, Игоря и Дины, Н3 Марины, PANEL_FEEDBACK и гостевые разделы спецификации/контрактов. Main и стенды не изменялись. Все записи при проверке — только MemoryRepo; seed/YAML не менялись.

## Воспроизведение и результат

| Пункт | На базе 89c8ff6 | В этой ветке |
|---|---|---|
| Р6, будущая поездка после TTL | Воспроизведено в памяти с датой проверки 26 октября 2026, TTL 24 января 2027 и поездкой 14–21 сентября 2027: create и confirm `at_risk`, room_number=null, комментарий не сохранён отдельно | create `sent`, confirm `confirmed`, номер 512 и комментарий сохранены; один нейтральный дедлайн на весь паспорт |
| Р6, стороны/этаж | Scope уже сохранялся в items, но готовый гостевой паспорт его не показывал | Сохранённое пожелание видно после отправки; паспорт без комнатных условий больше не советует первый этаж |
| Р6, обязательные поля | Причина disabled отсутствовала | Подпись просит имя/текст ожиданий или исправление дат |
| Р6, внешние места | Подтверждение карты называлось ответом отеля | Свежие подходящие сведения о маршруте/пляже/площадках/аптеке/барах помечены «Проверено по карте», с оговоркой об отсутствии гарантии отеля; неизвестное так не называется |
| Р10 | Company append добавлял Машу и Игоря, Masha заменяла текст и navigated | Company только показывает образец; textarea растёт. Masha при непустом тексте не запускает поиск, исходный текст можно вернуть; пустое поле сохраняет one-click demo. Даты сохраняются |
| Р12 | item.scope мог содержать только месяц и room_request | Ссылка и onOpen используют scope выбранного profile: сторона, конкретный этаж и месяц + индекс человека |
| Р4 UI | Наличие CID было достаточно для AskButton | Общий guard и обработчик отправки требуют `spec.hotelier_answerable === true` |
| Р8 UI | Причина перечисляла запрошенные условия с unknown/partial | «Потому что» строится только из yes без disputed, synthetic имеет явную demo пометку; группа на одних demo ответах названа «Сошлось по демо-ответам отелей — проверьте у отеля» |
| Р9 UI | Partial Wi-Fi выглядел как достигнутые 30 Мбит/с | Вместо label с порогом показывается text «Wi-Fi есть по карте · скорость неизвестна», статус «мало данных»; чип желания сохраняет порог; районная мобильная связь не названа Wi-Fi отеля |
| Т1 | Код пляжа на текущей базе не воспроизводится: f253c03 уже нормализует humanFact/guestText | Нормализация сохранена; новый непроверенный «фикс» поверх неё не добавлен |
| Т2 | Заголовок каждого evidence наследовал demo от смешанного Fact | Заголовок определяется собственным kind/source, расчёт не называется ответом отеля |
| Т3 | Связанные риски обслуживаются поисковым потоком | Сохранены context_fact_ids и кнопка источника риска; новый backend caveat остаётся видимым в подходящем слое |
| Т4 | «Тише» перечисляло все тихие стороны | Если все проверенные стороны тихие — одна фраза об одинаково тихих сторонах |
| Т5 | Несколько разных «Подробнее» | «Совет по номеру», «Почему так», «Страница отеля» |
| Т6 | Углы, дБ, «срез» и «условный уровень» во втором слое | Короткие human summaries; расчётные числа остаются в «Для дотошных»/evidence; исправлены даты и двойная точка, дубли «Данных нет» |
| Т7 | Beach surface показывал другое расстояние по прямой рядом с длиной пешего маршрута | Для пляжа маршрута не показывается второе несопоставимое расстояние; исходное значение остаётся в evidence |
| Т8 | Парсер — другой поток | Не изменялся |
| Т11 | Неограниченная крутилка | Через 8 сек человеческое сообщение, через 30 сек ошибка и «Повторить»; предыдущие search fetch и таймеры отменяются при новом запросе/unmount |

Известное required «нет» показывается после unknown отдельной визуальной группой. Это сохраняет порядок backend после группировки UI; API group не расширен.

## Безопасность и проверки

Новые статусы не вводились. Будущий TTL — только информационный `evidence_review`; срок ни одного Fact/Claim не продлевается. Текущие expired/stale/disputed факты не подтверждаются. Повтор confirm теперь проверяет и изменение значения/scope, а не только срока; неизменившийся snapshot идемпотентен. Порог скорости, ограничения маршрута, target/scope и отрицания не ослаблены.

Новые смысловые тесты: `tests/test_r3_guest_passport.py`: общий дедлайн, сохранение scope/комментария/номера, idempotency, отсутствие записи claims/facts, expired/stale/disputed, Wi-Fi без скорости, повтор после реального отрицания и неизвестное окружение. По согласованию root точечно обновлены старые future-TTL expectations в `backend/tests/test_passport.py` и `tests/test_group_passport.py`; проверки реальных изменений/конфликтов/порогов сохранены.

Проверки перед commit:

- Полный pytest с pipefail: **2058 passed, 125 subtests passed**, 139,18 с. Единственное предупреждение — deprecation FastAPI/TestClient httpx.
- Ruff: `ruff check backend tests geo`.
- Frontend: `npm run lint`, `npm run build`.
- 13 дополнительных проверок чистых guest helpers через esbuild/Node: источники Wi-Fi, краткий шум/углы, room groups/absent floors, yes-only demo reason, русские даты.
- Baseline/current MemoryRepo reproduction: old-main `at_risk → at_risk`, номер null, comment false; current `sent → confirmed`, номер 512, comment true, один срок «до 24 января».

Actual браузерный smoke после интеграции выполняет root; scripts этой веткой не менялись. Самостоятельных записей на 8080/8081/8082 не было.

## Файлы и интеграция

Backend: `backend/api/passports.py`. Frontend: страницы Search/Hotel/Compare/Booking и components ui/FactsPanel/facts/RoomLab/GroupPlan/RoomAdvice/RoomProfiles/panel-guest-presentation/panel-guest-query; новый `frontend/src/panel-r3-guest.css` сохраняет палитру и Nunito.

Новый prop: `FactsPanel.onCorrect?: (fact: Fact) => void`. Кнопка «Неверно» (`correct-calculated-fact`) только в hotelier mode для non-synthetic computed calculation evidence. Hotelier-поток передаёт handler. GroupPlan onOpen получает item с выбранным scope. Timeline confirm добавляет JSON-поля `hotel_comment` и `reported_room_number`; общие модели не меняются.

Root согласовал нейтральный будущий TTL и обновление старых тестов. Search-поток добавляет optional `SearchHit.why_higher`; UI показывает строку явно. Изменение YAML connectivity/hotelier answer parsing/общих моделей — не в этой ветке. Root добавил контракты `a80c25d`; выполнен обычный merge в `1028b0b` без конфликтов. После merge: focused passport pytest **76 passed, 21 subtests**, Ruff и frontend lint PASS.

Smoke selectors: `company-search-help`, `restore-search-input`, `passport-saved-scope`, `passport-recheck`, `passport-hotel-reply`, `passport-required-fields`, `why-higher`, `correct-calculated-fact`. Проверить both link/onOpen для компании и slow search abort/retry. Старые smoke, ожидающие append фиктивных людей или critical future-TTL, надо обновить под R3.

Коммит реализации: `4b21406 fix(guest): preserve passport choices and clarify search evidence`. Контракты root влиты обычным merge `1028b0b`, без cherry-pick/rebase/amend/push.
