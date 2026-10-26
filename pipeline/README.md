# О!кно: открытые данные Сочи

Pipeline нужен для обновления данных разработчиком. Приложение запускается из уже включённых seed и PMTiles и не вызывает его при старте. Сырые PBF, GeoTIFF, промежуточные выгрузки и окружение находятся в gitignored `data/raw/` и `.venv/`.

## Запуск

Полный pipeline запускается в отдельном `Dockerfile.pipeline`: Python **3.12.15 slim-bookworm**, целевая платформа **linux/amd64**, multi-stage, пользователь 10001. Он не использует корневую `.venv` и не добавляет GDAL/инструменты загрузки в runtime-образ приложения. Для подготовки данных нужны интернет, несколько ГБ памяти и свободного диска; вычисления всех 250 отелей могут занять длительное время. На arm64 Docker использует эмуляцию amd64.

```sh
docker build --platform linux/amd64 -f Dockerfile.pipeline -t okno-pipeline .
docker run --rm --platform linux/amd64 \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$PWD/data,dst=/app/data" \
  okno-pipeline --region pipeline/regions/sochi.yaml --dry-run
```

Полная пересборка из открытых источников — та же команда **без `--dry-run`**. Она изменяет производные файлы в `data`; существующий raw cache читается там же. `--user` сохраняет владельца bind-mounted файлов на POSIX-хосте; образ по умолчанию также работает без root. `REGION` можно передать через `--region` либо `docker run -e REGION=...`.

`pipeline.rebuild` исполняет следующую последовательность и печатает её до запуска:

```text
fetch_osm → fetch_overture → merge_hotels → fetch_dem → build_dsm
→ fetch_open → fetch_ookla → fetch_tiles → export_seed
→ compute_views (все 250) → compute_runtime_cache (герой)
→ compact_seed → validate → compute_validate
```

`fetch_dem` сначала сохраняет TIFF и добавляет высоты в пеший граф. `build_dsm` получает **оба явных пути** `data/raw/Copernicus_DSM_COG_10_N43_00_E039_00_DEM.tif` и `...E040...tif`, проверяет их размеры и SHA-256 по download metadata и вызывает `pipeline.build_dsm.convert(..., output=data/geo/terrain_30m.npz, bbox=[39.35,43.10,40.45,43.98], resolution=30)`. Только после этого `compute_views` строит локальные DSM, виды, закат, шум и маршруты для полного списка отелей. Отдельных шагов `compute_sun/noise/walk` в реализации нет: они входят в этот расчёт. `export_seed` выполняется до geo, чтобы нормализация входов не инвалидировала новые claims. Упаковка после geo сохраняет хеши исходных байтов.

По умолчанию LOS cache создаётся только для demo-героя `osm-node-10058556917`. Для другого отеля передают `--cache-hotel-id ID` (можно повторять); `--all-runtime-caches` явно включает все подходящие отели и требует существенно больше места. Все **geo claims 250 отелей** рассчитываются независимо от этого выбора. Отель без сохранённого LOS cache использует полный региональный пересчёт.

`--compact-geo` включает отдельный optional offline hook `python -m pipeline.compact_geo --data-dir data --apply` после `compute_views` и перед LOS cache/валидацией. Он предназначен для lossless упаковки per-hotel output NPZ, сохраняя массивы и физические terrain/LOS inputs. `--apply` передаётся явно: без него сам `compact_geo` только показывает план. По умолчанию hook выключен; для его включения модуль `pipeline.compact_geo` должен быть включён в исходники образа. `pipeline.rebuild --compact-geo --dry-run` явно показывает включённый шаг и полную команду, не исполняя её.

Проверка включённых результатов без сети и без изменений в `data`:

```sh
docker run --rm --platform linux/amd64 --network none \
  --read-only --tmpfs /tmp \
  --mount "type=bind,src=$PWD/data,dst=/app/data,readonly" \
  okno-pipeline --validate-only
```

`--validate-only` проверяет seed, 250 geo snapshot, claims, provenance и имеющиеся LOS cache; новые cache не создаются, отчёты не сохраняются поверх поставки. `--from-cache` означает **пересчёт geo из уже экспортированных seed/tiles и cached DEM TIFF**, а не восстановление seed из PBF/Parquet. Перед расчётом seed и DEM проходят проверку; пропущенные скачивания не объявляются выполненными. Этот режим запускают с `--network none`, RW mount `data` и при необходимости отдельным read-only mount raw cache в `/app/data/raw`. Он всё равно пересчитывает все geo outputs, поэтому его не запускают для простой проверки поставки.

`--from-stage NAME --to-stage NAME` ограничивает диапазон и требует готовых входов предыдущих шагов; он не загружает пропущенные зависимости. `--workers N` управляет числом fork workers в `compute_views`. Полный и частичный план можно посмотреть через `--dry-run`, справку — через `--help`. Для нового региона нужен явный `--terrain-bbox WEST SOUTH EAST NORTH`; параметры портфеля, snapshots и DEM tiles берутся из YAML. У `portfolio_ids.json` есть поле региона: фиксированный список Сочи применяется только к этому региону. Смена состава Сочи требует явной правки этого списка и повторного аудита.

Хеши lock фиксируют окружение, но полный rebuild создаёт новые `computed_at`, claim IDs и retrieval metadata. Побайтного совпадения исторических JSON snapshot и одинаковых floating-point результатов между прежним macOS окружением и Linux не обещаем. Включённые результаты не перегенерируются автоматически при старте приложения.

Старый `pipeline.run` оставлен для отдельных этапов подготовки открытых данных; он **не является полным geo rebuild**. Для него требуется то же объединённое окружение `pipeline/rebuild.lock`:

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

## Окружение и совместимость

`pipeline/rebuild.in` включает `backend/requirements.in` и только дополнительные `duckdb`, `osmium`, `requests`, `rasterio`, `pmtiles` из acquisition pipeline. Поэтому общие `pydantic==2.11.4`, `shapely==2.1.0`, `pyproj==3.7.1`, `PyYAML==6.0.2`, `numpy==2.2.5`, `numba==0.61.2` и `astral==3.2` совпадают с runtime direct pins. Старый `pipeline/requirements.lock` описывает прежнее окружение acquisition и не используется Docker rebuild; runtime lock не меняется.

Официальная [матрица Numba 0.61.2](https://numba.readthedocs.io/en/0.61.2/user/installing.html#version-support-information) поддерживает Python 3.12; [release notes](https://numba.readthedocs.io/en/0.61.2/release/0.61.2-notes.html) подтверждают NumPy 2.2. [Rasterio 1.5.2](https://pypi.org/project/rasterio/1.5.2/) требует Python ≥3.12 и NumPy ≥2. Его Linux wheels включают GDAL; Docker устанавливает только wheels с `--only-binary=:all:` и проверяет все SHA-256 через `--require-hashes`.

Базовый Python-образ дополнительно закреплён digest. Системная библиотека `libexpat1=2.5.0-1+deb12u5` берётся из Debian bookworm-security: она нужна GDAL wheel для импорта в slim-образе. При обновлении образа проверяют и этот pin; runtime Dockerfile не меняется.

Обновление объединённого lock выполняется отдельно от runtime. В Python 3.12 окружении с `pip-tools==7.6.2` и `pip-audit==2.10.1`:

```sh
python -m piptools compile --generate-hashes --strip-extras \
  --no-emit-index-url --no-emit-trusted-host \
  --output-file pipeline/rebuild.lock pipeline/rebuild.in
python -m pip_audit --disable-pip --no-deps -r pipeline/rebuild.lock
```

`pip-compile` следует запускать в целевом Python/платформе при изменении зависимостей: [документация pip-tools](https://pip-tools.pypa.io/en/stable/) объясняет зависимость environment markers от окружения. Приёмка образа включает импорты бинарных модулей и `--validate-only` на Linux amd64.

`fetch_osm` должен предшествовать `fetch_overture`: второй этап добавляет только действительно дополнительные footprints. Повторные этапы читают кэш с URL, временем получения, ETag, размером и SHA256. `fetch_overture` делает отдельный Parquet bbox scan для каждой зоны: один OR-предикат в DuckDB приводил к существенно большим сетевым чтениям. Overpass не используется. Сетевые сбои не заменяются синтетическими значениями.

## Зафиксированный результат 26.10.2026

| Артефакт | Состав |
|---|---|
| `hotels.json`, `hotels.csv.gz` | 250 реальных записей: 175 центр A + 75 Адлер–Имеретинка B |
| `hotels_b.json` | Те же 75 записей B для независимой передачи гео-потоку |
| `buildings.geojson.gz` | 73 641 здание: 58 305 OSM + 15 336 дополнительных Overture |
| `pois.geojson.gz` | 15 505 реальных POI, оригинальные `opening_hours`, `access` и остальные теги |
| `roads.geojson.gz` / `railways.geojson.gz` | 24 742 / 520 объектов |
| `beaches.geojson.gz` / `coastline.geojson.gz` | 47 / 16 объектов; исходное направление coastline сохранено |
| `walk_graph.json.gz` | 122 640 узлов, 263 346 направленных рёбер; все узлы с DEM-высотой |
| `open_claims.jsonl.gz` | 3 364 Claim-valid записи: 3 000 climate, 250 AQ, 114 nearby_photos |
| `nearby_photos.json` | 100 файлов Commons с индивидуальными автором, лицензией и source URL; 23 локальные миниатюры |
| `connectivity.json` | 3 mobile Ookla tiles, 0 fixed; ни один из выбранных отелей не попадает в эти три тайла, поэтому hotel connectivity claims отсутствуют |
| `city.pmtiles` | 7 536 245 байт, обе зоны, zoom 0–15, Protomaps schema |

Главные пути для S2: `data/seed/{hotels,buildings,pois,roads,railways,beaches,coastline}.json/geojson`, `walk_graph.json.gz`. Источники и точные пути перечислены в `manifest.json`; схемы файлов — в общих контрактах. Для runtime карта доступна по `/data/tiles/city.pmtiles`; glyphs — `/data/tiles/fonts/{fontstack}/{range}.pbf`, sprites — `/data/tiles/sprites/light` и `dark`. Включены Noto Sans Regular/Medium/Italic, диапазоны Latin 0–255 и Cyrillic 1024–1279, sprite 1x/2x и тексты лицензий.

Seed GeoJSON физически хранятся только в `.geojson.gz`, `mtime=0`, без имени файла в gzip header. Логические пути `/data/seed/*.geojson` сохранены для существующих claims и geo snapshot. `geo.io.read_bytes`, `exists` и `read_json` сначала используют plain файл, а при его отсутствии читают `.gz` с распаковкой исходных байтов. Явный путь с окончанием `.gz` в `read_bytes` возвращает **сжатые байты**: SHA-256 `walk_graph.json.gz` и бинарных `.npz` не меняется. Для `read_json` передают логический путь без `.gz`. `pipeline.common.read` использует тот же интерфейс, а `dump` записывает seed GeoJSON сразу в gzip. Маленький `data/tiles/coverage.geojson` остаётся plain для CLI PMTiles.

Для lossless упаковки уже подготовленного seed без загрузки источников и пересчёта геометрии:

```sh
.venv/bin/python -m pipeline.compact_seed
```

Команда проверяет прежние SHA-256 по manifest, сжимает исходные байты без JSON-пересериализации, проверяет обратную распаковку и обновляет только физическое представление. Повторный запуск сохраняет те же байты gzip и manifest. Шесть слоёв занимают 7 272 848 байт вместо 58 264 156; удалены дубли `buildings.csv.gz` и `osm_features.csv.gz` общим размером 7 815 034 байта. Runtime читает слои напрямую из GeoJSON, соответствующие DB таблицы не нужны для загрузки seed. `export_seed` также больше не создаёт эти дубли.

В `manifest.outputs` ключ указывает на физический `.geojson.gz`; `bytes` и `sha256` относятся к нему. Поля `encoding=gzip`, `logical_path`, `uncompressed_bytes` и `uncompressed_sha256` описывают исходный GeoJSON. Хеши в существующих geo claims и snapshot относятся к исходным байтам и сохраняются. Runtime LOS cache проверяет именно эти хеши; повторные обращения используют digest, привязанный к physical path, mtime, ctime, размеру и inode файла. Изменение исходного seed по-прежнему инвалидирует cache.

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
- Срок действия claims задаётся одним правилом: открытые данные живут `ttl_days` своей характеристики (`data/characteristics/*.yaml`), георасчёты перепроверяются раз в квартал (`geo.service.geo_ttl_days`, не дольше `ttl_days`). `python -m pipeline.retime_claims --check` сверяет поставленные ledgers с правилом; без `--check` он выпускает новые claims с исправленным сроком и `supersedes_ids` на прежние записи (прежние записи не редактируются). Так 26.10.2026 переизданы 250 claims качества воздуха (было 1 день), 114 claims фото окрестностей (365 → 180 дней) и 1 161 claim наружного шума (30 → 90 дней).

## Проверки и безопасность

```sh
docker run --rm --platform linux/amd64 --network none --read-only --tmpfs /tmp \
  --mount "type=bind,src=$PWD/data,dst=/app/data,readonly" okno-pipeline --validate-only
python -m compileall -q pipeline
python -m pip install --dry-run --only-binary=:all: --require-hashes -r pipeline/rebuild.lock
python -m pip_audit --disable-pip --no-deps -r pipeline/rebuild.lock --format json --output /tmp/okno-rebuild-audit.json
```

`validation.json` подтверждает Hotel/Source/Claim модели, уникальность ID, ровно две CSV-колонки, `gzip mtime=0`, геометрии, непустую provenance, ссылки всех рёбер и DEM coverage, существование evidence artifacts, физические и исходные SHA256 manifest и предел **<50 MB на файл**. Наибольший включённый seed/tiles файл — `city.pmtiles`, 7 536 245 байт; `buildings.geojson.gz` занимает 4 686 376 байт. `pipeline.compute_validate` дополнительно проверяет исходные GeoJSON-хеши в каждом из 250 geo snapshot и 20 340 claims. Проверка не является измерением точности гостиничных характеристик.

Объединённый `rebuild.lock` получен `pip-compile --generate-hashes`: **pip-audit 2.10.1 от 26.10.2026, 45 пакетов, известных уязвимостей не найдено**. На Linux amd64 проверены импорты, Numba known-answer LOS, чтение исходного DEM TIFF и offline/read-only `--validate-only`: 250 отелей, 20 340 geo claims, один существующий LOS cache. Полный online rebuild при этой проверке не запускался, большие данные не пересчитывались. Старый машинный отчёт `pipeline/dependency_audit.json` относится к acquisition-окружению `requirements.lock` (22 пакета). Утилита go-pmtiles 1.31.2 загружается только с официального GitHub release и проверяется по заранее записанному SHA256 издателя **до распаковки и запуска**. SHA для Darwin/Linux arm64/x86_64 находятся в `fetch_tiles.py`; URL и проверенный digest сохранены в manifest. Shell installers и `curl | sh` отсутствуют.
