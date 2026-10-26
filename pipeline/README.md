# О!кно: открытые данные Сочи

Pipeline нужен для обновления данных разработчиком. Приложение запускается из уже включённых seed и PMTiles и не вызывает его при старте. Сырые PBF, GeoTIFF, промежуточные выгрузки и окружение находятся в gitignored `data/raw/` и `.venv/`.

## Запуск

Проверено на macOS arm64, Python 3.13. Контракты приложения совместимы с Python 3.12. Для подготовки данных нужны интернет, около 2–3 ГБ памяти и несколько ГБ свободного диска; в runtime тяжёлые зависимости pipeline не нужны.

```sh
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r pipeline/requirements.lock
.venv/bin/python -m pipeline.run --region pipeline/regions/sochi.yaml
```

Эквивалентная переменная окружения:

```sh
REGION=pipeline/regions/sochi.yaml .venv/bin/python -m pipeline.run
```

Корневой `make pipeline REGION=pipeline/regions/sochi.yaml` может вызывать эту команду. Параметры bbox, доли портфеля, PBF snapshot, Overture release, DEM tiles, допущения высот, погодные точки и периоды находятся в YAML. Для другого региона создают конфиг с такими же полями A/B и проверяют покрытие источников. У `portfolio_ids.json` есть поле региона: фиксированный список Сочи применяется только к этому региону. Смена целевого состава Сочи требует явной правки этого списка и повторного аудита, чтобы обновление источника не меняло демонстрационные ID молча.

Отдельные этапы:

```sh
.venv/bin/python -m pipeline.run --stage fetch_osm
.venv/bin/python -m pipeline.run --stage fetch_overture
.venv/bin/python -m pipeline.run --stage merge_hotels
.venv/bin/python -m pipeline.run --stage fetch_dem
.venv/bin/python -m pipeline.run --stage fetch_open
.venv/bin/python -m pipeline.run --stage fetch_ookla
.venv/bin/python -m pipeline.run --stage fetch_tiles
.venv/bin/python -m pipeline.run --stage export_seed
.venv/bin/python -m pipeline.run --stage validate
```

`fetch_osm` должен предшествовать `fetch_overture`: второй этап добавляет только действительно дополнительные footprints. Повторные этапы читают кэш с URL, временем получения, ETag, размером и SHA256. `fetch_overture` делает отдельный Parquet bbox scan для каждой зоны: один OR-предикат в DuckDB приводил к существенно большим сетевым чтениям. Overpass не используется. Сетевые сбои не заменяются синтетическими значениями.

## Зафиксированный результат 26.10.2026

| Артефакт | Состав |
|---|---|
| `hotels.json`, `hotels.csv.gz` | 250 реальных записей: 175 центр A + 75 Адлер–Имеретинка B |
| `hotels_b.json` | Те же 75 записей B для независимой передачи гео-потоку |
| `buildings.geojson`, `buildings.csv.gz` | 73 641 здание: 58 305 OSM + 15 336 дополнительных Overture |
| `pois.geojson` | 15 505 реальных POI, оригинальные `opening_hours`, `access` и остальные теги |
| `roads.geojson` / `railways.geojson` | 24 742 / 520 объектов |
| `beaches.geojson` / `coastline.geojson` | 47 / 16 объектов; исходное направление coastline сохранено |
| `walk_graph.json.gz` | 122 640 узлов, 263 346 направленных рёбер; все узлы с DEM-высотой |
| `open_claims.jsonl.gz` | 3 364 Claim-valid записи: 3 000 climate, 250 AQ, 114 nearby_photos |
| `nearby_photos.json` | 100 файлов Commons с индивидуальными автором, лицензией и source URL; 23 локальные миниатюры |
| `connectivity.json` | 3 mobile Ookla tiles, 0 fixed; ни один из выбранных отелей не попадает в эти три тайла, поэтому hotel connectivity claims отсутствуют |
| `city.pmtiles` | 7 536 245 байт, обе зоны, zoom 0–15, Protomaps schema |

Главные пути для S2: `data/seed/{hotels,buildings,pois,roads,railways,beaches,coastline}.json/geojson`, `walk_graph.json.gz`. Источники и точные пути перечислены в `manifest.json`; схемы файлов — в общих контрактах. Для runtime карта доступна по `/data/tiles/city.pmtiles`; glyphs — `/data/tiles/fonts/{fontstack}/{range}.pbf`, sprites — `/data/tiles/sprites/light` и `dark`. Включены Noto Sans Regular/Medium/Italic, диапазоны Latin 0–255 и Cyrillic 1024–1279, sprite 1x/2x и тексты лицензий.

Финальная выборка строится из 499 именованных OSM-объектов размещения и 500 Overture hotel/lodging/resort/hostel/motel. Консервативное объединение использует имя и расстояние; общий домен гостиничной сети сам по себе не объединяет разные отели. Для совпадения сайта требуется полный URL и сходство имени. Идентификаторы источников не переписываются; все объединения и исключения сохранены в `hotel_merge_audit.json`. Первый переход на зоны A/B сохранил 175 центральных ID и заменил 75 объектов на зону B. Последующая явная проверка объединила два дубля санатория Заполярье с его основным OSM ID и исключила один неоднозначный locality label; три места A заполнены реальными Overture гостиницами. Причины, старые и новые ID записаны в audit и региональном конфиге. Оригинальные имена и координаты каждого входного источника остаются в `Hotel.tags.source_records`.

## Источники, даты и лицензии

Реально использованы **8 внешних семейств данных**. В registry 12 записей из-за раздельных тем Overture и Open-Meteo, плюс один `geo_engine` с `kind=computed`; вычислительный движок не считается внешним источником.

| Семейство | Snapshot / содержание | Лицензия и атрибуция |
|---|---|---|
| OpenStreetMap / Geofabrik | South FD `261025`, данные до 2026-10-25T21:21:21Z | ODbL 1.0, © OpenStreetMap contributors, extract Geofabrik |
| Overture | `2026-10-21.0`, Places и Buildings, per-record providers сохранены | Places CDLA Permissive 2.0 / Apache 2.0 / CC0 по записи; Buildings ODbL |
| Copernicus DEM | GLO-30 Public, release 2021, N43E039 + N43E040 | Copernicus DEM public licence; полная атрибуция DLR/Airbus/EU/ESA в Source |
| Open-Meteo | ERA5 2016–2025; marine фактический доступный период по полю; CMIP6 2025; CAMS 21–26.10.2026 | CC BY 4.0; бесплатный API только для некоммерческого прототипа; Weather data by Open-Meteo.com |
| Wikimedia Commons | Геопоиск A/B и `imageinfo.extmetadata` каждого файла | Индивидуальные лицензии, авторы и source URL в `nearby_photos*.json` |
| Wikidata | Только явные `wikidata=Q…` у OSM отелей | CC0; один точный связанный объект, но inception/architecture не заполнены — история unknown без claim |
| Ookla | Fixed/mobile 2026Q3, bbox query с сохранением test/device counts | CC BY-NC-SA 4.0; атрибуция Ookla в registry; использовать только некоммерчески |
| Protomaps | Daily build 2026-10-25, региональный PMTiles | ODbL данные, BSD утилита, OFL 1.1 шрифты, MIT sprites; сохранить видимую атрибуцию © Protomaps © OSM contributors |

Ссылки на точные запросы, retrieval timestamps, raw/subset SHA256, bbox, SQL, licences и SHA256 всех включённых артефактов находятся в `data/seed/manifest.json`. Для удалённого Parquet SHA относится к материализованному результату запроса, что явно обозначено; хэш всей мировой базы не выдумывается. PBF и DEM проверяются целиком. Производная объединённая база с OSM распространяется с ODbL-атрибуцией и обязательствами этой лицензии; исходные права на Commons и Ookla сохраняются.

## Границы достоверности

- Имена, координаты, звёзды, сайт и этажность берутся только из источников. `basic_fields_source=open_data`; цены, типы номеров, шумоизоляция и расписания не заполняются моками.
- Высота есть у 119 зданий; 25 145 оценены как этажность × 3 м; 48 377 имеют `height_m=null,height_source=unknown`. Неположительный исходный `height=-1` сохранён как тег, но исключён из высотных входов. Неизвестная этажность не становится выдуманным числом.
- Copernicus — **DSM с растительностью и инфраструктурой**, не bare-earth DTM. Использование его как рельефа должно снижать уверенность; это не точный уровень земли у окна.
- Собираются way-полигоны и tagged nodes. Multipolygon relations не собраны; это ограничивает полноту контуров и гостиниц и записано в manifest.
- Foot graph сохраняет реальные node IDs, `oneway:foot`, запреты foot/access и переходы; пересечение линий само по себе не создаёт узел. Граф обрезан по двум зонам, имеет 724 слабых компоненты; два основных содержат 69 084 и 43 400 узлов. Отсутствие маршрута не означает физическую недоступность.
- Воздушный климат — десятилетняя выборка, не 30-летняя норма WMO. SST имеет меньше непустых лет (для сентября 2023–2025), собственные sample dates/counts сохранены. CMIP6 хранится отдельно как проекция и не подменяет ERA5. AQ — недавнее модельное окно с частью текущего прогноза, не сезонная чистота воздуха.
- Фото по геотегу не подтверждают вид из номера или фасад отеля. Каждая используемая фотография сохраняет свою лицензию.
- Ookla — региональные тесты пользователей, не Wi-Fi отеля. Нет совпадающего тайла — unknown. GTFS, авиашум и стройки вне OSM не подменены неподтверждёнными источниками.

## Проверки и безопасность

```sh
.venv/bin/python -m pipeline.validate
.venv/bin/python -m compileall -q pipeline
.venv/bin/pip install --dry-run --require-hashes -r pipeline/requirements.lock
.venv/bin/pip-audit --disable-pip --no-deps -r pipeline/requirements.lock --format json --output pipeline/dependency_audit.json
```

`validation.json` подтверждает Hotel/Source/Claim модели, уникальность ID, ровно две CSV-колонки, `gzip mtime=0`, геометрии, непустую provenance, ссылки всех рёбер и DEM coverage, существование evidence artifacts, SHA256 manifest и предел **<50 MB на файл**. Наибольший файл — `buildings.geojson`, 41 636 238 байт. Проверка не является измерением точности гостиничных характеристик.

`requirements.in` содержит прямые зависимости; `requirements.lock` получен `pip-compile --generate-hashes` и фиксирует все транзитивные версии и hashes. **pip-audit 2.10.1 от 26.10.2026: 22 пакета, известных уязвимостей не найдено**; машинный отчёт — `pipeline/dependency_audit.json`. Утилита go-pmtiles 1.31.2 загружается только с официального GitHub release и проверяется по заранее записанному SHA256 издателя **до распаковки и запуска**. SHA для Darwin/Linux arm64/x86_64 находятся в `fetch_tiles.py`; URL и проверенный digest сохранены в manifest. Shell installers и `curl | sh` отсутствуют.
