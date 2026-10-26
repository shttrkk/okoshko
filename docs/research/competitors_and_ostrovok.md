# Конкуренты, prior art и контекст Островка для трека «О!дин шаг до идеального отеля»

Дата сбора: 2026-10-26. Период: 2024–2026. Кейс: `/Users/shttrkk/ostrovok-hack/materials/CASE.md`.

Оговорка по источникам: часть данных о функциях 2026 года (Airbnb, Expedia Explore 2026, Mindtrip) взята из отраслевых СМИ и блогов, не из первоисточника. Такие места помечены. Страницы Expedia (expedia.com/product/ask-filter, newsroom) вернули HTTP 429, поэтому их содержание известно только по выдаче поиска. Статья tproger прочитана через прокси r.jina.ai, потому что напрямую не открылась.

---

## 1. AI/NL-поиск отелей у глобальных игроков (Booking, Expedia, Airbnb, Google, Tripadvisor, Trip.com, Mindtrip, Perplexity, ChatGPT apps)

### Takeaway
Все крупные OTA уже сделали стандартный набор: NL-запрос превращается в обычные фильтры (Smart Filter, AI filters), Q&A по карточке отеля по данным листинга и отзывов, AI-сводки отзывов, AI-сравнение и чат-ассистент. Все эти функции **переупаковывают уже существующий контент**: текст листинга, галочки удобств, отзывы, фото. Ни один игрок не **вычисляет** новые атрибуты (вид, шум, солнце, реальный пеший маршрут) и не показывает по отдельному факту источник, уверенность и дату.

### Cited Findings

**Booking.com**
- 30.10.2024: Booking запустил Smart Filter, Property Q&A и Review Summaries. Smart Filter: пользователь описывает объект своими словами, GenAI сканирует инвентарь и сам применяет фильтры. Пример из релиза: «Hotels in Amsterdam with a great gym, a rooftop bar, and canal views from the room». Запуск только в мобильном приложении, в US/UK/AU/NZ/SG — [Booking.com press release](https://news.booking.com/bookingcom-enhances-travel-planning-with-new-ai-powered-features--for-easier-smarter-decisions/)
- Property Q&A отвечает на вопросы об объекте по листингу, отзывам и фото (примеры: «есть ли зарядка для EV», «пускают ли больших собак»). На момент релиза функция работала через AI Trip Planner, вывод на страницы объектов был запланирован. Релиз не говорит, что происходит с вопросом, на который в данных нет ответа, и уходит ли он отельеру — [Booking.com press release](https://news.booking.com/bookingcom-enhances-travel-planning-with-new-ai-powered-features--for-easier-smarter-decisions/)
- Review Summaries задуманы как сводки, «tailored to what matters to each traveler», например парковка или доступность для колясок. На момент релиза функция была в эксперименте. AI Trip Planner работает в США с июня 2023 года — [Booking.com press release](https://news.booking.com/bookingcom-enhances-travel-planning-with-new-ai-powered-features--for-easier-smarter-decisions/)
- 09.10.2025, партнёрская сторона: Smart Messenger (агент собирает данные партнёра, объекта и брони и подсказывает, что и когда написать гостю) и Auto-Reply (партнёр задаёт темы ответов, и агент сразу отвечает на типовые и нетиповые вопросы гостей; Auto-Reply доступен глобально). Заявлено «73% increase in partner satisfaction». Для гостей запущены AI Trip Support (24/7, передаёт сложные случаи человеку) и AI Voice Support — [Hospitality Net / Booking.com](https://www.hospitalitynet.org/news/4129287/bookingcom-debuts-agentic-ai-innovations-adding-to-its-robust-suite-of-genai-tools-for-customers)
- Июнь 2026, позиция врио CTO Booking Випула Хингне: главное в AI — доверие, «Success will not be determined by who has the best model». Самый ценный AI, по его словам, тот, которого путешественник не замечает: ранжирование, фрод, прогноз цен. Видимые чат-боты, наоборот, «конкурируют за внимание» — [Skift](https://skift.com/2026/06/01/booking-ai-trust-vipul-hingne/)
- Новых публичных NL-функций Booking в 2026 году поиск не нашёл. AltexSoft пишет, что Booking развивает NL-поиск, discovery и smart filters, чтобы пользователи не уходили во внешних ассистентов — [AltexSoft](https://www.altexsoft.com/travel-industry-news/booking-holdings-says-ai-speeds-search-cuts-costs/)

**Expedia Group**
- Май 2024, EXPLORE: представлен Romie, AI-ассистент для планирования и бронирования. Его можно добавить в SMS-групповой чат, и он «учится» на пользователе — [Expedia Group IR](https://ir.expediagroup.com/news-and-events/news/news-details/2024/Put-Your-Trip-on-Autopilot-Expedia-Group-Introduces-New-Innovations-at-EXPLORE-to-Take-the-Stress-out-of-Travel-and-Enhance-Partner-Experience/default.aspx); [PhocusWire](https://www.phocuswire.com/expedia-explore-ariane-gorin-ai-romie)
- На Expedia.com есть AI Filters и Property Q&A: блок «Have a question?» на странице объекта отвечает «based on property data and real guest reviews», а AI-фильтры поднимают релевантные объекты. Данные по выдаче поиска, страница вернула 429 — [Expedia product page](https://www.expedia.com/product/ask-filter/)
- Explore 2026: Property Expert на Hotels.com отвечает на вопросы об объекте и о районе по данным объекта и отзывам. AI Property Compare сравнивает объекты по «vibe, location, amenities, and trade-offs». На Vrbo появился NL-поиск. Данные по выдаче поиска — [Expedia Newsroom](https://www.expedia.com/newsroom/expedia-group-unveils-new-ai-experiences-expands-travel-ecosystem-and-launches-philanthropy-program-at-explore-2026/); [Forbes](https://www.forbes.com/sites/jefffromm/2026/06/02/expedia-looks-to-leverage-and-humanize-ai-in-the-travel-journey/)
- Партнёрские агенты Explore 2026. Content Agent «for automatically detecting what information travelers still need and strengthening listing content», то есть сам находит пробелы в контенте. Autonomous Distribution заполняет листинги «from trusted sources». Есть Partner Companion и Dev MCP server. Сроки запуска не названы — [Expedia Partner blog](https://partner.expediagroup.com/en-us/resources/blog/ai-at-explore-2026-what-partners-need-to-know)
- Руководство Expedia на Explore 2026 подчёркивало «human service, transparency, and trust», чтобы предотвращать ошибки AI — [Travel Market Report](https://www.travelmarketreport.com/canada/news/articles/expedia-groups-explore-2026-what-the-future-holds-for-travel-advisors)

**Airbnb**
- Февраль 2026: тест AI-поиска на ограниченной группе пользователей — [Dataconomy](https://dataconomy.com/2026/02/16/airbnb-tests-natural-language-ai-search-tool-for-trip-bookings/)
- Май 2026 (вторичный источник). Панель фильтров заменило одно свободное поле «What / Add description». Подсказки: «Spacious place with bunk beds and board games». На карточках в выдаче появились AI-сводки вида «This apartment is peaceful, heated, and fits two guests» с пометкой, что они **derived from host-provided details**. Модель читает текст хоста. Никакой верификации утверждений хоста не описано — [RentalScaleUp](https://www.rentalscaleup.com/airbnb-natural-language-search/)
- 30.09.2026, Fall Update (вторичные источники). AI-поиск текстом или голосом. AI-фильтры: запрос «baby» раскрывается в фильтры «кроватки, детские площадки, детские книги/игрушки», и все они строятся на удобствах, которые отметил хост. Появились AI-описания листингов и сравнение wishlist с AI-сводками. Запуск начинается с США — [Hostaway](https://www.hostaway.com/blog/airbnb-2026-fall-update-what-it-means-for-hosts/); [RentalScaleUp](https://www.rentalscaleup.com/airbnb-fall-update-2026-guest-features/)
- Вывод отраслевых комментаторов: хостам теперь выгодно писать «для AI» конкретные факты вроде скорости интернета в Mbps или пешего расстояния до транспорта — [RentalScaleUp](https://www.rentalscaleup.com/airbnb-natural-language-search/)

**Google**
- Октябрь 2024: в Google Maps появились Gemini-сводки отзывов и поле «Ask Maps about this place» с готовыми вопросами. Среди примеров есть вопрос, тихое ли место. Ответы опираются на данные о 250 млн мест и на информацию сообщества — [9to5Google](https://9to5google.com/2024/10/31/google-maps-gemini/)
- 27.08.2026: AI Mode умеет бронировать отели. Пользователь описывает поездку и предпочтения и получает список с отзывами и «key factors». Дальше «Continue on Google», выбор номера, условия отмены и оплата Google Pay. Партнёры: Booking.com, Expedia, Hilton, Hotels.com, IHG, Marriott, Priceline, Trip.com, Wyndham, Choice. Только США, только английский. Поиск по атрибутам вроде тишины или вида в статье не упоминается — [TechCrunch](https://techcrunch.com/2026/08/27/googles-ai-mode-can-now-track-flight-prices-help-book-hotels-and-more/)
- Агентное бронирование в AI Mode анонсировали в ноябре 2025 года. 07.08.2026 подтверждён ограниченный тест в США, технический партнёр Amadeus — [Hospitality ON](https://hospitality-on.com/en/distribution/google-launches-ai-agent-hotel-booking-test-united-states)

**Tripadvisor**
- AI-сводки отзывов об отелях. И путешественники, и владельцы могут оставить feedback на каждую сводку — [Hospitality Net](https://www.hospitalitynet.org/news/4118778/tripadvisor-launches-ai-powered-hotel-review-summaries)
- Июль 2026, расследование Which?: сводки смягчали серьёзные жалобы. У курорта на Кабо-Верде чистота в сводке «spotless», а в отзывах было 102 упоминания пищевого отравления (Riu Palace, март 2026). Бот Ollie тоже не предупредил о проблемах с гигиеной — [Euronews](https://euronews.com/travel/2026/07/03/tripadvisor-ai-summaries-give-glowing-reviews-to-dangerous-hotels-consumer-watchdog-finds)

**Trip.com (TripGenie)**
- Запрос «отели рядом с метро Бангкока» ведёт на кураторский список. TripGenie опирается на рейтинги Trip.Best, Trip.Deals и Trip.Trends — [TTG Asia](https://www.ttgasia.com/?p=91465)
- Trip.Best (2025) ранжирует отели по 14 темам (семья, виды, культура и т. д.) и показывает только топ-1% — [ITB News](https://news.itb.com/?p=49778)
- TripGenie работает как оверлей поверх приложения, принимает текст и голос — [PhocusWire](https://www.phocuswire.com/Trip-com-unveils-AI-travel-assistant-focused-on-personalization)

**Mindtrip Stays**
- Разговорный поиск отелей вместо фильтров. Учитывает цены, удобства, отзывы и **room-level details**, объясняет, почему объект подходит под критерии, и следит за ценой после поиска. Инвентарь берётся у Priceline, Nuitee, Expedia, Booking.com и Agoda — [Hotel Management](https://www.hotelmanagement.net/tech/mindtrip-launches-ai-powered-hotel-search-booking-platform)
- В интерфейсе чат с уточнениями и сравнениями слева, детали справа, ссылка на бронь — [AFAR](https://www.afar.com/magazine/we-tested-ai-travel-planning-apps-here-are-the-3-that-actually-worked?country=85)
- Дата запуска в источниках расходится: агрегатор поиска назвал июль 2026, а URL пресс-релиза на BusinessWire датирован 2025-12-08 — [BusinessWire](https://www.businesswire.com/news/home/20251208469469/en)

**Perplexity и ChatGPT**
- Март 2025: Perplexity запустил бронирование отелей внутри ответа. Tripadvisor даёт отзывы и информацию об объектах, Selfbook отвечает за бронь и оплату. Доступно около 140 000 объектов — [PhocusWire](https://www.phocuswire.com/perplexity-selfbook-agentic-ai-travel-booking-tripadvisor)
- Октябрь 2025: Booking.com и Expedia вошли в число первых партнёров Apps SDK в ChatGPT. Приложения построены на MCP. В Expedia можно уточнять запрос («Only show 4-star hotels»), а бронь завершается на сайте Expedia — [AltexSoft](https://www.altexsoft.com/travel-industry-news/chatgpt-turns-into-a-travel-hub-with-expedia-and-booking-com); [TechCrunch](https://techcrunch.com/2025/10/24/how-to-use-the-new-chatgpt-app-integrations-including-spotify-figma-canva-and-others)

### Inferences
- Рынок пришёл к одной формуле: NL переводится в **существующие** фильтры, Q&A и сводки строятся по **существующему** контенту. Атрибуты, которых нет в карточке, этой формулой не найти: «закат с 5-го этажа» не стоит галочкой ни у одного хоста. Ровно эту нишу и задаёт кейс Островка.
- Пометка «derived from host-provided details» у Airbnb показывает, что лидеры уже осознают проблему происхождения данных. Но решают её одним общим ярлыком, а не provenance для каждого факта.
- История с Which? и Tripadvisor — готовый аргумент для питча: генеративные сводки без опоры на доказательства «отбеливают» отели, а честное «не дотягивает» встречается редко.
- Content Agent у Expedia и Auto-Reply у Booking ближе всего к нашей идее диалога с отельером, но они работают на уровне контента и переписки. Структурированных атрибутов с уверенностью они не заполняют.
- Booking и Expedia открыли свои приложения в ChatGPT через MCP, и Expedia завела Dev MCP server. Если выставить нашу базу атрибутов как MCP-tool, это будет понятный жюри сигнал «agentic-ready».

### Gaps
- Нет первоисточника по функциям Airbnb 2026: newsroom не найден, всё из блогов.
- Страница Expedia ask-filter и newsroom Explore 2026 не открылись (429). Детали UX, например показываются ли цитаты отзывов в ответе, не проверены.
- Не найдено, куда уходит вопрос из Property Q&A Booking, на который в данных нет ответа, и передаётся ли он отельеру.
- По Layla найти актуальную информацию не удалось, этот продукт не исследовался.

---

## 2. Российский рынок: Яндекс Путешествия, Отелло (2ГИС), МТС Travel, OneTwoTrip, Суточно, Авито, сам Островок

### Takeaway
В России AI в тревеле пока используется для **генерации** контента (Алиса пишет описания, сортирует фото по комнатам) и для чат-ассистентов поверх своих данных (Отелло + 2ГИС). Публичных продуктов, которые вычисляют атрибуты по геоданным, не найдено. Собственного пользовательского AI-поиска у Островка в открытых источниках тоже нет. Публично известна только внутренняя автоматизация: голосовой бот, который обзванивает отели.

### Cited Findings
- 21.10.2025: Яндекс Путешествия подключили Алису к генерации описаний отелей и апартаментов. Отельер вводит технические данные в Яндекс Бизнесе или Extranet, а Алиса пишет описание с учётом достопримечательностей рядом и отзывов гостей. При изменении данных описание обновляется само, исправления идут через поддержку. Ещё одна функция: AI определяет, к какой комнате относится загруженное фото. Цифр эффекта нет — [AdIndex](https://adindex.ru/news/digital/2025/10/21/338296.phtml)
- В 2025 году в Яндекс Путешествиях вышло 40+ обновлений, среди них тематические подборки направлений (винодельни, рыбалка) и страницы поездок с погодой и подборкой отелей. Что подборки делает нейросеть, не утверждается — [Rozetked](https://rozetked.me/articles/43511)
- Подтверждения, что Яндекс Путешествия ищут отели по свободному запросу через Алису, не найдено. Обычный поиск работает на фильтрах по цене, типу и рейтингу — [vc.ru](https://vc.ru/travel/2618285-kak-vybrat-oteli-v-yandeks-puteshestviyakh); есть тест Алисы AI на сложном запросе «с детьми и собакой» — [Турдом](https://www.tourdom.ru/hotline/obzory-tekhnologiy/ozadachili-alisu-ai-zaprosom-na-podbor-otelya-chtoby-otdokhnut-s-detmi-sobakoy-i-vstretit-lyubov/)
- 2026: Яндекс ставит Алису в гостиничные номера. Она отвечает гостю по базе знаний, которую ведёт персонал отеля — [Калининград daily](https://kaliningraddaily.com/technology/2026061796900)
- Апрель 2026: Отелло (сервис бронирования 2ГИС) запустил AI-ассистента. Запросы на естественном языке («хочу отель рядом с Эрмитажем»), карточки отелей прямо в чате с картой. Ассистент отвечает про парковку, камеру хранения и время завтрака, суммирует отзывы, помнит контекст и даты. Данные: отели и партнёры, справочник и отзывы 2ГИС. По оценке компании, около 20% обращений в поддержку — вопросы о конкретных отелях — [RB.ru](https://rb.ru/news/servis-bronirovaniya-otello-zapustil-ii-assistenta-nejroset-pomogaet-vybrat-zhilyo-i-sostavit-marshrut-poezdki/)
- МТС Travel запущен в конце 2022 года, персональные рекомендации внедряются с 2024 года. Про генеративный поиск сведений нет — [Bnovo](https://bnovo.ru/blog/mts-travel/)
- Пилот MTS AI для неназванного сервиса бронирования (кейс датирован 03.10.2023): DETR проверяет, что заявленное в листинге есть на фото, и дописывает в листинг то, что видно на фото. Точность около 95%, в пилоте 6 типов объектов, в планах 29 параметров — [MTS AI](https://mts.ai/resheniya/search-by-description/)
- Островок, внутренний кейс: голосовой бот на платформе Just AI обзванивает отели-партнёров и фиксирует результат. Заявлены замена 10 операторов колл-центра и ускорение обзвона в 4 раза. Там же: на платформе более 3,2 млн вариантов размещения — [Generation AI](https://generation-ai.ru/cases/ostrovok-voice-bot-hotels)
- В блоге Островка есть пользовательский гайд «как спланировать путешествие с помощью нейросетей». Это контент, не продукт — [Блог Островка](https://blog.ostrovok.ru/kak-splanirovat-puteshestvie-s-pomoshhyu-nejrosetej/)
- Про AI-поиск у OneTwoTrip, Суточно и Авито Путешествий в найденных материалах ничего нет — [Hi-Tech Mail](https://hi-tech.mail.ru/articles/145641-luchshie-prilozheniya-dlya-bronirovaniya-zhilya-v-rossii/)

### Inferences
- Главный конкурент на рынке РФ и при этом вероятный покупатель Островка (см. раздел 6) — Яндекс. Его AI-ставка в тревеле сейчас сводится к генеративным описаниям из данных отельера. Наш питч может противопоставить «сгенерированное описание» и «вычисленный и проверяемый факт».
- Голосовой бот Островка показывает, что канал автоматической коммуникации с отельерами у компании уже есть. Наш «диалог с отельером» встраивается в существующий операционный процесс и не выглядит фантазией.
- Отелло подтверждает спрос на Q&A по конкретному отелю: 20% обращений в поддержку — именно такие вопросы. Цифру можно использовать в питче, указав источник.

### Gaps
- Пользовательских AI-функций Островка (чат, NL-поиск) в открытых источниках не найдено. Возможно, они есть в закрытом тесте, но это не подтверждено.
- Нет данных об AI-функциях Суточно, Авито Путешествий и OneTwoTrip за 2025–2026 годы.

---

## 3. Нишевые продукты «attribute data» (шум, пешая доступность, солнце, вид, семантика отзывов, room mapping)

### Takeaway
Отдельные кирпичики есть: HowLoud (шум), Walk Score (пешая доступность по маршрутам), Shadowmap SunScore (инсоляция по 3D), TrustYou (семантика отзывов), Gimmonix и GIATA (room mapping с полем view), CV-проверка фото. Но каждый закрывает один атрибут, работает **на уровне адреса или здания**, а не номера, этажа или фасада, лицензируется B2B (чаще для недвижимости) и **не объединён** с отзывами, ответами отельера и поиском на естественном языке.

### Cited Findings
- HowLoud Soundscore: оценка 0–100 (50 очень громко, 100 очень тихо). Строится из дорожного трафика, авиатрафика и локальных источников вроде школ, магазинов и ресторанов. Лицензируется компаниям из недвижимости — [Built In LA](https://www.builtinla.com/articles/know-your-noise-level-howloud-and-factual-create-soundscore-homes); [Architect Magazine](https://www.architectmagazine.com/design/walk-score-except-for-sound_o/)
- Walk Score: для каждого адреса анализируются сотни пеших маршрутов к объектам, баллы зависят от расстояния с функцией затухания. Есть API с Transit Score — [Walk Score methodology](https://www.walkscore.com/methodology.shtml); [APIs.io](https://apis.apievangelist.com/store/walk-score-scores-api/). В 2013 году существовал прототип hotel finder на Travel Time API с отелями из Expedia Affiliate Network — [GeekWire](https://www.geekwire.com/2013/sheraton-space-needle-walk-score-unveils-hotel-finder/)
- Shadowmap SunScore API: оценка 0–100 годовой прямой инсоляции, считается по 3D-геометрии зданий, рельефу и траектории солнца. Партнёры подключаются выборочно — [Shadowmap API](https://shadowmap.org/api). Shadowmap даёт sunlight scoring для Redfin и Willhaben, 700 000+ MAU — [Stadia Maps case](https://stadiamaps.com/customer-stories/shadowmap/). Shadowmap Studio: встраиваемая 3D-визуализация солнца для листингов недвижимости — [Shadowmap Studio](https://studio.shadowmap.org/)
- TrustYou: семантический анализ отзывов по тональности и категориям (room, service, food, amenities), более 320 000 отелей, 23+ языка, meta-review в виде текстовой сводки. Источники в основном 2013–2014 годов — [PhocusWire](https://www.phocuswire.com/TLabs-Showcase-TrustYou); [TrustYou blog](https://www.trustyou.com/blog/guest-feedback-reviews/review-analytics-hospitality-tourism)
- Room mapping: поставщики по-разному описывают один и тот же номер, и view — одно из полей, которые дублируются. Gimmonix извлекает тип кровати, вид и удобства и приводит их к единым категориям. Источник вида при этом — текст поставщика, а не вычисление — [AltexSoft](https://www.altexsoft.com/blog/cloud/hotel-mapping-tools-how-giata-gimmonix-databindr-and-other-solutions-work/); [Gimmonix](https://gimmonix.com/news/how-does-room-mapping-work)
- Room 77 (исторический кейс) показывал фото видов из номеров. Его программа Hotel Verified охватывала около 100 отелей в 18 направлениях, данные собирались через приложение — [PhocusWire](https://www.phocuswire.com/Inside-out-Room-77-begins-displaying-room-photos-too)
- meerblickurlaub.de — каталог только отелей Европы с настоящим видом на море, который ведут «два AI-агента». Как проверяется вид, не раскрыто — [Travel Massive](https://www.travelmassive.com/posts/i-built-a-website-that-only-lists-hotels-in-europe-with-a-sea-view-237525593)
- Фото в листингах ненадёжны: 4 778 снимков, то есть 19% изученных, дали хотя бы один «AI red flag» (ABCD Agency и ContentGuard.me) — [Canberra Times](https://www.canberratimes.com.au/story/9307149/how-to-spot-ai-and-fake-images-in-hotel-and-holiday-listings/)
- «Тихие» программы отелей: у Crowne Plaza Sleep Advantage с 2004 года есть «Quiet Zone» — минимум один этаж, где с вс по чт с 21:00 до 10:00 не работают уборка и техслужба, плюс гарантия wake-up call. Источники 2004–2010 годов — [Business Traveller](https://www.businesstraveller.com/news/2010/04/24/crowne-plaza-aims-to-help-you-sleep-better/)

### Inferences
- Шкала 0–100 с прозрачной методикой (Soundscore, SunScore, Walk Score) — проверенный UX-паттерн для «мягких» атрибутов. Его стоит повторить, но **на уровне фасада, этажа и номера**, с разбивкой факторов.
- Тишина у Crowne Plaza — это свойство **этажа и времени** (Quiet Zone). Это подтверждает, что тишина нужна на уровне номера, а не здания.
- Слабое место всех нишевых продуктов: нет связки «вычисление ↔ отзывы ↔ ответ отельера ↔ разрешение конфликтов». В этом наша ниша.

### Gaps
- Не найдено актуальное покрытие HowLoud за 2025–2026 годы и есть ли у него публичный API. Источники старые.
- Нет документации GIATA по атрибутам вида номера.
- Стартап, который проверяет вид на море из конкретного номера, не найден. Это скорее признак незанятой ниши, чем пробел поиска.
- ReviewPro и Olery отдельно не исследовались.

---

## 4. Чего существующие продукты НЕ делают (пробелы)

### Takeaway
Ни у кого не найдено четырёх вещей: (1) атрибутов, вычисленных на уровне номера, фасада или этажа; (2) показа источника, уверенности и даты по каждому факту; (3) замкнутой петли вопросов к отельеру, которая структурированно пополняет базу атрибутов; (4) явной политики свежести и перепроверки.

### Cited Findings
- AI-сводки Airbnb строятся из текста хоста и только помечены как «derived from host-provided details». Верификации нет — [RentalScaleUp](https://www.rentalscaleup.com/airbnb-natural-language-search/)
- AI-фильтры Airbnb работают на удобствах, которые отметил хост: «baby» раскрывается в кроватки и детские площадки — [Hostaway](https://www.hostaway.com/blog/airbnb-2026-fall-update-what-it-means-for-hosts/)
- Booking Property Q&A берёт ответы из листинга, отзывов и фото. Передача вопросов без ответа отельеру не описана — [Booking.com](https://news.booking.com/bookingcom-enhances-travel-planning-with-new-ai-powered-features--for-easier-smarter-decisions/)
- Google AI Mode показывает «key factors» и отзывы. Атрибутный поиск по виду или тишине в описании не фигурирует — [TechCrunch](https://techcrunch.com/2026/08/27/googles-ai-mode-can-now-track-flight-prices-help-book-hotels-and-more/)
- Сводки Tripadvisor систематически смягчают негатив — [Euronews / Which?](https://euronews.com/travel/2026/07/03/tripadvisor-ai-summaries-give-glowing-reviews-to-dangerous-hotels-consumer-watchdog-finds)
- Expedia Content Agent ищет, «what information travelers still need». Это ближайший аналог, но он работает на стороне контента партнёра, и его сроки не объявлены — [Expedia Partner blog](https://partner.expediagroup.com/en-us/resources/blog/ai-at-explore-2026-what-partners-need-to-know)
- Яндекс генерирует описания из данных отельера и отзывов, исправления только через поддержку — [AdIndex](https://adindex.ru/news/digital/2025/10/21/338296.phtml)
- У самого Островка CV проверяет соответствие фото описанию поставщика и показывает «подтверждённые параметры номера». При этом бывали ошибки, например туалеты распознавались как столовые. Их обычно находят отельеры, контент-команда и аккаунт-менеджеры — [Habr, Островок](https://habr.com/ru/companies/ostrovok/articles/831126/)

### Inferences
- Пробел (1): **вычисление**. Никто не считает line-of-sight, шум по фасадам или пеший маршрут с перепадом высот для отелей.
- Пробел (2): **provenance UX**. Даже лидеры показывают в лучшем случае общий ярлык «из данных хоста».
- Пробел (3): **hotelier loop**. Есть автоответы на вопросы гостей (Booking Auto-Reply) и детектор пробелов в контенте (Expedia), но нет агента, который сам формулирует вопрос там, где уверенность низкая, раскладывает свободный ответ по схеме и отмечает конфликты.
- Пробел (4): **freshness**. Перепроверка, например «рядом построили дом и закрыли вид», нигде не описана.
- Самоподтверждение Островка: компания уже ведёт «подтверждённые параметры номера» через CV и знает цену ошибок распознавания. Тема verified-атрибутов для неё не новая, и жюри должно быть в контексте.

### Gaps
- Публичных метрик точности AI-ответов (Q&A, сводки) ни у одного игрока не найдено.

---

## 5. Академический и индустриальный prior art

### Takeaway
Есть зрелые направления: aspect-based sentiment по отзывам (в том числе туризм), туристические KG + RAG, LLM-агенты для планирования (TravelPlanner) и объяснимые рекомендации на LLM. Ключевые факты для питча: на TravelPlanner агенты плохо удерживают много жёстких ограничений (у GPT-4 успех 0.6%), а исследований объяснений рекомендаций на LLM ещё мало.

### Cited Findings
- TravelPlanner (ICML 2024, arXiv 2402.01622): 1 225 запросов на планирование, песочница с инструментами и почти 4 млн записей. «Even GPT-4 only achieves a success rate of 0.6%». Агенты сбиваются с задачи, плохо выбирают инструменты и плохо отслеживают несколько ограничений — [arXiv](https://arxiv.org/abs/2402.01622v2); [PMLR](https://proceedings.mlr.press/v235/xie24j.html)
- Систематический обзор объяснений рекомендаций на LLM: из 232 статей ACM критериям отвечают только 6, и область оценена как ранняя — [arXiv 2411.19576](https://arxiv.org/pdf/2411.19576); [PMC](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11808143/)
- Обзор LLM-агентов для рекомендательных систем (Findings EMNLP 2025) выделяет interaction-oriented парадигму: многоходовой диалог для выяснения предпочтений и интерпретируемые объяснения — [ACL Anthology](https://preview.aclanthology.org/dashboard/2025.findings-emnlp.620)
- ABSA в туризме на LLM: извлечение четвёрок aspect–category–opinion–sentiment с учётом позиционной информации — [arXiv 2409.14997](https://arxiv.org/pdf/2409.14997). Датасет OATS Hotels (Chebolu et al., 2024) упоминается как бенчмарк ABSA по отзывам TripAdvisor — [arXiv 2605.28058](https://arxiv.org/pdf/2605.28058). Гибрид, где LLM выделяет аспекты, а классический ML классифицирует тональность ради масштаба — [arXiv 2602.21082](https://arxiv.org/abs/2602.21082v1)
- KG + LLM для отелей и туризма: HII-KG (Findings EACL 2026) сочетает retrieval подграфов KG с генерацией по отзывам об отелях — [ACL Anthology](https://preview.aclanthology.org/ingest-eacl/2026.findings-eacl.285.pdf). QCG-Rerank — reranking в туристическом RAG — [arXiv 2411.08724](https://arxiv.org/html/2411.08724v1). TravelRAG строит многослойный KG из соцсетей как источник для LLM — [IJGI 13:414](https://mdpi-res.com/d_attachment/ijgi/ijgi-13-00414/article_deploy/ijgi-13-00414.pdf). Полные тексты не проверялись.
- У самого Островка: матчинг отелей в «метаотель» (ближайшие соседи, затем фильтрация пар, затем классификация) и мультимодальный матчинг комнат в «метакомнату» — [Habr, Островок](https://habr.com/ru/companies/ostrovok/articles/831126/)

### Inferences
- TravelPlanner — хороший аргумент за нашу архитектуру. Жёсткие ограничения («тихо» И «закат» И «≤10 мин до моря») надо проверять **детерминированно** по структурированным атрибутам, а LLM оставить разбор запроса и объяснение. Так мы прямо закрываем слабость, которую показал бенчмарк.
- Объяснимые рекомендации — молодая область, поэтому explanation с доказательствами («почему подходит / в чём не дотягивает») воспринимается как новизна.
- Атрибуты лучше привязывать к сущности уровня «метакомнаты» или категории номера, это совместимо с моделью данных Островка.

### Gaps
- Не найдено работ, которые вычисляют вид из окна или шум по фасаду специально для отелей. Есть общие GIS-работы, но они не искались.
- Даты и площадки HII-KG и arXiv 2605.28058 не верифицированы.

---

## 6. Островок / ETG: бизнес, масштаб, стек, как думают о контенте, поиске и LLM

### Takeaway
Островок — второй по доле OTA в России. Владелец, американская ETG, продаёт российский бизнес, и главный претендент — Яндекс, причём технологическая платформа остаётся у ETG. Инженерно это Go и Python, ML на CatBoost, CV для фото и матчинга, небольшая LLM для перевода 50 млн отзывов, сверхбыстрый поиск в памяти на Go. Тема качества контента («подтверждённые параметры номера», метакомнаты) для них давно своя.

### Cited Findings

**Бизнес и масштаб**
- По TravelLine за 2024 год доля Островка в онлайн-бронировании отелей 21,5% (2-е место), у Яндекс Путешествий 29,2%. Консолидированная выручка Островка за 2024 год 9,7 млрд ₽ (+40%). Более 23 млн посетителей в месяц, 9,5 млн установок приложения, 2,9+ млн отелей в мире. Компания основана в 2010 году. Совокупный оборот RateHawk, ZenHotels и Roundtrip за 2024 год — $3,7 млрд — [Profi.Travel, 06.11.2025](https://profi.travel/news/63693/details)
- ETG (основатели — выходцы из Google Сергей Фаге и Кирилл Махаринский) продаёт Островок, чтобы сосредоточиться на зарубежных проектах. Российский и международный бизнес разделили в 2022 году — [Profi.Travel](https://profi.travel/news/63693/details)
- 28.05.2026: Яндекс назван основным претендентом. Продавец хочет 20 млрд ₽, покупатель готов не более чем на 15 млрд. В сделку входят **только российские активы: бренд и контракты с отельерами. Программная платформа остаётся у ETG** для международного B2B. Нужно одобрение ФАС. По TravelLine, у Яндекс Путешествий вместе с Acase 34,9% продаж OTA, у Островка 24,8% — [Kod.ru](https://kod.ru/yandex-ostrovok-sdelka); [Коммерсант](https://www.kommersant.ru/doc/8692225); [Ведомости](https://www.vedomosti.ru/business/news/2026/05/28/1200795-yandeks-stal-osnovnim)
- Новостей о закрытии сделки за сентябрь–октябрь 2026 года поиск не нашёл. Последние сообщения датированы концом мая 2026 года — [CNews](https://www.cnews.ru/news/top/2026-05-28_yandeks_monopoliziruet)
- Юрлицо — ООО «Бронирование гостиниц», 501–1000 сотрудников, блог на Хабре с 2011 года — [Habr, Островок](https://habr.com/ru/companies/ostrovok/news/1072506/)

**Поиск, ранжирование и ML** (Habr, 24.07.2024, Иван Елфимов)
- 1,5 млн поисковых запросов в минуту, до 100 GiB данных в минуту, 230 поставщиков и 100 000 прямых контрактов с отелями — [Habr](https://habr.com/ru/companies/ostrovok/articles/831126/)
- Ранжирование на CatBoost, обучение на 10 млн сессий за 30 дней по 1000 регионам, до 5000 отелей в каждом. Признаки: отзывы, рейтинг, звёзды, удобства, история отеля и превышение средней цены по региону. Онлайн point-wise reranking 100–200 отелей. Look-to-book модель даёт +3–4% конверсии. Есть Dynamic Cache TTL — [Habr](https://habr.com/ru/companies/ostrovok/articles/831126/)
- 50 млн отзывов переводит на 20 языков «небольшая LLM». В планах персонализированный выбор самого релевантного отзыва — [Habr](https://habr.com/ru/companies/ostrovok/articles/831126/)
- Контент: метаотель и метакомната. CV проверяет, соответствует ли фото описанию поставщика, и показывает «подтверждённые параметры номера». Умная галерея ранжирует фото с учётом интересов пользователя — [Habr](https://habr.com/ru/companies/ostrovok/articles/831126/)

**Инфраструктура поиска** (Habr, 18.12.2025, Иван Коломбет)
- Старый стек Python + Postgres + Redis упёрся в масштабирование, задержки доходили до 60 с. Новый in-memory движок на Go даёт 30 000 RPS вместо 700, p99 60 мс вместо 2000 мс, cache-hit 99,992%. В сервисе 130 тыс. отелей, 700 тыс. категорий номеров и 2,5 млн тарифов — [Habr](https://habr.com/ru/companies/ostrovok/articles/972084/)

**Операции**
- Голосовой бот (Just AI) обзванивает отели-партнёров вместо 10 операторов — [Generation AI](https://generation-ai.ru/cases/ostrovok-voice-bot-hotels)
- Островок публикует поведенческую аналитику: например, среднее окно бронирования в Петербурге в 2025 году — 24 дня (в 2024 году 22 дня) — [ДП](https://www.dp.ru/a/2026/02/25/u-nas-otmena-oteleri-gotovjatsja)

### Inferences
- Язык жюри: Go и Python, ML-прагматизм (CatBoost, метрики конверсии), инженерная надёжность (p99, RPS). Решение стоит подавать как сервис с измеримыми метриками (покрытие, точность, latency), а не как «чат с LLM».
- «Метакомната» у Островка — естественная точка привязки атрибутов уровня номера (сторона фасада, этаж). Об этом стоит сказать на питче: «ложится на вашу модель данных».
- User story Островка («конверсия будет расти, а отмен станет меньше») хорошо сочетается с их look-to-book мышлением. Стоит показать гипотезу, как verified-атрибуты снижают отмены по причине «не соответствует ожиданиям», но без выдуманных цифр.
- Продажа бизнеса Яндексу (если состоится) делает дифференциацию от Яндекса по данным стратегически ценной. Это стоит упоминать аккуратно, потому что сделка не подтверждена.

### Gaps
- Нет публичных данных Островка о доле отмен и их причинах, включая «не соответствует описанию».
- Не найдено статей Островка на Хабре про LLM/RAG в продукте, кроме перевода отзывов.
- Не найдено публичных данных о закрытии сделки с Яндексом.

---

## 7. Хакатоны Островка (2024, 2025, 2026): формат, треки, жюри, победители

### Takeaway
«О! Хакатон» проходит ежегодно, онлайн, 8 дней, с призами 250, 150 и 100 тыс. ₽ на трек. 2026 год — третья итерация и первая целиком про AI. Критерии оценки не публикуются, а формируются на закрытых сессиях экспертов «по релевантности задаче». Финал устроен жёстко: 5 минут питч и 5 минут вопросы, до 10 финалистов. Есть первичный LLM-фильтр сабмитов, и решение обязано разворачиваться одной командой `docker compose up`. Списков победителей прошлых лет в открытых источниках нет.

### Cited Findings

**2024 (1-я итерация)**
- По агрегатору хакатонов: 28.09–05.10.2024, треки «динамическое ценообразование» и «система ранжирования отелей». Упоминается также задача по сбору и тегированию данных о номерах в реестр. Призы 250, 150 и 100 тыс. ₽. Данные агрегатора противоречат друг другу и официальной страницей не подтверждены — [хакатоны.рф](https://www.xn--80aa3anexr8c.xn--p1acf/tpost/xcsvren761-o-hakaton)

**2025 (2-я итерация)**
- 26.09–03.10.2025, онлайн, middle/senior Python/Go. Треки: инструмент «Секретный гость» для улучшения бронирования и оценки отелей и персонализированный подбор отелей по данным о пользователе. 500 000 ₽ на трек — [Habr, Островок](https://habr.com/ru/companies/ostrovok/posts/939850/); [ICT2GO](https://ict2go.ru/events/61923/)
- Tproger (рекламный материал Островка): «единственный в России регулярный travel-tech хакатон». Во второй итерации рекордные 460 команд; в другом месте статьи — 800 регистраций, цифры внутренне не согласованы. Охват промо 1,4 млн, 19 000 визитов лендинга. **LLM делала первичную фильтрацию пустых и сырых сабмитов**, затем 18 экспертов оценивали «техническую и продуктовую эффективность» идей. Упоминаются бонусный проект «Tinder для отелей» и решение, которое эксперты назвали «невозможным для реализации за неделю». Результат для компании — прямой найм. Бот хакатона написан на Go + PocketBase, лендинг на Next.js. Победители не названы — [Tproger](https://tproger.ru/articles/go--llm-i-1-4-mln-ohvata--kak-ostrovok-delaet-edinstvennyj-regul)

**2026 (3-я итерация, текущая)**
- 23–30.10.2026, онлайн, команды до 5 человек, призовой фонд 1 млн ₽ (по 500 тыс. на трек). Трек «О!дин шаг до идеального отеля» для middle/senior, трек «О!дин запрос» (AI-агент для путешествий) для junior. Эксперты: Владимир Симонов (Senior Engineering Manager), Галина Ревина (Senior Project Manager), Михаил Решетняк (Team Lead) — [ostrovok.tech/hackathon](https://ostrovok.tech/hackathon/); [Habr](https://habr.com/ru/companies/ostrovok/news/1072506/)
- Правила: «Критерии оценок Жюри формируются на закрытых экспертных сессиях с Экспертами по принципу релевантности установленной Задаче». Сдача до 10:00 29.10. Жюри отбирает **до 10** финалистов. Финал 30.10 с 17:00 до 19:00: «5 минут – презентация решения и 5 минут – ответы на вопросы Жюри», после питча жюри может пересмотреть оценки. Призы на трек: 250, 150 и 100 тыс. ₽ — [Правила О! Хакатона (PDF)](https://ostrovok.tech/hackathon/docs/rules.pdf)
- Технические правила: один репозиторий в организации ostrovok-hackathon-2026, обязательны README, docker-compose.yml, Dockerfile и .env.example, git-тег `submission`, порт 8080 и `/health`, сборка multi-stage, запуск не от root, развёртывание не дольше примерно 10 минут без ручных шагов. Обязательно видео-скринкаст. LLM подключается через OpenAI-совместимое API. «В случае, если возникнут проблемы при локальной развёртке — мы не сможем… проставить соответствующие баллы» — [docs.ostrovok.tech, локальная копия RULES.md](https://docs.ostrovok.tech/s/b9e6ec5e-5ad6-4091-9c85-a48eee9b21cc)
- Организаторы в Telegram пообещали учитывать, что одиночным участникам сложнее — локальные заметки `/Users/shttrkk/ostrovok-hack/materials/TELEGRAM_NOTES.md`, источник без URL

### Inferences
- Что ценит жюри: по Tproger — «техническая и продуктовая эффективность». Эксперты 2026 года — инженерный менеджмент, тимлид и проектный менеджер. Ставка, скорее всего, на **работающий, воспроизводимый прототип с реальной продуктовой ценностью**, а не на концепт.
- LLM-префильтр означает, что README, структура репозитория и описание должны быть понятны машине: явные разделы «что вычисляется», «источники», «метрики», «как запустить».
- Пять минут питча — это вау-момент в первые 60 секунд: один запрос («тихий номер с видом на закат недалеко от моря»), выдача с объяснением «закат есть, но с 5-го этажа», карточка доказательства (карта line-of-sight и источники) и вопрос отельеру в реальном времени.
- Упоминание «невозможного за неделю» решения показывает, что амбициозный технический охват жюри замечает и ценит.
- Если сборка не поднимется, баллов не будет. Воспроизводимость критична.

### Gaps
- Победителей и их проектов за 2024 и 2025 годы найти не удалось (возможно, публиковались только в Telegram-канале хакатона).
- Конкретные критерии и веса оценки не публикуются.
- Состав жюри 2026 года не объявлен. Известны только эксперты.
- Официальное подтверждение треков 2024 года не найдено.

---

## 8. Что взять в наш продукт (10–15 заимствуемых паттернов)

### Takeaway
Берём проверенные UX-паттерны лидеров: NL в редактируемые фильтры-чипы, Q&A на карточке, AI-сравнение, объяснение соответствия, шкалы 0–100 с методикой. Поверх них добавляем то, чего у лидеров нет: доказательства и уверенность по каждому факту.

### Cited Findings
1. **Разбор NL-запроса в видимые и редактируемые чипы-фильтры**. «Тихо», «закат», «≤10 мин до моря» показываются как чипы, которые можно убрать, ужесточить или ослабить, с весами must/nice — паттерн Smart Filter Booking и AI-фильтров Airbnb («baby» → кроватки, площадки) — [Booking.com](https://news.booking.com/bookingcom-enhances-travel-planning-with-new-ai-powered-features--for-easier-smarter-decisions/); [Hostaway](https://www.hostaway.com/blog/airbnb-2026-fall-update-what-it-means-for-hosts/)
2. **Блок «Спросить об этом отеле» с готовыми вопросами** на карточке («Тихо ли ночью?», «Какие номера на запад?») — паттерн «Ask Maps about this place» в Google Maps и «Have a question?» в Expedia — [9to5Google](https://9to5google.com/2024/10/31/google-maps-gemini/); [Expedia](https://www.expedia.com/product/ask-filter/)
3. **Ярлык происхождения у каждого AI-утверждения**. У Airbnb это один общий ярлык «derived from host-provided details», у нас иконка источника у каждого факта: геоданные, отзывы или отельер — [RentalScaleUp](https://www.rentalscaleup.com/airbnb-natural-language-search/)
4. **Сводка, персонализированная под запрос**: показываем то, что важно этому гостю (парковка, доступность), а не общий пересказ — Booking Review Summaries — [Booking.com](https://news.booking.com/bookingcom-enhances-travel-planning-with-new-ai-powered-features--for-easier-smarter-decisions/)
5. **AI-сравнение с явными trade-offs** по собранным атрибутам — Hotels.com AI Property Compare («vibe, location, amenities, and trade-offs») и сравнение wishlist у Airbnb — [Expedia Newsroom](https://www.expedia.com/newsroom/expedia-group-unveils-new-ai-experiences-expands-travel-ecosystem-and-launches-philanthropy-program-at-explore-2026/); [RentalScaleUp](https://www.rentalscaleup.com/airbnb-fall-update-2026-guest-features/)
6. **Объяснение «почему подходит»** у каждого результата — Mindtrip Stays «explains why properties match» — [Hotel Management](https://www.hotelmanagement.net/tech/mindtrip-launches-ai-powered-hotel-search-booking-platform)
7. **Раскладка: чат и уточнения слева, карточки и карта справа**, отели карточками прямо в диалоге — Mindtrip и Отелло — [AFAR](https://www.afar.com/magazine/we-tested-ai-travel-planning-apps-here-are-the-3-that-actually-worked?country=85); [RB.ru](https://rb.ru/news/servis-bronirovaniya-otello-zapustil-ii-assistenta-nejroset-pomogaet-vybrat-zhilyo-i-sostavit-marshrut-poezdki/)
8. **Шкалы 0–100 с открытой методикой и разбивкой факторов** («Тишина 78/100: магистраль 2-го класса в 120 м с восточного фасада, бар в 300 м…») — HowLoud Soundscore и Shadowmap SunScore — [Built In LA](https://www.builtinla.com/articles/know-your-noise-level-howloud-and-factual-create-soundscore-homes); [Shadowmap API](https://shadowmap.org/api)
9. **Доступность по реальному маршруту, а не по прямой** — методика Walk Score: сотни пеших маршрутов и функция затухания — [Walk Score](https://www.walkscore.com/methodology.shtml)
10. **Визуализация солнца и видимости** (3D или карта с линией обзора и временем заката по месяцам) — Shadowmap Studio для листингов недвижимости — [Shadowmap Studio](https://studio.shadowmap.org/)
11. **Content Agent для отельера**: система сама находит, каких сведений не хватает гостям, и спрашивает; темы ответов задаются один раз и переиспользуются — Expedia Content Agent и Booking Auto-Reply — [Expedia Partner blog](https://partner.expediagroup.com/en-us/resources/blog/ai-at-explore-2026-what-partners-need-to-know); [Hospitality Net](https://www.hospitalitynet.org/news/4129287/bookingcom-debuts-agentic-ai-innovations-adding-to-its-robust-suite-of-genai-tools-for-customers)
12. **Feedback-кнопка на каждом факте** для гостя и для отельера («верно / неверно / исправить») — Tripadvisor собирает feedback на сводки от путешественников и владельцев — [Hospitality Net](https://www.hospitalitynet.org/news/4118778/tripadvisor-launches-ai-powered-hotel-review-summaries)
13. **Тематические подборки как точки входа в демо** («Лучшие для сна», «Закат из окна», «С собакой») — Trip.Best (14 тем) и тематические подборки Яндекс Путешествий — [ITB News](https://news.itb.com/?p=49778); [Rozetked](https://rozetked.me/articles/43511)
14. **Передача сложного вопроса человеку**: если уверенности нет, вопрос уходит отельеру, как Booking AI Trip Support передаёт сложные случаи оператору — [Hospitality Net](https://www.hospitalitynet.org/news/4129287/bookingcom-debuts-agentic-ai-innovations-adding-to-its-robust-suite-of-genai-tools-for-customers)
15. **MCP-интерфейс к базе атрибутов**, чтобы внешний агент (ChatGPT, «О!дин запрос»-подобный) мог искать по нашим атрибутам — приложения Booking и Expedia в ChatGPT на MCP, Dev MCP server у Expedia — [AltexSoft](https://www.altexsoft.com/travel-industry-news/chatgpt-turns-into-a-travel-hub-with-expedia-and-booking-com); [Expedia Partner blog](https://partner.expediagroup.com/en-us/resources/blog/ai-at-explore-2026-what-partners-need-to-know)

### Inferences
- Пункты 1, 2, 6 и 7 — минимум, без которого продукт выглядит слабее Booking и Airbnb. Пункты 3, 8, 10 и 11 сразу отделяют нас от всех.

### Gaps
- Не проверено, как именно Expedia и Booking визуально цитируют отзывы в Q&A: страница Expedia недоступна.

---

## 9. Наши дифференциаторы (чего не делает никто)

### Takeaway
Лидеры **пересказывают** имеющийся контент. Мы **вычисляем, доказываем и поддерживаем свежим** то, чего в контенте нет, на уровне номера, фасада и этажа, с честными ограничениями и замкнутой петлёй с отельером.

### Cited Findings
- Анализ рынка выше: ни Booking, ни Expedia, ни Airbnb, ни Google AI Mode, ни Яндекс не описывают вычисляемые атрибуты и provenance по факту — [Booking.com](https://news.booking.com/bookingcom-enhances-travel-planning-with-new-ai-powered-features--for-easier-smarter-decisions/); [RentalScaleUp](https://www.rentalscaleup.com/airbnb-natural-language-search/); [TechCrunch](https://techcrunch.com/2026/08/27/googles-ai-mode-can-now-track-flight-prices-help-book-hotels-and-more/); [AdIndex](https://adindex.ru/news/digital/2025/10/21/338296.phtml)
- Нишевые продукты (HowLoud, Shadowmap, Walk Score) работают по адресу или зданию, по одному атрибуту и в B2B для недвижимости — [Shadowmap API](https://shadowmap.org/api); [Built In LA](https://www.builtinla.com/articles/know-your-noise-level-howloud-and-factual-create-soundscore-homes)
- Генеративные сводки без доказательной базы смягчают негатив (Which? против Tripadvisor) — [Euronews](https://euronews.com/travel/2026/07/03/tripadvisor-ai-summaries-give-glowing-reviews-to-dangerous-hotels-consumer-watchdog-finds)
- LLM-агенты плохо держат несколько жёстких ограничений (TravelPlanner, 0.6% у GPT-4) — [arXiv](https://arxiv.org/abs/2402.01622v2)

### Inferences (наши дифференциаторы)
1. **Вычисленные, а не угаданные атрибуты на уровне фасада, этажа и номера.** Line-of-sight по DEM и высотам OSM («закат из окон западной стороны, с 5-го этажа, апрель–август»), шум по фасадам, пеший маршрут с перепадом высот и переходом через трассу. Ни у кого из лидеров этого нет.
2. **Карточка доказательства (evidence card) по каждому факту**: источник (геоданные, отзыв, отельер, Wikidata), метод, уверенность, дата получения, срок годности (TTL) и кнопка «показать расчёт» (карта или луч обзора). У Airbnb в лучшем случае один общий ярлык.
3. **Обнаружение и разбор противоречий.** «Отзывы говорят шумно, геоданные — тихо, отельер — шумоизоляция» превращается в итог с уверенностью и объяснением. Ни один продукт этого не показывает.
4. **Честные частичные совпадения.** «Вид на закат есть, но только с 5-го этажа», «тихо, кроме пятницы и субботы из-за бара в 150 м». Это антипод «отбеливающих» сводок Tripadvisor.
5. **Активная петля с отельером.** Агент сам решает, о чём спросить, выбирая атрибуты с низкой уверенностью и высокой ценностью для поиска. Свободный ответ раскладывается по схеме, отельер подтверждает или правит. Принцип «ответил один раз — работает для всех гостей». Expedia Content Agent и Booking Auto-Reply делают только части этой петли и без структурированной уверенности. У Островка уже есть канал автоматического обзвона отелей (голосовой бот), так что петля ложится на реальный процесс — [Generation AI](https://generation-ai.ru/cases/ostrovok-voice-bot-hotels)
6. **Свежесть как функция.** У каждого факта есть TTL, переоценка идёт по триггерам (новое здание в OSM, разрешение на стройку, перекрытие улицы), а в UI видно «проверено N дней назад». Аналогов не найдено.
7. **Расширяемость текстом.** Новый атрибут описывается на естественном языке, агент сам планирует источники и инструменты. У лидеров атрибуты — фиксированные списки удобств.
8. **Детерминированная проверка жёстких ограничений плюс LLM только для разбора запроса и объяснения.** Это прямой ответ на провалы агентов в TravelPlanner, и его легко защитить перед инженерным жюри.
9. **Собственные метрики качества**: размеченная выборка, точность и покрытие по каждому атрибуту, доля вопросов отельеру, калибровка уверенности. Публичных метрик точности AI-ответов нет ни у одного конкурента.
10. **Совместимость с моделью данных Островка.** Атрибуты привязаны к «метаотелю» и «метакомнате», которые Островок уже строит ([Habr](https://habr.com/ru/companies/ostrovok/articles/831126/)). Это продолжение их работы с «подтверждёнными параметрами номера», а не чуждая система.
11. **Отличие от главного российского конкурента.** Яндекс генерирует описания из данных отельера, а мы вычисляем и проверяем факты. При возможной смене владельца Островка это особенно ценный актив данных. Сделка не подтверждена, на питче упоминать аккуратно.

### Gaps
- Нет количественных доказательств связи между verified-атрибутами и конверсией или отменами. В питче это нужно подавать как гипотезу или метрику для A/B, а не как факт.
