# Каталог открытых источников данных и API для неочевидных характеристик отелей (состояние на 26.10.2026)

> Методика. Источники: официальная документация провайдеров и реестры. Часть фактов я проверил сам 26.10.2026 запросами к живым эндпоинтам с ноутбука (сеть вне РФ). Такие факты помечены **[проба 26.10.2026]**, ссылка ведёт на сам эндпоинт. Для сравнения взяты два bbox: «центр Сочи» `39.65,43.55,39.80,43.65` и «Ницца» `7.20,43.65,7.32,43.73`. Названия тегов, библиотек и полей оставлены на английском.

---

## 1. Список отелей: OSM, Overture Places, Wikidata или GeoNames

### Takeaway
Для мастер-списка лучше всего брать **OSM (`tourism=hotel|guest_house|hostel|apartment`) и объединять его с Overture Places**. В OSM хорошо заполнены `stars`, `website` и геометрия здания. Overture даёт в 1,7–2,8 раза больше объектов и чаще содержит сайт, но не содержит звёзд и строится на данных Meta. Wikidata и GeoNames как список отелей бесполезны: в Wikidata слишком мало отелей, у GeoNames нет атрибутов. Wikidata пригодится для «истории здания».

### Cited Findings
- **Масштаб OSM по миру:** `tourism=hotel` встречается 455 326 раз, `guest_house` 200 960, `apartment` 91 903, `hostel` 62 859. Ключ `stars` есть лишь у 62 388 объектов, `website` у 4,8 млн, `contact:website` у 0,88 млн — [taginfo tourism=hotel](https://taginfo.openstreetmap.org/tags/tourism=hotel), [taginfo key stars](https://taginfo.openstreetmap.org/keys/stars) **[проба 26.10.2026 через taginfo API]**.
- **OSM, центр Сочи:** 125 объектов `tourism=hotel`. Из них name есть у 117, website у 53, stars у 36, phone у 41, building:levels у 34, wikidata у 3, rooms у 3 — [Overpass API](https://overpass-api.de/api/interpreter) **[проба 26.10.2026]**.
- **OSM, Ницца:** 193 объекта `tourism=hotel`. name у 192, website у 125, stars у 115, wikidata у 40, building:levels у 13 — [overpass.private.coffee](https://overpass.private.coffee/api/interpreter) **[проба 26.10.2026]**.
- **Overture Places, центр Сочи** (релиз 2026-10-21.0): `basic_category='hotel'` 212 (сайт у 158), `lodging` 43, `resort` 9. Источники для hotel: meta 201, Foursquare 10, Microsoft 1 — [Overture S3 places](https://docs.overturemaps.org/guides/places/) **[проба 26.10.2026, DuckDB по S3]**.
- **Overture Places, Ницца:** hotel 536 (сайт у 341), private_lodging 402, lodging 150, resort 5. Источники для hotel: meta 317, BrightQuery 192, Foursquare 19, AllThePlaces 6, Uberall 2 — **[проба 26.10.2026]**.
- **Состав Overture Places:** поля `names`, `basic_category`, `taxonomy`, `confidence`, `websites`, `socials`, `emails`, `phones`, `brand`, `addresses`, `sources`. Примерно 60 из 88 млн записей пришли от Meta, около 16 млн от BrightQuery. Данных OSM в теме нет — [Overture Places guide](https://docs.overturemaps.org/guides/places/).
- **Breaking changes в Overture:** в декабре 2025 появилось свойство `taxonomy`. **В сентябре 2026 удалено `categories`**, в октябре 2026 добавлено `basic_category` (около 280 меток). Старый код с `categories.primary` больше не работает — [Overture Places guide](https://docs.overturemaps.org/guides/places/).
- **Лицензия Overture Places:** CDLA Permissive 2.0 / Apache 2.0 в зависимости от источника, AllThePlaces под CC0. При объединении с OSM результат может подпадать под ODbL как производная БД — [Overture Places guide](https://docs.overturemaps.org/guides/places/).
- **Выгрузка Overture по bbox:** `overturemaps download --bbox=12.46,41.89,12.48,41.91 -f geojson --type=place -o rome.geojson`. Путь на S3: `s3://overturemaps-us-west-2/release/2026-10-21.0/theme=places/type=place/*`. Для поиска последнего релиза рекомендуют STAC-каталог — [Overture Places guide](https://docs.overturemaps.org/guides/places/). Пакет `overturemaps` 1.0.2 вышел 2026-08-25 — [PyPI overturemaps](https://pypi.org/project/overturemaps/) **[проба PyPI 26.10.2026]**.
- **Скорость Overture:** запрос DuckDB 2.0 к S3 по bbox одного города отработал за 1–11 с для places и за 1,5–22 с для buildings, без ключей и без AWS-аккаунта **[проба 26.10.2026]**.
- **Wikidata:** в радиусе 10 км от центра Сочи нашлось всего 5 элементов-отелей (`wdt:P31/wdt:P279* wd:Q27686`) — [WDQS](https://query.wikidata.org/sparql) **[проба 26.10.2026]**.
- **GeoNames:** бесплатный веб-сервис требует параметр `username`. Лимиты 10 000 кредитов в день и 1 000 в час. Лицензия CC BY, нужна ссылка на GeoNames — [GeoNames export](https://geonames.org/export/). Сторонние сайты называют 20–30 тыс. в день, официальная страница говорит о 10 000 — [nodepit](https://nodepit.com/node/ws.palladian.nodes.extraction.location2.source.geonames.GeonamesLocationSourceNodeFactory).

### Inferences
- Рабочий рецепт: взять OSM как «скелет» (здесь есть геометрия здания, `stars`, `building:levels`, `wikidata`). Затем подтянуть Overture: матчинг по расстоянию меньше 50–100 м плюс fuzzy-сравнение имени. Из Overture брать сайт, телефон и `confidence`. Расхождения между OSM и Overture (имя, сайт) — готовый кейс для требования «система замечает противоречия источников».
- В Сочи `stars` есть примерно у 29% OSM-отелей, в Ницце примерно у 60%. Звёздность — хороший кандидат на вопрос отельеру или на «тег с низкой уверенностью».
- В Overture есть `private_lodging` (402 объекта в Ницце). Это квартиры с краткосрочной арендой; при фильтрации «отелей» их, скорее всего, стоит исключить.

### Gaps
- Как полнота Overture и OSM для Москвы, Санкт-Петербурга и Калининграда соотносится с данными по Сочи, не проверено. Цифры есть только для центра Сочи и для Ниццы.
- Доля ложных и закрытых объектов в Overture (у Meta-источника) не измерялась.
- Наличие у GeoNames feature code `HTL` (отели) и его полноту я не проверил.

---

## 2. Доступ к OSM: Overpass, экстракты Geofabrik/BBBike, osmium/pyrosm/osmnx/QuackOSM, береговая линия

### Takeaway
**Публичный Overpass не годится для пакетного сбора.** 26.10.2026 все три инстанса отвечали с ошибками: 502 «at capacity», 504, таймаут. Для хакатона правильно скачать PBF региона с Geofabrik (Калининград занимает 27,9 МБ, Южный ФО 301 МБ), разобрать его локально через QuackOSM/pyrosm/osmium и закоммитить готовый GeoParquet.

### Cited Findings
- **overpass-api.de (FOSSGIS):** «light use» — до 10 000 запросов и до 1 ГБ в сутки. Параллельные скрипты запрещены. После 429/406 нужно ждать 30 с. Обязательны User-Agent или Referer. Таймаут по умолчанию 3 мин. Коммерческое использование — только на своём или платном сервере — [OSM Wiki Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API).
- **Альтернативные инстансы:** VK Maps `https://maps.mail.ru/osm/tools/overpass/api/interpreter` («no requests limitations»). Private.coffee (бывший kumi.systems) `https://overpass.private.coffee/api/interpreter` («no rate limit»). NextGIS работает с ключом. У Overpass.ch бесплатный тариф 10k запросов в месяц и 30 в минуту — [OSM Wiki Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API).
- **Фактическая доступность 26.10.2026:** overpass-api.de вернул `502` с текстом «This Overpass API server is currently at capacity… This public endpoint is intended for browser-based and human-operated tools. For automated or high-volume use, run a local Overpass API instance». VK Maps выдал `504 Gateway Time-out` на запрос около 60 с. private.coffee один раз ответил успешно (Ницца), на втором запросе завис — **[проба 26.10.2026]**.
- **Geofabrik, Россия:** russia-latest.osm.pbf 3,9 ГБ, Central FD 841 МБ, Northwestern FD 625 МБ, South FD 301 МБ, Kaliningrad 27,9 МБ. Выгрузка обновляется ежедневно (данные до 2026-10-25), есть .osc.gz-диффы. Крым входит и в выгрузку России, и в выгрузку Украины, отдельно Crimean FD весит 38,8 МБ — [Geofabrik Russia](https://download.geofabrik.de/russia.html).
- **QuackOSM:** читает PBF через DuckDB и пишет GeoParquet. Умеет фильтровать по геометрии и тегам (wildcards и отрицания). Сам скачивает нужные экстракты (Geofabrik, BBBike, OSMfr), в том числе по текстовому геокоду (`quackosm --geom-filter-geocode "Shibuya, Tokyo"`). Лицензия Apache-2.0 — [GitHub kraina-ai/quackosm](https://github.com/kraina-ai/quackosm). Последняя версия 0.19.0 от 2026-09-28 — [PyPI QuackOSM](https://pypi.org/project/QuackOSM/) **[проба PyPI]**.
- **pyrosm:** на PyPI версия 0.25.0 от 2026-10-25 — [PyPI pyrosm](https://pypi.org/project/pyrosm/) **[проба PyPI]**. Changelog на readthedocs упоминает v0.10.0/0.11.0 (июнь–июль 2026) с out-of-core движком `OSM(filepath, engine="out_of_core")` — [pyrosm changelog](https://pyrosm.readthedocs.io/en/latest/changelog.html). Нумерация версий в двух источниках расходится, но проект явно жив.
- **Прочие библиотеки:** osmnx 2.1.1 (2026-07-21, MIT), osmium (pyosmium) 4.3.2 (2026-10-18), overpy 0.7 (2023, не обновляется) — PyPI [osmnx](https://pypi.org/project/osmnx/), [osmium](https://pypi.org/project/osmium/), [overpy](https://pypi.org/project/overpy/) **[проба PyPI]**.
- **Береговая линия и полигоны воды:** osmdata.openstreetmap.de раздаёт water polygons (моря и океаны из `natural=coastline`) в виде Shapefile в WGS84 (split) и Mercator (split и simplified). Внутренние водоёмы (`natural=water`) туда не входят. Лицензия ODbL — [osmdata water polygons](https://osmdata.openstreetmap.de/data/water-polygons.html). В OSM 1,29 млн путей `natural=coastline` — [taginfo](https://taginfo.openstreetmap.org/tags/natural=coastline) **[проба]**.

### Inferences
- Пайплайн для демо: Geofabrik PBF региона → QuackOSM с tag-filter (`tourism`, `amenity`, `leisure`, `highway`, `railway`, `building`, `opening_hours` и т.д.) → GeoParquet в репозиторий (единицы–десятки МБ на город). Overpass подходит только для точечных интерактивных запросов агента, и то с fallback на локальные данные.
- Сочи (Краснодарский край) должен быть в выгрузке Южного ФО, но Geofabrik на странице этого прямо не пишет. Для одного города удобнее вырезать bbox через `osmium extract -b` или геометрический фильтр QuackOSM.

### Gaps
- Размеры городских экстрактов BBBike и их наличие для Сочи и Калининграда я не проверил.
- Размер файла osmdata water polygons и частота обновления на странице не указаны.

---

## 3. Footprints зданий и высоты: OSM, Overture, Microsoft, Google, GHSL

### Takeaway
**Overture Buildings, по сути, это OSM плюс Microsoft ML.** В Ницце высота (`height`) есть у 69% зданий: это французские OSM-импорты плюс высоты от MS. В Сочи высота есть всего у 62 из 40 тыс. зданий, но этажность (`num_floors`, из `building:levels`) заполнена у 40%. Для Сочи и других городов РФ высоту здания придётся считать как этажи × 3 м, а там, где этажности нет, брать DSM минус DTM или GHSL (100 м). Google Open Buildings не покрывает ни Европу, ни Россию.

### Cited Findings
- **Overture Buildings, состав:** источники — OSM (наивысший приоритет), Esri Community Maps, IGN Spain, City of Vancouver, Microsoft и Google Open Buildings. Типы `building` и `building_part`, есть поле `height`. Лицензия ODbL. Релизы ежемесячные, последний 2026-10-21.0 — [Overture Buildings guide](https://docs.overturemaps.org/guides/buildings/).
- **Центр Сочи:** 39 986 зданий (OSM 31 282, Microsoft ML 8 704). `height` у 62 (все из OSM), `num_floors` у 15 988 (все из OSM). У зданий от MS высоты нет совсем **[проба 26.10.2026]**.
- **Ницца:** 46 997 зданий (OSM 45 596, MS 1 401). `height` у 32 290 (OSM 30 895, MS 1 395), `num_floors` у 2 222 **[проба 26.10.2026]**.
- **OSM по миру:** `building:levels` у 43,8 млн объектов, `height` у 28,0 млн — [taginfo building:levels](https://taginfo.openstreetmap.org/keys/building:levels), [taginfo height](https://taginfo.openstreetmap.org/keys/height) **[проба]**.
- **Microsoft Global ML Building Footprints:** около 1,4 млрд зданий по снимкам Bing 2014–2024. Высоты добавлялись для США и Западной Европы (май 2023), Северной Америки (март 2023) и ряда других регионов, отсутствие высоты обозначено `-1`. Формат — GeoJSONL по стране и quadkey (у файлов расширение `.csv.gz`, хотя внутри GeoJSONL). Ссылки лежат в `dataset-links.csv`, URL которого сменился в июле 2026. **Лицензия CDLA Permissive 2.0, а не ODbL.** Последнее обновление 13.08.2026. Recall от ~71% (Африка) до ~86% (Европа), в плотной застройке качество хуже — [GitHub GlobalMLBuildingFootprints](https://github.com/microsoft/GlobalMLBuildingFootprints).
- **Google Open Buildings v3:** охватывает Африку, Южную и Юго-Восточную Азию, Латинскую Америку и Карибы. **Европы и России в списке стран нет.** Лицензия CC BY 4.0 или ODbL на выбор. Доступ через GCS `gs://open-buildings-data/v3/...` (178 ГБ полигонов) и Earth Engine — [Google Open Buildings](https://sites.research.google/gr/open-buildings/).
- **GHSL GHS-BUILT-H R2023A:** глобальный растр 100 м со средней высотой зданий в ячейке на 2018 год. Построен по AW3D30, SRTM30 и Sentinel-2. Свободное переиспользование с указанием источника, DOI 10.2905/85005901-3A49-48DD-9D19-6261354F56FE. В EE: `JRC/GHSL/P2023A/GHS_BUILT_H` — [Earth Engine catalog](https://developers.google.com/earth-engine/datasets/catalog/JRC_GHSL_P2023A_GHS_BUILT_H).

### Inferences
- Если выбрать регионом Ниццу, расчёт видимости моря или заката получается заметно достовернее, потому что реальные высоты есть у большинства зданий. В Сочи придётся работать с этажами и ставить уверенность «средняя».
- GHSL с разрешением 100 м не годится для перекрытия вида соседним домом. Он полезен как fallback, чтобы отличить «вокруг многоэтажки» от «вокруг частный сектор».
- При конфликте `height` и `building:levels × 3 м` сработает механизм «противоречие источников → вопрос отельеру: на каком этаже номер и сколько этажей в здании».

### Gaps
- Прямой загрузки GHS-BUILT-H с сайта JRC (без Earth Engine) я не нашёл, только EE.
- Открытые 3D-модели городов (CityGML/LoD2) для Ниццы, Барселоны, Лиссабона и российских городов не исследованы.
- Охват `height` в Overture для Москвы и Санкт-Петербурга не проверен.

---

## 4. DEM/DSM: Copernicus, SRTM, NASADEM, ALOS, FABDEM, OpenTopography, Terrain Tiles, Open-Meteo

### Takeaway
**Основной источник — Copernicus GLO-30 с AWS S3: без ключа, COG, 12–30 МБ на тайл 1°×1°.** Тайлы для Сочи и Калининграда существуют. Это DSM: он уже включает здания и лес, что для видимости одновременно и плюс, и минус. FABDEM (DTM без зданий и леса) разрешён только для некоммерческого использования. OpenTopography требует ключ и даёт всего 50 вызовов в сутки неакадемическим пользователям.

### Cited Findings
- **Copernicus DEM на AWS:** бакеты `copernicus-dem-30m` и `copernicus-dem-90m` в `eu-central-1`. Работает `--no-sign-request`, AWS-аккаунт не нужен. Формат COG. Это **DSM** (включает здания, инфраструктуру, растительность). У GLO-30 Public неполное покрытие для части стран (список в отдельной таблице). Над океаном тайлов нет, высота там равна 0 — [AWS Registry Copernicus DEM](https://registry.opendata.aws/copernicus-dem/).
- **Тайлы для РФ существуют:** `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N43_00_E039_00_DEM/Copernicus_DSM_COG_10_N43_00_E039_00_DEM.tif` (Сочи) отдаёт HTTP 200, 11,9 МБ. `..._N54_00_E020_00_DEM.tif` (Калининград) отдаёт HTTP 200, 30,0 МБ **[проба 26.10.2026]**.
- **OpenTopography Global DEM API:** **ключ обязателен** (бесплатный, из My Account). Лимит в сутки: 200 вызовов для академических пользователей, **50 для неакадемических**. Максимальная площадь для 30-метровых наборов 450 000 км². Наборы: SRTM GL1/GL3, NASADEM, AW3D30, COP30, COP90, Continental Europe DTM 30m, GEDI L3, GEBCO и др. Параметры описаны в Swagger на portal.opentopography.org/apidocs — [OpenTopography developers](https://opentopography.org/developers).
- **AWS Terrain Tiles (Mapzen/Tilezen):** бакет `elevation-tiles-prod` (us-east-1), зеркало `elevation-tiles-prod-eu`, без аккаунта. Атрибуция по [tilezen/joerd attribution.md](https://github.com/tilezen/joerd/blob/master/docs/attribution.md). Дата последнего обновления не указана, данные «добавляются по отзывам сообщества» — [AWS Registry Terrain Tiles](https://registry.opendata.aws/terrain-tiles/).
- **Open-Meteo Elevation API:** `https://api.open-meteo.com/v1/elevation?latitude=52.52,48.85&longitude=13.41,2.35`. Под капотом Copernicus **GLO-90** (90 м), до 100 координат за запрос — [Open-Meteo Elevation API](https://open-meteo.com/en/docs/elevation-api).
- **FABDEM** (Copernicus без зданий и леса): лицензия **CC BY-NC-SA 4.0**, при использовании нужны также атрибуции Copernicus — [GEE community catalog FABDEM](https://gee-community-catalog.org/projects/fabdem/). На странице репозитория Bristol указана другая лицензия, «Non-Commercial Government Licence», тайлы 1×1° в zip-архивах 10×10°, версия V1-0 — [data.bris.ac.uk](https://data.bris.ac.uk/data/dataset/25wfy0f9ukoge2gs7a5mqpq2j7). Источники расходятся в формулировке, но некоммерческое ограничение есть в обоих.

### Inferences
- Для вида на море или закат: DTM-рельеф (FABDEM или Copernicus) плюс собственная «надстройка» из OSM-зданий с высотами дают лучшую модель, чем голый DSM, где деревья вдоль набережной могут «закрыть» море. Для прототипа хакатона (некоммерческое использование) FABDEM юридически допустим, но лицензию лучше указать в README.
- Open-Meteo Elevation на 90 м годится только для грубой высоты над уровнем моря (например, «отель на холме, +80 м»), но не для расчёта видимости.
- Тайл 1° весит 12–30 МБ, поэтому обрезанный COG одного города (несколько МБ) можно спокойно коммитить.

### Gaps
- Условия доступа к ALOS AW3D30 напрямую у JAXA (регистрация) и к NASADEM/SRTM через NASA Earthdata (login) я не проверил. Через OpenTopography они доступны с ключом.
- Нет ли в GLO-30 Public пропущенных тайлов на территории РФ: Сочи и Калининград есть, полный список исключений не смотрел.
- Форматы Terrain Tiles (terrarium/normal/geotiff/skadi) и разрешение на странице реестра не описаны.

---

## 5. Шум: карты EEA END и что делать с Россией

### Takeaway
**Для ЕС есть стратегические карты шума END (Round 4 = 2022, Lden/Lnight, изолинии с шагом 5 дБ по агломерациям больше 100 тыс. жителей), но публикуются они в основном национальными и городскими порталами и INSPIRE-сервисами, а не одним файлом EEA.** У EEA централизованно лежат только табличные данные экспозиции. **Для России открытых карт шума я не нашёл**, шум придётся моделировать по OSM: классы дорог, ж/д, бары и клубы, аэропорт.

### Cited Findings
- **Спецификация отчётности END:** DF4_8 strategic noise maps, шаблон GeoPackage и таблица INSPIRE download services (версия July 2022) — [EEA reporting guidelines DF4_8](https://epanet.eea.europa.eu/Eionet/reportnet/docs/noise/archive/reportingguidelines_df4_8_strategicnoisemaps_july2022.pdf).
- **Round 2022:** изолинии шума от дорог и ж/д внутри агломераций END (больше 100 тыс. жителей) поставлялись до 18.11.2024. Изолинии государства передают добровольно — [ETC HE report 2025/16](https://www.eionet.europa.eu/etcs/etc-he/products/etc-he-products/etc-he-reports/etc-he-report-2025-16-assessment-of-the-length-of-roads-and-railways-inside-end-agglomerations-in-2022/@@download/file/etc-he-report-2025-16-domingues.pdf).
- **У EEA централизованно лежит экспозиция:** число людей в диапазонах Lden 55–59/60–64/65–69/70–74/>75. Временное покрытие 2021, приём до 16.04.2024. Это таблицы, а не геометрия — [EEA data on noise exposure](https://www.eea.europa.eu/data-and-maps/data/data-on-noise-exposure-7).
- **Пример национальной публикации (Ирландия):** Round 4 Road/Rail/Industry Agglomeration Lden, GeoJSON через WFS EPA. Метод CNOSSOS-EU, с раундами 1–3 несопоставим — [data.gov.ie Road Agglomeration Lden](https://data.gov.ie/en_GB/dataset/noise-round-4-road-agglomeration-lden/resource/d03fe15f-ac51-429a-8c4a-b76dce40fa9f), [INSPIRE Geoportal record](https://inspire-geoportal.ec.europa.eu/srv/api/records/IEc0d8c45b-4cb2-4c2f-8bc8-a24e8c1434a2).
- **Прокси-источники шума в OSM:** `amenity=nightclub` 24 376 объектов по миру, `highway=pedestrian` 893 985 — [taginfo nightclub](https://taginfo.openstreetmap.org/tags/amenity=nightclub), [taginfo pedestrian](https://taginfo.openstreetmap.org/tags/highway=pedestrian) **[проба]**.

### Inferences
- Для Ниццы, Барселоны и Лиссабона карты Lden и Lnight нужно искать на национальных геопорталах (Франция, Испания, Португалия) или через INSPIRE Geoportal. Достаточно скачать один раз и закоммитить, получится карта «реального шума» с высокой уверенностью.
- Для Сочи и других городов РФ: эвристическая модель «шум по сторонам фасада». Учитываются расстояние до `highway=motorway|trunk|primary|secondary` с затуханием около 6 дБ на удвоение расстояния, `railway=rail`, `amenity=bar|pub|nightclub` и их `opening_hours`, а также направление ВПП аэропорта. Уверенность «низкая/средняя», дальше вопрос отельеру о шумоизоляции. Карту END из ЕС можно использовать, чтобы откалибровать эвристику.

### Gaps
- Единой прямой ссылки EEA на pan-European GeoPackage изолиний Round 4 я не нашёл (DiscoMap/Reportnet не подтверждены).
- Конкретные порталы с картами шума для Ниццы (Métropole Nice Côte d'Azur), Барселоны (Mapa estratègic de soroll) и Лиссабона не проверены.
- Открытых карт шума для Москвы, Санкт-Петербурга, Сочи и Калининграда не найдено. Это не доказательство их отсутствия, но публичных датасетов я не обнаружил.

---

## 6. Погода, климат и море: Open-Meteo (Historical, Climate, Marine), ERA5

### Takeaway
**Open-Meteo закрывает весь блок «климат по месяцам и вода» без ключа:** архив ERA5 с 1940 года (включая `sunshine_duration`), климатические проекции и **температура моря** (`sea_surface_temperature`, ERA5-Ocean с 1940 года). Проверено для Сочи. Ограничение: бесплатный тариф только некоммерческий, 10 000 вызовов в сутки, данные под CC BY 4.0.

### Cited Findings
- **Лимиты и лицензия Open-Meteo:** до 600 вызовов в минуту, 5 000 в час, 10 000 в сутки, 300 000 в месяц. Лимит считается по IP и приложению. **Коммерческое использование на бесплатном тарифе запрещено.** Данные под CC BY 4.0 — [Open-Meteo Terms](https://open-meteo.com/en/terms).
- **Historical Weather API:** `https://archive-api.open-meteo.com/v1/archive?latitude=..&longitude=..&start_date=..&end_date=..&daily=..&timezone=..`. ERA5 0,25° с 1940 года, ERA5-Land 0,1° с 1950, ECMWF IFS 9 км с 2017, CERRA 5 км (Европа, 1985–06.2021). ERA5 приходит с задержкой 5 дней. Для daily-переменных обязателен `timezone`. Есть daily `sunshine_duration` (по WMO: прямая радиация больше 120 Вт/м²), `daylight_duration`, `precipitation_sum`, температура, ветер — [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api).
- **Marine API:** `https://marine-api.open-meteo.com/v1/marine`, переменная `sea_surface_temperature`. Модели: MeteoFrance SST/MFWAM/SMOC (0,08°, Copernicus Marine), ECMWF WAM, DWD EWAM/GWAM, GFS Wave, ERA5-Ocean (0,5°, **1940 – наст. время**). У побережья точность ограничена — [Open-Meteo Marine API](https://open-meteo.com/en/docs/marine-weather-api).
- **Проверка для Сочи:** `daily=sea_surface_temperature_mean&start_date=2025-10-01&end_date=2025-10-03` вернул 21,5 / 21,5 / 21,7 °C. Точка снапнулась на 43.54N 39.71E — [Marine API пример](https://marine-api.open-meteo.com/v1/marine?latitude=43.58&longitude=39.70&daily=sea_surface_temperature_mean&start_date=2025-10-01&end_date=2025-10-03) **[проба 26.10.2026]**.
- **Climate API:** `https://climate-api.open-meteo.com/v1/climate?...&models=EC_Earth3P_HR&daily=temperature_2m_mean` отвечает для Сочи — [Climate API](https://climate-api.open-meteo.com/v1/climate?latitude=43.58&longitude=39.72&start_date=2025-01-01&end_date=2025-01-02&models=EC_Earth3P_HR&daily=temperature_2m_mean) **[проба 26.10.2026]**.
- **Python-клиент:** `openmeteo-requests` 1.7.5 (2026-01-19) — [PyPI](https://pypi.org/project/openmeteo-requests/) **[проба PyPI]**.

### Inferences
- «Климатическая норма по месяцам» (последние 10–30 лет ERA5, агрегированные по месяцам) — это 1–2 запроса на отель, а на практике один на кластер отелей, потому что ERA5 0,25° даёт одну точку на весь город. Считается заранее и коммитится как JSON. Фраза «в октябре здесь ещё купаются» выводится из SST > 20 °C (для Сочи в начале октября 2025 было 21,5 °C).
- Для демо на хакатоне (без монетизации) бесплатный тариф подходит, атрибуция «Weather data by Open-Meteo.com (CC BY 4.0)» обязательна.

### Gaps
- Точные имена daily-переменных в архиве (например, `temperature_2m_mean`) на странице описаны неполно. Я проверял только `sea_surface_temperature_mean` и `temperature_2m_mean` в Climate API.
- Прямой доступ к ERA5 через Copernicus CDS (cdsapi, ключ, очереди) не исследован, потому что Open-Meteo его закрывает.

---

## 7. Качество воздуха: OpenAQ v3 и Open-Meteo Air Quality (CAMS)

### Takeaway
**OpenAQ v3 требует ключ** (заголовок `X-API-Key`, бесплатно; 60 запросов в минуту, 2 000 в час), без ключа отвечает 401. **Open-Meteo Air Quality (CAMS) работает без ключа и для Сочи, Москвы и Калининграда отдаёт значения домена `cams_europe`.** Это основной источник; OpenAQ — источник «реальных станций» там, где они есть.

### Cited Findings
- **OpenAQ, лимиты:** 60 запросов в минуту и 2 000 в час, лимит привязан к ключу. Заголовки `x-ratelimit-*`, при превышении 429, за повторные нарушения бан. Обязательна атрибуция источника по Terms of Use — [OpenAQ rate limits](https://docs.openaq.org/using-the-api/rate-limits).
- **OpenAQ, ключ:** заголовок `X-API-Key`, регистрация на explore.openaq.org/register — [OpenAQ API key](https://docs.openaq.org/using-the-api/api-key). Без ключа `https://api.openaq.org/v3/locations?coordinates=43.585,39.72&radius=25000` отвечает **HTTP 401** **[проба 26.10.2026]**. Официальный SDK `openaq` 1.2.0 (2026-10-14) — [PyPI openaq](https://pypi.org/project/openaq/) **[проба PyPI]**.
- **Open-Meteo Air Quality:** `https://air-quality-api.open-meteo.com/v1/air-quality?latitude=..&longitude=..&hourly=pm2_5,european_aqi&domains=cams_europe`. Для Сочи вернул PM2.5 ≈ 6 мкг/м³ и EAQI 22, для Москвы PM2.5 22–38 и EAQI 44–58. Для Калининграда ответ тоже пришёл — **[проба 26.10.2026]**. Лимиты и CC BY 4.0 те же, что у всего Open-Meteo — [Open-Meteo Terms](https://open-meteo.com/en/terms).

### Inferences
- CAMS — это модель, а не измерения. В карточке стоит писать «модельная оценка CAMS» и давать уверенность «средняя». Если рядом (меньше 5 км) есть станция OpenAQ, её данные повышают уверенность, а их расхождение с CAMS — ещё один пример конфликта источников.
- Метрика «чистый воздух» — это среднее или медиана EAQI за сезон, а не текущий прогноз. Для этого нужно собрать историю (у CAMS в Open-Meteo есть past_days и архив) и закоммитить агрегат.

### Gaps
- Покрытие станциями OpenAQ в Сочи, Москве, Санкт-Петербурге и Калининграде не проверено: без ключа API недоступен.
- Официальной информации о выводе из эксплуатации OpenAQ v1/v2 в просмотренных документах нет. Проба показывает только, что v3 требует ключ.
- Глубина архива CAMS Europe в Open-Meteo не проверена.

---

## 8. Связь: OpenCellID, Ookla Open Data, Mozilla Location Service

### Takeaway
**Mozilla Location Service закрыт в 2024 году.** OpenCellID требует ключ (CC BY-SA 4.0) и даёт только положения вышек, а не качество связи. **Лучший прокси «пригодно для удалёнки» — Ookla Open Data:** тайлы около 610 м со средней скоростью и задержкой, fixed и mobile, ежеквартально, на S3 без ключа, последний квартал Q3 2026. Но лицензия CC BY-NC-SA 4.0, то есть только некоммерческое использование.

### Cited Findings
- **Закрытие MLS:** 13.03.2024 перестали выдавать новые ключи, 27.03.2024 перестали принимать данные и публиковать выгрузки, 10.04.2024 выгрузки удалены, 12.06.2024 отключены сторонние ключи. Репозиторий ichnaea архивирован 31.07.2024 — [Mozilla Discourse: Retiring the MLS](https://discourse.mozilla.org/t/retiring-the-mozilla-location-service/128693), [GitHub ichnaea #2065](https://github.com/mozilla/ichnaea/issues/2065).
- **OpenCellID API:** `https://opencellid.org/cell/get`, `/cell/getInArea`, `/cell/getInAreaSize`, параметр `key`, `format=json`. Данные под **CC BY-SA 4.0** с обязательной атрибуцией OpenCelliD — [OpenCellID wiki API](https://wiki.opencellid.org/wiki/API). Без ключа `getInArea` возвращает `{"error":"API Key not known: ","code":2}` **[проба 26.10.2026]**. Мейнтейнер — Unwired Labs — [Unwired Labs help](https://help.unwiredlabs.com/support/solutions/articles/36000333519-how-is-opencellid-related-to-unwired-).
- **Ookla Open Data:** тайлы web-mercator z16 (~610,8 × 610,8 м), средние download/upload и latency, fixed и mobile, ежеквартально. Форматы Shapefile и Parquet, геометрия WKT в EPSG:4326. Бакет `s3://ookla-open-data/` (us-west-2), `--no-sign-request`. **Лицензия CC BY-NC-SA 4.0**, есть шаблон цитирования — [AWS Registry Ookla](https://registry.opendata.aws/speedtest-global-performance/).
- **Ookla, свежесть:** в бакете есть `parquet/performance/type=mobile/year=2026/quarter=1..3`, то есть Q3 2026 — [листинг S3](https://ookla-open-data.s3.us-west-2.amazonaws.com/?list-type=2&prefix=parquet/performance/type=mobile/year=2026/&delimiter=/) **[проба 26.10.2026]**.

### Inferences
- Характеристика «связь для удалёнки»: Ookla fixed (скорость интернета, косвенный признак Wi-Fi) плюс Ookla mobile в тайле отеля. OpenCellID добавляет счётчики вышек по `radio` (LTE/NR) в радиусе 500 м. Уверенность средняя: Ookla — это средние по тестам пользователей, в тайлах с малым числом тестов шумно.
- Parquet за квартал фильтруется DuckDB по bbox (quadkey или WKT). Для одного города результат — килобайты, его можно коммитить.

### Gaps
- Лимиты OpenCellID (запросов в сутки, число загрузок полной базы в сутки, ограничения bbox для `getInArea`) на странице API не указаны, и надёжного источника я не нашёл.
- Размер Parquet Ookla за квартал и покрытие РФ (число тестов в тайлах Сочи и Калининграда) не проверены.
- Поля OpenCellID (radio, mcc, mnc, lac, cellid, range, samples) в просмотренном тексте не перечислены.

---

## 9. Уличные фото и изображения: Mapillary, Panoramax, KartaView, Wikimedia Commons, Flickr

### Takeaway
**Wikimedia Commons geosearch работает без ключа и хорошо покрывает и Сочи, и ЕС** (253 файла в радиусе 500 м от центра Сочи). **Mapillary v4 нужен бесплатный client token**, лимиты щедрые, но bbox должен быть меньше 0,01°. **Panoramax хорош во Франции** (Ницца, лицензия etalab-2.0, без ключа), **в Сочи 0 снимков.** KartaView жив, но без нормального API. У Flickr в 2025 году урезали скачивание для бесплатных аккаунтов.

### Cited Findings
- **Mapillary API v4, авторизация:** client token в query `access_token` или в заголовке `Authorization: OAuth <token>` — [Mapillary API docs](https://www.mapillary.com/developer/api-documentation).
- **Mapillary `/images`:** фильтры `bbox`, `captured_at`, `is_pano`, `compass_angle`/`computed_compass_angle`, `thumb_1024_url`. Радиус-поиск до 50 м, до 100 результатов. **bbox меньше 0,01 градуса².** До 2 000 сущностей на запрос — [Mapillary API docs](https://www.mapillary.com/developer/api-documentation).
- **Mapillary, лимиты:** Entity API 60 000 запросов в минуту, Search 10 000 в минуту, тайлы 50 000 в сутки. Векторные тайлы покрытия `https://tiles.mapillary.com/maps/vtp/mly1_public/2/{z}/{x}/{y}` — [Mapillary API docs](https://www.mapillary.com/developer/api-documentation). SDK `mapillary` 1.0.16 (2026-09-08) — [PyPI](https://pypi.org/project/mapillary/) **[проба PyPI]**.
- **Panoramax:** API близок к STAC 1.0.0 с добавочными маршрутами. `GET /api/configuration` отдаёт лицензию инстанса — [Panoramax STAC compatibility](https://docs.panoramax.fr/api/dev/STAC_compatibility).
- **Panoramax, проверка:** `https://api.panoramax.xyz/api/search?bbox=7.25,43.69,7.28,43.71&limit=100` в Ницце вернул 100 снимков (упёрлись в limit), license `etalab-2.0`. Тот же запрос по центру Сочи вернул **0** — [Panoramax search Nice](https://api.panoramax.xyz/api/search?bbox=7.25,43.69,7.28,43.71&limit=100) **[проба 26.10.2026]**.
- **KartaView:** статус Active, владелец Grab — [Wikipedia KartaView](https://en.wikipedia.org/wiki/KartaView). Пакетной выгрузки нет (по OSM wiki на 2020 год) — [OSM Wiki KartaView](https://wiki.openstreetmap.org/wiki/KartaView). Сообщество обсуждает, «жив ли проект» — [OSM community](https://community.openstreetmap.org/t/is-kartaview-dead/2828).
- **Wikimedia Commons geosearch:** `https://commons.wikimedia.org/w/api.php?action=query&list=geosearch&gscoord=43.5815|39.7229&gsradius=500&gsnamespace=6&gslimit=500&format=json` вернул 253 файла (ns=6) **[проба 26.10.2026]**. Параметры: `gsradius` 10–10000 м, `gslimit` до 500 (боты до 5000), есть `gsbbox` — [MediaWiki Extension:GeoData](https://www.mediawiki.org/wiki/Extension:GeoData).
- **Flickr:** с 15.05.2025 бесплатные аккаунты не позволяют скачивать оригиналы и large (больше 1024 px). CC-фото по-прежнему доступны во всех размерах — [Flickr blog](https://blog.flickr.net/en/2025/04/15/service-update-original-large-size-download-limitations-on-free-accounts/). Коммерческий ключ выдаётся по отдельной заявке и может быть платным — [Flickr API ToS](https://www.flickr.com/services/api/tos/). Лимит «3600 запросов в час» встречается только на стороннем сайте с неизвестной датой — [aquoid.com](https://aquoid.com/plugins/photonic/flickr/).

### Inferences
- Для задачи «что видно с улицы» (фасад, балконы, терраса): Mapillary радиус-поиском (50 м) вокруг входа плюс `computed_compass_angle`, чтобы выбрать кадры, смотрящие на фасад, и затем VLM-классификация. Для Ниццы есть второй источник, Panoramax. Для Сочи остаются только Mapillary и Commons.
- В Commons у каждого файла своя лицензия (CC BY-SA, CC0 и др.). Её нужно забирать через `prop=imageinfo&iiprop=extmetadata` и показывать в UI.
- В `curl` символ `|` в `gscoord` нужно URL-кодировать (`%7C`): ru.wikipedia без кодирования вернул ошибку **[проба]**.

### Gaps
- Лицензия изображений Mapillary (обычно называют CC BY-SA 4.0) на странице документации не указана, не подтверждено.
- Покрытие Mapillary в Сочи и Калининграде без токена проверить не удалось.
- Актуальные лимиты и условия бесплатного ключа Flickr API в 2025–2026 официально не подтверждены.

---

## 10. История и семантика: Wikidata SPARQL и Wikipedia geosearch

### Takeaway
**Wikidata и Wikipedia — источник для «истории здания» и достопримечательностей, но не для списка отелей.** Связь через тег `wikidata=Q…` в OSM есть лишь у 3 из 125 отелей в Сочи и у 40 из 193 в Ницце. Остальное придётся искать геопоиском по зданию (`wikibase:around`) или по адресу. WDQS: таймаут 60 с, 5 параллельных запросов на IP, обязателен User-Agent.

### Cited Findings
- **Лимиты WDQS:** жёсткий таймаут 60 с. Бюджет 60 с процессорного времени на 60 с окна на клиента (UA+IP), 30 ошибочных запросов в минуту, 5 параллельных запросов на IP. При превышении 429 с `Retry-After`. Обязателен корректный User-Agent — [WDQS User Manual](https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual).
- **Геосервисы WDQS:** `wikibase:around` (`wikibase:center`, `wikibase:radius` в км, `wikibase:distance`) и `wikibase:box` (`cornerSouthWest`/`cornerNorthEast`) — [WDQS User Manual](https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual).
- **Wikidata, отели в Сочи:** 5 отелей в радиусе 10 км (среди них «Москва (гостиница, Сочи)», «Родина», Hyatt Regency Sochi) — **[проба 26.10.2026]**.
- **ru.wikipedia geosearch:** `list=geosearch&gscoord=43.5815%7C39.7229&gsradius=2000` вернул статьи («Каменный якорь», монументы и т.п.) — **[проба 26.10.2026]**. Параметры описаны в [Extension:GeoData](https://www.mediawiki.org/wiki/Extension:GeoData).
- **OSM:** ключ `wikidata` есть у 4,48 млн объектов — [taginfo wikidata](https://taginfo.openstreetmap.org/keys/wikidata) **[проба]**.

### Inferences
- Шаги для «истории здания». (1) Тег `wikidata` у отеля или у здания-контура в OSM. (2) Иначе `wikibase:around` радиусом 30–50 м вокруг центроида здания, ищем объекты с `P571` (дата основания/постройки), `P149` (архитектурный стиль), `P1435` (статус памятника), `P84` (архитектор). (3) Wikipedia-геопоиск, затем LLM-экстракция из текста статьи с цитатой как источником. Уверенность высокая, только если совпадают и координаты, и название или адрес.
- Тег OSM `start_date` или `building:architecture` у контура здания — дополнительный независимый источник для кросс-проверки.

### Gaps
- Свойства (P571, P149, P1435, P84) я привожу по общему знанию Wikidata, в этом исследовании их не перепроверял.
- Произошло ли разделение графа WDQS на main и scholarly и затрагивает ли оно гео-запросы, на странице User Manual не сказано.

---

## 11. Общественный транспорт (GTFS) и трансфер из аэропорта

### Takeaway
**Ницца и Барселона закрываются официальными GTFS** (Ницца — без ключа, Licence Ouverte 2.0, ежедневная выгрузка автобусов и трамвая; Барселона — TMB с ключом app_id/app_key). **Для российских городов GTFS ненадёжен:** у Санкт-Петербурга есть фид ORGP, но ссылка не ответила из-за рубежа; для Москвы, Сочи и Калининграда официальных открытых GTFS я не нашёл. Каталоги: Mobility Database (6 000+ фидов, API с refresh token) и Transitland (API key).

### Cited Findings
- **Mobility Database:** 6 000+ фидов GTFS, GTFS-RT и GBFS в 99+ странах, ежедневная проверка — [mobilitydatabase.org/about](https://mobilitydatabase.org/about). API требует refresh token — [R-пакет mobdb](https://github.com/cran/mobdb). Без токена `https://api.mobilitydatabase.org/v1/feeds?country_code=RU` отвечает 302 (редирект на авторизацию) **[проба 26.10.2026]**. Старый CSV каталога deprecated после 30.07.2025 — [GitHub mobility-database-catalogs](https://github.com/MobilityData/mobility-database-catalogs).
- **Transitland REST v2:** `https://transit.land/api/v2/rest`, **ключ обязателен** (`apikey`). Месячная квота зависит от плана. Есть «direct access to GTFS files» — [Transitland REST API](https://www.transit.land/documentation/rest-api/).
- **Ницца (Lignes d'Azur):** ежедневная GTFS-выгрузка автобусов и трамвая на transport.data.gouv.fr. Одна из версий: 89 маршрутов, 2 754 остановки — [transport.data.gouv.fr Lignes d'Azur](https://transport.data.gouv.fr/datasets/export-quotidien-au-format-gtfs-du-reseau-de-transport-lignes-d-azur). Лицензия Licence Ouverte 2.0 — [data.gouv.fr](https://data.gouv.fr/en/datasets/export-quotidien-au-format-gtfs-du-reseau-de-transport-lignes-d-azur). API transport.data.gouv.fr отвечает 200 без ключа — [API](https://transport.data.gouv.fr/api/datasets?format=json) **[проба 26.10.2026]**.
- **Барселона (TMB):** официальный фид в Mobility Database (mdb-2359), авторизация по API key, нужна регистрация на developer.tmb.cat (app_id/app_key). Последняя версия покрывает 17.08.2026–27.03.2027 — [Mobility Database TMB](https://mobilitydatabase.org/feeds/gtfs/mdb-2359), [pytmb](https://pypi.org/project/pytmb/).
- **Санкт-Петербург (ORGP):** в 2016 году Transitland добавил фид наземного транспорта СПб на 1 066 маршрутов, без метро — [Transitland news 2016](https://www.transit.land/news/2016/12/23/saint-petersburg). Адрес `https://transport.orgp.spb.ru/Portal/transport/internalapi/gtfs/feed.zip` известен из сторонней библиотеки — [GitHub borapop/ogrp](https://github.com/borapop/ogrp). HTTP редиректит на HTTPS, а **HTTPS не ответил за 60 с** из нашей сети **[проба 26.10.2026]**. Возможна гео-блокировка, это не подтверждено.
- **Python-инструменты:** gtfs-kit 13.0.1 (2026-08-30, MIT), partridge 1.1.2 (2023), r5py 1.1.7 (2026-06-29, обёртка над R5 для мультимодальной маршрутизации с GTFS) — PyPI [gtfs-kit](https://pypi.org/project/gtfs-kit/), [r5py](https://pypi.org/project/r5py/) **[проба PyPI]**.

### Inferences
- «Дорога из аэропорта»: r5py (GTFS + OSM PBF) считает время в пути, число пересадок и наличие ночных рейсов. Ночные рейсы определяются по `stop_times` после 00:00 и по `calendar`. Для Ниццы это делается полностью на открытых данных, судя по тому, что фид включает трамвай.
- Для Сочи без GTFS остаётся fallback: OSM-маршруты `route=bus|train` (без расписаний) плюс вопрос отельеру «есть ли трансфер, сколько стоит». Можно и вручную собрать мини-GTFS для одного маршрута аэропорт–центр, но это ручная работа, и её нужно честно пометить в данных.
- r5py требует Java (JDK) в Docker-образе, и это стоит учитывать при сборке за ≤10 мин. Альтернатива — заранее посчитать маршруты и закоммитить результат.

### Gaps
- Официальных открытых GTFS для Москвы, Сочи (в том числе «Ласточка» и автобусы аэропорта AER) и Калининграда я не нашёл. Каталог Mobility Database по `country_code=RU` без токена проверить не удалось.
- Наличие маршрута в аэропорт NCE в фиде Lignes d'Azur не проверял по `routes.txt`.
- Лиссабон (Carris/Metro GTFS) не исследован.

---

## 12. Маршрутизация: OSRM, Valhalla, GraphHopper, ORS или локальный osmnx/pandana

### Takeaway
**Для «реальной пешей доступности» правильнее локально посчитать граф (osmnx или pyrosm) и заранее закоммитить результаты.** Публичный демо-сервер OSRM на любой профиль отвечает **автомобильным** маршрутом. Пеший OSRM есть у FOSSGIS (`routing.openstreetmap.de/routed-foot`). GraphHopper бесплатно даёт 500 кредитов в сутки, только для некоммерческого использования. **pandana теперь под AGPL-3.0.**

### Cited Findings
- **Gotcha OSRM demo:** `https://router.project-osrm.org/route/v1/foot/39.7229,43.5815;39.7300,43.5800` и `/driving/` вернули одинаковый маршрут: 4 208,6 м, 358 с. Профиль игнорируется, по факту это car — **[проба 26.10.2026]**.
- **FOSSGIS OSRM foot:** `https://routing.openstreetmap.de/routed-foot/route/v1/foot/39.7229,43.5815;39.7300,43.5800` вернул 1 299,4 м и 1 039 с, то есть реальный пеший маршрут — **[проба 26.10.2026]**.
- **GraphHopper Free:** 500 кредитов в сутки, до 5 точек на запрос, только некоммерческое использование. После исчерпания запросы жёстко блокируются. Routing на 2–10 точек стоит 1 кредит — [GraphHopper pricing](https://www.graphhopper.com/pricing/), [What is one credit](https://support.graphhopper.com/support/solutions/articles/44000718211-what-is-one-credit-).
- **Локальные библиотеки:** osmnx 2.1.1 (MIT). **pandana 0.8 (2026-08-24) под AGPL-3.0-or-later.** r5py 1.1.7 — PyPI [osmnx](https://pypi.org/project/osmnx/), [pandana](https://pypi.org/project/pandana/), [r5py](https://pypi.org/project/r5py/) **[проба PyPI]**.

### Inferences
- «Реальная доступность с перепадом высот»: граф osmnx (`network_type="walk"`), к которому через rasterio добавлены высоты узлов из Copernicus или FABDEM (в osmnx есть `elevation.add_node_elevations_raster`). Вес ребра считается по формуле Тоблера. Флаг «переход через оживлённую трассу» ставится, если маршрут пересекает `highway=primary|trunk` вне `crossing=traffic_signals`. Всё считается офлайн для N отелей × M целей (пляж, метро, центр) и коммитится.
- AGPL у pandana для хакатонного прототипа некритична, но её лучше заменить на `networkx` или `scipy.sparse.csgraph`, которых хватает для одного города.

### Gaps
- Официальная страница лимитов OpenRouteService (`/restrictions/`, `/plans/`) вернула 404. По памяти: нужен ключ, около 2 000 directions в сутки и 40 в минуту, но **это не подтверждено**.
- Публичный демо-сервер Valhalla (valhalla1.openstreetmap.de) и его usage policy не проверены.
- Usage policy FOSSGIS routing (допустимый объём) не проверена. Для пакетного счёта лучше локальный граф.

---

## 13. POI-дополнения: opening_hours, зарядки EV, детские площадки и аптеки, качество воды на пляжах, световое загрязнение, городские порталы (стройки, перекрытия, события)

### Takeaway
Для «что работает ночью» основной инструмент — OSM с `opening_hours`, парсер **opening-hours-py** (Rust-биндинг, поддерживается, 2.1.4). Для EV — OSM `amenity=charging_station` плюс OpenChargeMap (ключ обязателен). Для пляжей ЕС — сервис EEA Bathing Water 2025 (ArcGIS REST). Для светового загрязнения — VIIRS VNL v2.2 (бесплатная регистрация в EOG). **Стройки и перекрытия из городских порталов реально доступны в Париже (CSV/JSON/SHP, еженедельно).** Для Москвы и других городов РФ официальных подтверждённых API я не нашёл.

### Cited Findings
- **opening_hours в OSM:** `opening_hours=24/7` у 764 068 объектов. `amenity=pharmacy` 443 417, `leisure=playground` 1 017 515, `amenity=charging_station` 208 417, `amenity=parking` 6,9 млн, `tourism=viewpoint` 265 348 — taginfo: [24/7](https://taginfo.openstreetmap.org/tags/opening_hours=24%2F7), [pharmacy](https://taginfo.openstreetmap.org/tags/amenity=pharmacy), [playground](https://taginfo.openstreetmap.org/tags/leisure=playground), [charging_station](https://taginfo.openstreetmap.org/tags/amenity=charging_station), [viewpoint](https://taginfo.openstreetmap.org/tags/tourism=viewpoint) **[проба]**.
- **opening-hours-py:** парсер OSM opening_hours на Rust. Объект `OpeningHours` умеет состояние «сейчас» и следующее изменение, таймзоны, sun events и встроенные праздники — [PyPI opening-hours-py](https://pypi.org/project/opening-hours-py). Версия 2.1.4 (2026-07-07, MIT OR Apache-2.0) **[проба PyPI]**. **humanized_opening_hours заброшен:** последний релиз 0.6.2 в 2018 году, разработка «paused» — [PyPI osm-humanized-opening-hours](https://pypi.org/project/osm-humanized-opening-hours/).
- **OpenChargeMap:** `/v3/poi`, ключ обязателен (`key` в query или `X-API-Key`) — [MS connector docs](https://learn.microsoft.com/pl-pl/connectors/openchargemapip), [OCM community: API keys must be real](https://community.openchargemap.org/t/note-api-keys-must-be-real-api-keys/811). Сервис позиционирует себя как non-commercial, non-profit — [openchargemap.org](https://openchargemap.org/site/develop/api).
- **EEA Bathing Water 2025:** ArcGIS MapServer `BathingWater_Dyna_WM_2025` с классом качества (Excellent/Good/Sufficient/Poor/Not classified), координатами и историей 2015–2025 — [water.discomap.eea.europa.eu](https://water.discomap.eea.europa.eu/arcgis/rest/services/BathingWater/BathingWater_Dyna_WM_2025/MapServer). На EMODnet есть загружаемый набор с координатами, но с покрытием 1990–2023 — [EMODnet catalogue](https://emodnet.ec.europa.eu/geonetwork/srv/api/records/8d85660c-7930-40dc-8733-96abdcf4bca7).
- **VIIRS VNL:** EOG (Colorado School of Mines), последняя версия VNL v2.2 — [EOG VNL](https://eogdata.mines.edu/products/vnl/). Для доступа нужна бесплатная регистрация — [Payne Institute](https://payneinstitute.mines.edu/?p=3162). В Earth Engine есть `NOAA/VIIRS/DNB/ANNUAL_V22` за 2012–2024 — [EE catalog](https://developers.google.com/earth-engine/datasets/catalog/NOAA_VIIRS_DNB_ANNUAL_V22). Ежемесячные композиты с ранним доступом платные — [Payne Institute](https://payneinstitute.mines.edu/?p=3162).
- **Париж, стройки, мешающие движению:** CSV/JSON/SHP, обновление еженедельное (с 12.2016), Lambert 93. Поля `NIVEAU_PERTURBATION` и `STATUT` (1 — à venir … 5 — terminé), датасет неисчерпывающий — [data.gouv.fr Paris chantiers perturbants](https://www.data.gouv.fr/datasets/paris-chantiers-perturbants-la-circulation).
- **data.mos.ru:** есть Python-обёртка `opengovpy`, в которой ключ необязателен. Официальной документации по ключам и лимитам я не нашёл — [PyPI opengovpy](https://pypi.org/project/opengovpy/0.11).

### Inferences
- «Ночью рядом работают»: из OSM берём `amenity=pharmacy|cafe|fast_food|bar`, `shop=convenience|supermarket` с `opening_hours`, парсим через opening-hours-py, проверяем `is_open(пятница 02:00)` в радиусе 500 м по пешему графу. Уверенность ограничена свежестью тега: можно использовать `check_date:opening_hours`, если он есть.
- «Для семей»: `leisure=playground`, `highway=pedestrian|living_street`, `amenity=hospital` плюс `healthcare:speciality=paediatrics` (тег приведён по общему знанию OSM, не проверен). «На машине»: `amenity=parking`, `amenity=charging_station`, доступ к входу через `highway=*` с `motor_vehicle=no` или `access=no` (пешеходная зона).
- «Стройки рядом» для Ниццы через data.gouv.fr — это ещё нужно найти. Парижский датасет показывает, что формат существует. Для Сочи реалистичны только OSM `landuse=construction` и `building=construction`.

### Gaps
- Порталы со стройками, перекрытиями и событиями для Ниццы, Барселоны (Open Data BCN), Лиссабона и Москвы (data.mos.ru: разрешения на строительство, ОАТИ) не проверены по API.
- Лицензия данных OpenChargeMap (по поставщикам) и её лимиты официально не подтверждены.
- `healthcare:speciality=paediatrics` и другие «семейные» теги, а также их заполненность, не проверялись.

---

## 14. Базовая карта для UI: политика тайлов OSM, OpenFreeMap, Protomaps PMTiles, MapLibre

### Takeaway
**Лучший вариант для «запуска без интернета-зависимостей» — Protomaps PMTiles:** `pmtiles extract` по bbox города из дневной сборки даёт один файл, который отдаётся статически вместе с приложением. Онлайн-альтернатива — **OpenFreeMap**: без ключей и без лимитов, коммерческое использование разрешено. **tile.openstreetmap.org для продакшена и офлайна не годится:** предзагрузка и офлайн запрещены.

### Cited Findings
- **Политика тайлов OSMF:** обязательны атрибуция на карте, уникальный User-Agent, Referer и соблюдение кэш-заголовков (иначе кэш не меньше 7 дней). **Запрещены bulk download и префетч, а «Offline use is not permitted on tile.openstreetmap.org».** SLA нет, могут заблокировать без уведомления — [OSMF Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/).
- **OpenFreeMap:** бесплатно, «no registration, no user database, no API keys, and no cookies». Публичный инстанс без лимитов на просмотры и запросы. Стили Positron, Bright, Liberty, Dark, Fiord, 3D. Коммерческое использование разрешено. Атрибуция обязательна (MapLibre добавляет её сам). Есть self-hosting и еженедельные планетарные выгрузки (Btrfs и MBTiles). Офлайн-загрузки в приложениях — до 3 000 тайлов или 50 МБ — [openfreemap.org](https://openfreemap.org/).
- **Protomaps:** `pmtiles extract https://build.protomaps.com/20261011.pmtiles my_area.pmtiles --bbox=...`, дневные сборки на maps.protomaps.com/builds. Лицензии BSD (код) и ODbL (данные). Планета до z6 весит около 60 МБ — [Protomaps Getting Started](https://docs.protomaps.com/guide/getting-started). Python-пакет `pmtiles` 3.8.1 (2026-09-16) — [PyPI pmtiles](https://pypi.org/project/pmtiles/) **[проба PyPI]**.

### Inferences
- В repo кладём `city.pmtiles` (для города до z15, ориентировочно десятки МБ, не проверено) плюс стиль protomaps-basemaps. MapLibre GL JS с протоколом `pmtiles://` читает его с того же сервера (nginx или FastAPI static), внешних зависимостей нет. Если файл слишком большой для git, его можно скачивать в `Dockerfile` (`pmtiles extract` занимает секунды–минуты), а OpenFreeMap оставить как fallback.

### Gaps
- Точный размер PMTiles-экстракта для города (Сочи или Ницца, z0–15) не измерен.
- Политика использования `build.protomaps.com` для автоматических экстрактов (допустимая частота) не проверена.

---

## 15. Положение солнца: pvlib, astral, suncalc

### Takeaway
Для «закат из окон западного фасада, апрель–август» хватит **suncalc** (векторизованный порт suncalc.js) или **astral**. pvlib точнее (SPA) и активно поддерживается, но тяжелее. Все три без сети и ключей.

### Cited Findings
- **pvlib** 0.16.1 (2026-09-26, BSD-3-Clause), набор для моделирования PV, включает расчёт положения солнца — [PyPI pvlib](https://pypi.org/project/pvlib/) **[проба PyPI]**.
- **astral** 3.2 (2022-11-05, Apache-2.0): «Calculations for the position of the sun and moon» — [PyPI astral](https://pypi.org/project/astral/) **[проба PyPI]**.
- **suncalc** 0.1.3 (2023-04-19, MIT): «A fast, vectorized Python port of suncalc.js» — [PyPI suncalc](https://pypi.org/project/suncalc/) **[проба PyPI]**.

### Inferences
- Алгоритм: для каждого дня года берём азимут солнца на высоте около 0–5° (закат) и проверяем, попадает ли он в сектор обзора фасада (нормаль ± 60–70°). Затем строим луч над DEM и зданиями от точки «окно на этаже k». Результат вида «закат виден с 5-го этажа, апрель–август» — прямое следствие расчёта. Уверенность зависит от высот зданий (см. раздел 3).
- astral и suncalc давно не обновлялись, но алгоритмы стабильны, и для хакатона это не риск.

### Gaps
- Точность astral и suncalc в сравнении с pvlib (NREL SPA) не измерялась. Для задачи «виден ли закат» ошибка около 0,1° несущественна, это вывод, а не измерение.

---

## 16. Легальность: можно ли скрапить сайты отелей и букингов

### Takeaway
**Скрапинг Booking и других OTA — плохая идея**: в ЕС пользовательское соглашение может запретить скрапинг даже незащищённой базы (CJEU, Ryanair v PR Aviation), а в РФ действует смежное право изготовителя БД (ст. 1334 ГК, база от 10 000 элементов презюмируется защищённой). **Безопаснее** опираться на открытые лицензированные данные (OSM, Overture, Wikidata), на точечное чтение **официального сайта самого отеля** из OSM/Overture (`website`) с соблюдением robots.txt и с «insubstantial part», а также на диалог с отельером.

### Cited Findings
- **CJEU C-30/14 Ryanair v PR Aviation (2015):** если база не защищена ни копирайтом, ни sui generis правом, директива 96/9 не мешает владельцу ограничить её использование договором (условия сайта). У Ryanair условия запрещали screen scraping в коммерческих целях — [Portolano](https://portolano.it/news/ecj-clarifies-database-directive-scope-in-screen-scraping-case), [MediaLaws](https://www.medialaws.eu/ecj-clarifies-database-directive-scope-in-screen-scraping-case/).
- **Директива о БД:** законный пользователь общедоступной базы вправе извлекать несущественные части, если это не противоречит нормальной эксплуатации базы — [MediaLaws](https://www.medialaws.eu/ecj-clarifies-database-directive-scope-in-screen-scraping-case/).
- **РФ, ст. 1334 ГК:** изготовитель БД, создание которой потребовало существенных затрат, имеет исключительное право на извлечение материалов. База от 10 000 элементов презюмируется таковой. В деле «ВКонтакте» против «Дабла» (А40-18827/17) СИП указал, что неважно, кто наполнял базу, важна существенность затрат — [pravo.ru](https://pravo.ru/story/202821/), [pravo.ru news](https://pravo.ru/news/233284/). Есть и позиция, что индексирование и кеширование смежные права не нарушают — [АГ](https://www.advgazeta.ru/novosti/indeksirovanie-i-keshirovanie-ne-narushaet-smezhnye-prava-sotsseti-na-bazu-dannykh-polzovateley/).
- **Данные ODbL, CC BY-SA и CC BY-NC-SA** накладывают свои условия: share-alike для OSM-производных (Overture Buildings, Overture+OSM) — [Overture Buildings](https://docs.overturemaps.org/guides/buildings/), [Overture Places](https://docs.overturemaps.org/guides/places/); некоммерческое использование для Ookla и FABDEM — [Ookla](https://registry.opendata.aws/speedtest-global-performance/), [FABDEM](https://gee-community-catalog.org/projects/fabdem/).

### Inferences
- Для прототипа: (1) не трогать Booking, Ostrovok и другие OTA (к тому же условие кейса — без данных Островка); (2) с сайта отеля брать только факты (наличие парковки, правила для животных) с цитатой и URL как источником, уважая robots.txt, с низкой частотой запросов и честным User-Agent; (3) в README перечислить лицензии и атрибуции (OSM ODbL, Open-Meteo CC BY 4.0, Ookla CC BY-NC-SA, OpenCellID CC BY-SA, Commons — по файлам).
- Это не юридическая консультация. Для хакатонного некоммерческого прототипа риски минимальны, но жюри оценит явный раздел «Лицензии и источники».

### Gaps
- Конкретные пункты ToS Booking.com и Островка о скрапинге не проверены.
- Судебной практики конкретно по сайтам отелей не найдено.

---

## 17. Рекомендованный минимальный стек источников

### Takeaway
Минимальный стек, который запускается без ключей: **Geofabrik PBF → QuackOSM, Overture (DuckDB/S3), Copernicus GLO-30 (S3), Open-Meteo (archive, marine, air-quality, climate), Ookla (S3), Wikimedia Commons и Wikipedia, WDQS, Protomaps PMTiles.** С бесплатными ключами добавляются Mapillary, OpenAQ, OpenCellID и OpenChargeMap. По глубине открытых данных Ницца (Франция) заметно богаче Сочи: реальные высоты зданий, карты шума END, GTFS без ключа, Panoramax, качество воды EEA. Сочи выигрывает «родным» контекстом для Островка, но часть характеристик там будет с низкой уверенностью или уйдёт в вопросы отельеру.

### Cited Findings
- Все факты таблицы опираются на разделы 1–16 выше (ссылки там).

### Inferences

| Характеристика | Основной источник | Fallback | Достижимая уверенность (Ницца / Сочи) |
|---|---|---|---|
| Список отелей (имя, координаты, сайт, звёзды) | OSM `tourism=*` (Geofabrik + QuackOSM) | Overture Places (`basic_category` hotel/lodging), матчинг по расстоянию и имени | Высокая / высокая (звёзды: средняя / низкая — `stars` у ~60% / ~29%) |
| Высота и этажность соседних зданий | OSM `height`/`building:levels` (через Overture Buildings) | Copernicus DSM минус DTM (FABDEM), GHSL 100 м | Высокая (69% с height) / средняя (40% с этажами, высот почти нет) |
| Вид на море, горы, закат, достопримечательность | DEM Copernicus GLO-30 + высоты зданий + suncalc/pvlib (луч по растру) | FABDEM как DTM, OSM water polygons для моря; вопрос отельеру «какие номера на запад» | Средняя–высокая / средняя (+ подтверждение отельера) |
| Тишина и шум | Ницца: END Round 4 Lden/Lnight (нац. портал) | OSM-эвристика: дороги по классам, ж/д, бары и клубы с `opening_hours`, аэропорт | Высокая / низкая–средняя |
| Реальная пешая доступность (пляж, метро, центр, подъёмы) | osmnx walk-граф + высоты из DEM (локально, заранее) | FOSSGIS OSRM `routed-foot` (точечно) | Высокая / высокая |
| Что работает ночью | OSM `opening_hours` + opening-hours-py | Overture Places (часы работы не исследованы) → «неизвестно» | Средняя / средняя (зависит от заполненности тега) |
| Для семей (площадки, пешеходные улицы, детская больница) | OSM `leisure=playground`, `highway=pedestrian`, `amenity=hospital` | Wikidata (больницы) | Средняя–высокая / средняя |
| На машине (подъезд, парковка, EV) | OSM `amenity=parking`, `access`, `amenity=charging_station` | OpenChargeMap (ключ) | Средняя / средняя |
| Трансфер из аэропорта (ОТ, пересадки, ночью) | GTFS (Ницца — transport.data.gouv.fr, Барселона — TMB) + r5py | Для РФ: OSM route-relations + вопрос отельеру | Высокая / низкая |
| Климат по месяцам, солнце, вода | Open-Meteo Historical (ERA5, `sunshine_duration`) + Marine (`sea_surface_temperature`, ERA5-Ocean) | Open-Meteo Climate API | Высокая / высокая |
| Качество воздуха | Open-Meteo Air Quality (CAMS Europe) | OpenAQ v3 (ключ, станции) | Средняя / средняя |
| Связь для удалёнки | Ookla Open Data (fixed+mobile, Q3 2026) | OpenCellID (ключ, LTE/NR вышки) | Средняя / средняя (покрытие РФ не проверено) |
| Стройки и мероприятия рядом | Городские порталы (Париж — подтверждён; Ницца/Барселона — проверить) | OSM `landuse=construction`, `building=construction` | Средняя / низкая |
| История здания | OSM `wikidata` → Wikidata (P571/P149/P1435) | Wikipedia geosearch + LLM-экстракция с цитатой | Средняя / низкая–средняя (мало связок) |
| Фото фасада и что видно с улицы | Mapillary v4 (токен) + Wikimedia Commons geosearch | Panoramax (Франция, etalab-2.0) | Средняя / низкая–средняя |
| Качество воды на пляже | EEA Bathing Water 2025 (ArcGIS REST) | — | Высокая / нет данных (РФ вне EEA) |
| Световое загрязнение (звёзды, тёмные ночи) | VIIRS VNL v2.2 (EOG, регистрация) | Earth Engine `NOAA/VIIRS/DNB/ANNUAL_V22` | Средняя / средняя |
| Питомцы, шумоизоляция, шторы, этаж номера | **Диалог с отельером** (структурированный в поля) | Сайт отеля (точечно, robots.txt) | Высокая после ответа / высокая после ответа |
| Базовая карта UI | Protomaps PMTiles (файл в repo/образе) + MapLibre | OpenFreeMap (онлайн, без ключа) | — |

### Gaps
- Оценки уверенности в таблице — мой вывод на основе покрытия данных из проб 26.10.2026 (центр Сочи и Ницца), а не измеренная точность. Точность нужно мерить на собственной размеченной выборке (дополнительная задача 5 кейса).
- Для Москвы, Санкт-Петербурга и Калининграда цифры покрытия не снимались. Перед выбором региона стоит повторить пробы из разделов 1 и 3: это около 30 с на DuckDB-запрос к Overture.
