# Геленджик: второй регион тем же pipeline

Проверка 26.10.2026 из отдельного worktree `okno-wt-gelendzhik`, ветка `feat/gelendzhik`, база `04336b93db973844c5ddfeeb893804a16ebbbab0`. Первый `date`: **2026-10-26 16:46:03 UTC**. Стадии pipeline закончились **17:07:41 UTC**, затем выполнена дополнительная read-only проверка. Время завершения всей работы записано в `workflow_ended_at` [машинного отчёта](region_gelendzhik_timings.json). Лимит 90 минут не исчерпан.

Результат: **30 реальных исходных объектов размещения, 2376 geo claims и 418 open-data claims**, 8 различных характеристик. Это перенос вычислительной цепочки, а не проверка существования окон, номеров или эмпирической точности вида. Ручных/фотографических контрольных меток нет; `accuracy=null`.

## Изоляция и параметры

До запуска весь исходный `data` этого worktree был перенесён в `runtime/initial-data-snapshot`; эксперимент начинался с пустых `raw`, `seed`, `geo`, `tiles`. Единственный повторно использованный вход — неизменяемый PBF, hardlink файла из `okno-wt-data`; его metadata скопирована отдельно. Raw-каталог, stage metadata и bbox-выходы другого потока не подключались. PBF проверен целиком: **316601771 B**, SHA-256 `f30952667b42e6c88a442c7570292bd70c29c83c4d4aae1fe68aad08ef00be0f`, первоначальное получение `2026-10-26T12:16:08.470334+00:00`.

[Конфиг](../../pipeline/regions/gelendzhik.yaml) задаёт по 15 отелей на северной и центральной/восточной стороне бухты. Selection bbox A: `[38.020,44.575,38.100,44.615]`, B: `[38.040,44.540,38.110,44.575]`. Coverage rectangles используются **общим объединением**, а не как независимые границы соответствующих selection-зон: итоговый bbox `[37.995,44.510,38.150,44.650]`. По реальному PBF внутри selection A/B найдены 70/97 именованных кандидатов размещения; всего в coverage 184 OSM кандидата. Это проверка координат по исходным данным, без Overpass.

Для дальнего рельефа передан явный `--terrain-bbox 37.65 44.25 38.50 44.90`. Загружены правильные **N44E037 и N44E038**, 41592727 B суммарно; тайлы Сочи N43E039/040 не использовались. Перепроекция в EPSG:32637, 30 м, `valid_fraction=1.0` внутри заданной сетки. Copernicus GLO-30 release 2021 является DSM с растительностью/инфраструктурой, а не точным уровнем земли у окна. Дальние изгибы берега вне OSM coverage не проверены.

Все записи и крупные артефакты сохранены только локально в **`runtime/gelendzhik-data/`**. После прогона исходный snapshot возвращён в `data` worktree. Логические evidence-пути `/data/...` разрешаются, когда `runtime/gelendzhik-data` монтируется в `/app/data`. В git входят только конфиг, две минимальные параметризации, инструмент замеров и этот отчёт с JSON. Основные seed/geo/tiles, backend, frontend и контракты не изменялись. Этот поток не писал pipeline inputs/seed/geo/tiles main. Проверка `git status -- data` в 17:09 UTC была чистой; позднее root параллельно обновил `data/eval/results.json` своим eval-прогоном. SHA-256 основного seed manifest при финальной проверке по-прежнему совпадает с исходным snapshot: `3589fc304c83771768c4bed3ec1622871bb6875ee5db40d348bc29ba05df5e57`.

## Команды и среда

Apple M4, 10 CPU, 16 GiB RAM; Docker Desktop 29.4.0, 10 CPU / 8321712128 B RAM. Образ `okno-pipeline:rebuild`, ID `sha256:ff679436cc062273f797b6e871e17b1bcda30efbf131a4bbe4969b62420d67e5`; Linux amd64 через Rosetta на arm64. Образ и его зависимости уже находились в кэше: их сборка/скачивание не измерялись. `compute_views` использовал **1 worker**. Общая рабочая машина, не контролируемый производственный стенд; во время части OSM-разбора параллельно читался отдельный snapshot Сочи для регрессионной проверки.

Создание выполнено из основного checkout; после `cd` команды выполнялись из изолированного worktree. Функция сокращает повторяющиеся bind mounts; изменения pipeline и новый YAML подключены к готовому образу read-only.

```sh
git worktree add ../okno-wt-gelendzhik -b feat/gelendzhik main
cd ../okno-wt-gelendzhik
mkdir -p runtime
mv data runtime/initial-data-snapshot
mkdir -p data/raw data/seed data/geo data/tiles
ln ../okno-wt-data/data/raw/south-fed-district-261025.osm.pbf data/raw/
cp ../okno-wt-data/data/raw/south-fed-district-261025.osm.pbf.metadata.json data/raw/

probe() {
  network="$1"
  timings="$2"
  shift 2
  docker run --rm --platform linux/amd64 --network "$network" \
    --mount "type=bind,src=$PWD/data,dst=/app/data" \
    --mount "type=bind,src=$PWD/pipeline,dst=/app/pipeline,readonly" \
    --mount "type=bind,src=$PWD/scripts,dst=/app/scripts,readonly" \
    --mount "type=bind,src=$PWD/runtime,dst=/app/runtime" \
    --entrypoint python okno-pipeline:rebuild \
    -m scripts.region_probe --timings "$timings" -- "$@"
}
probe bridge runtime/acquire-timings.json --region pipeline/regions/gelendzhik.yaml \
  --terrain-bbox 37.65 44.25 38.50 44.90 --to-stage fetch_osm
probe bridge runtime/build-timings.json --region pipeline/regions/gelendzhik.yaml \
  --terrain-bbox 37.65 44.25 38.50 44.90 \
  --from-stage fetch_overture --to-stage compute_views --workers 1
probe none runtime/final-timings.json --region pipeline/regions/gelendzhik.yaml \
  --terrain-bbox 37.65 44.25 38.50 44.90 \
  --from-stage compute_runtime_cache --cache-hotel-id osm-node-1922177387
```

Это последовательные диапазоны **того же rebuild DAG** с готовыми выходами предыдущего диапазона. Все 14 стандартных стадий выполнены; optional `compact_geo` по умолчанию выключен и здесь не запускался. После `compute_views` выбран реальный ID с сохранёнными фасадами; default cache IDs Сочи не использовались. Сохранён только один LOS cache, а не кэши всех 30 отелей.

Дополнительная проверка выполнялась с теми же read-only mounts к `pipeline`/`scripts`, writable mount только для отчёта в `runtime`, и следующими параметрами:

```sh
docker run --rm --platform linux/amd64 --network none --read-only --tmpfs /tmp \
  --mount "type=bind,src=$PWD/data,dst=/app/data,readonly" \
  --mount "type=bind,src=$PWD/pipeline,dst=/app/pipeline,readonly" \
  --mount "type=bind,src=$PWD/scripts,dst=/app/scripts,readonly" \
  --mount "type=bind,src=$PWD/runtime,dst=/app/runtime" \
  --entrypoint python okno-pipeline:rebuild -m scripts.region_probe \
  --timings runtime/readonly-timings.json -- --region pipeline/regions/gelendzhik.yaml \
  --terrain-bbox 37.65 44.25 38.50 44.90 --validate-only
mv data runtime/gelendzhik-data
mv runtime/initial-data-snapshot data
```

## Время, CPU, память и загрузки

`region_probe` оборачивает существующий `rebuild.execute_stage`, не подменяя вычисления. Wall time — `perf_counter`; CPU — user + system процесса и завершённых дочерних процессов. Полные timestamps и отдельные времена 30 отелей находятся в [JSON](region_gelendzhik_timings.json).

| Стадия | Wall, с | CPU, с |
|---|---:|---:|
| fetch_osm, cached PBF | 567.786 | 581.018 |
| fetch_overture | 73.815 | 128.737 |
| merge_hotels | 5.872 | 5.831 |
| fetch_dem + elevations графа | 8.874 | 6.830 |
| build_dsm | 2.423 | 2.372 |
| fetch_open | 15.673 | 0.724 |
| fetch_ookla | 30.161 | 8.603 |
| fetch_tiles | 35.548 | 2.429 |
| export_seed | 1.611 | 1.577 |
| compute_views | 373.247 | 372.667 |
| compute_runtime_cache, 1 отель | 18.217 | 20.379 |
| compact_seed | 0.810 | 0.789 |
| validate | 4.399 | 4.371 |
| compute_validate | 2.493 | 2.414 |
| **Сумма стадий** | **1140.931** | **1138.741** |

Подготовка региона до geo: **741.765 с**. Отдельная read-only валидация после прогона: 5.018 + 2.508 с, в сумму выше не включена. Максимальный `ru_maxrss` процесса за его жизнь: **1808293888 B**; максимум среди завершённых дочерних процессов: **295546880 B**. Эти значения не означают дополнительную память каждой стадии и не складываются в измеренный общий пик контейнера.

- Cached: PBF **316601771 B**, новый сетевой download PBF **0 B**.
- Новые сохранённые HTTP bodies: **61266872 B**, включая DEM **41592727 B** и проверенный по pinned publisher SHA go-pmtiles archive **17444324 B**.
- Сохранённые результаты bbox SQL: **12785670 B** (Overture + пустые Ookla JSON). Это **не размер удалённого чтения** мировых Parquet.
- Счётчик `eth0` свежего source-контейнера после всех fetch-стадий: RX **844037968 B**, TX **9001631 B**. Включает remote range reads, протокольные расходы и extensions; не заявляется точным размером исходных датасетов.
- Итоговые файлы: raw **448457411 B** (включая общий hardlink PBF и распакованную утилиту), seed **4719257 B**, geo **56522996 B**, tiles **4444469 B**. Все экспортируемые seed/tiles/geo файлы меньше 50 MB. PMTiles: **3329038 B**.

## Полнота и неизвестные значения

OSM: 184 кандидата. Overture: 156 places, 24270 buildings; 5501 footprint добавлен после проверки перекрытий OSM. После консервативного merge 298 кандидатов; выбраны 30, по 15 на A/B, все выбранные ID — OSM. Identity audit и исходные source records сохранены. Buildings: **24367**, из них 7 с `height`, 1969 с `levels`, **22391 unknown** (91.9%). В исходных слоях неизвестная высота остаётся `null`; геомодель разыгрывает явно указанную эмпирическую prior, а не выдаёт её за измерение.

Граф: **36860 узлов, 80924 направленных ребра**, DEM покрывает все узлы; 61 слабая компонента. Слои: 3952 POI, 6400 дорог, 0 железных дорог, 37 пляжей, 16 coastline ways. Ноль в слое описывает выгрузку, а не доказанную полноту источника.

| Claims | Количество |
|---|---:|
| sea_view | 184 |
| sunset_view, включая месяцы и scopes | 1833 |
| mountain_view | 198 |
| quiet_night, наружная модель | 132 |
| beach_walk | 29 |
| climate | 360 |
| air_quality | 30 |
| nearby_photos | 28 |

В карточках этажность известна у **21/30**, у 9 неизвестна. В geo `floors.count` непуст у 22, но один дополнительный случай получен от ближайшего здания и не подтверждает этажность отеля. У 8 `count=null`: моделируется только условный уровень глаза 1.5 м, верхние этажи неизвестны. `unknown_characteristics=[]` сам по себе не означает, что этажность известна.

28 привязок — точный building ID. Две — `nearest_30m`: «Приморье Grand Resort Hotel», `osm-node-6813957785`, 2.15 м до выбранного здания; Alean Family Biarritz, `osm-way-444802651`, 20.21 м. Эти фасады/этажи требуют проверки идентичности здания. У «Самары», `osm-node-494318107`, `beach_walk` unknown: доступный направленный граф не нашёл маршрут; это не утверждение о физической недоступности пляжа.

Модель выдала положительный sea_view хотя бы на одном уровне/фасаде у 20 отелей; порог Monte Carlo P ≥ 0.8 есть у 13. Это описательная статистика модели. Ookla fixed/mobile дали 0 covering tiles: source не зарегистрирован как used, hotel connectivity claims отсутствуют, скорость неизвестна. Точных Wikidata links у выбранных OSM объектов нет: building history неизвестна. Commons: 100 записей с индивидуальным автором/лицензией, **0 offline thumbnails** по конфигу; фото не использованы как подтверждение вида. Цены, типы номеров, окна и шумоизоляция не заполнялись.

Registry: 11 записей, 10 внешних, **6 независимых семейств**: OSM/Geofabrik, Overture, Copernicus, Open-Meteo, Commons, Protomaps. Реальные source URLs, даты получения, версии, bbox, SQL, SHA и лицензии сохранены в экспериментальном `seed/manifest.json`. OSM/Overture buildings — ODbL, Overture places — per-record CDLA/Apache/CC0, Copernicus public licence, Open-Meteo CC BY 4.0, Commons per-file, Protomaps data ODbL и отдельные лицензии утилиты/шрифтов/sprites. OTA и ключи не использовались.

## Три примера sea_view

Все три примера имеют точный building ID, известную исходную этажность и `synthetic=false`. Scopes относятся к фасаду и диапазону моделируемых этажей. Высота глаза: `base+(floor−1)*3+1.5` м. Общий алгоритм не менялся: ближний DSM 2 м / 1200 м, дальний DSM 30 м / 30 км, азимуты 3°, сектор ±75°, 64 Monte Carlo реализации, порог моря 10°. `probability` — средняя модельная вероятность по scope; `confidence` дополнительно учитывает качество входов. Ни одно из этих чисел не является долей правильно предсказанных реальных окон.

| Отель / OSM ID | Scope и результат модели | Confidence claim | Evidence |
|---|---|---:|---|
| [Плаза](https://www.openstreetmap.org/node/1922177387), 6 этажей | W, этажи 2–6: `available=true`, средняя P 0.7844, ширина 43°. Детерминированный порог 2; P80 порог 4 | 0.6275 | `/data/geo/osm-node-1922177387.json`, claim `geo-1644db5d5588eef8d188` |
| [Малахит](https://www.openstreetmap.org/node/11067209470), 3 этажа | SW, этажи 2–3: `available=true`, средняя P 0.8281, ширина 71°. Детерминированный порог 2; P80 порог 3 | 0.5383 | `/data/geo/osm-node-11067209470.json`, claim `geo-75623f1184e08413b331` |
| [Золотая лоза](https://www.openstreetmap.org/node/1219433529), 2 этажа | SW, только этаж 2: `available=true`, P 0.4531, ширина 22°. P80 порога нет: слабая гипотеза | 0.2891 | `/data/geo/osm-node-1219433529.json`, claim `geo-8649e83eeccb80217081` |

У «Плазы» P по этажам 1–6 на W: `[0,0.296875,0.640625,0.984375,1,1]`; на N детерминированного моря нет на всех 6 уровнях. На S этажи 2–6 имеют P 1 и confidence 0.775. У «Малахита» SW по этажам: `[0,0.65625,1]`, NW даёт море только на 3-м уровне с P 1, но confidence всего 0.4 из-за покрытия высот. У «Золотой лозы» SW: `[0.015625,0.453125]`; детерминированное `available=true` не делает этот результат надёжным обещанием клиенту.

Физические файлы лежат в `runtime/gelendzhik-data/geo/<id>.json` и `<id>.npz`; соседние JSON содержат scopes, blocking IDs, `input_sha256`, модели высот, ограничения и claims. В [машинном отчёте](region_gelendzhik_timings.json) сохранены выбранные claim values, scopes, confidence, массивы вероятностей и SHA-256 этих snapshot. Это локальные экспериментальные evidence, не дополнение основного production seed.

## Осторожная оценка на 1000 отелей

Из 373.247 с compute stage **7.418 с** занимает загрузка региона; сумма отдельных hotel calls **364.615 с**. Первый холодный отель — **22.566 с**, включает JIT и собственную геометрию: это не отдельный замер startup. Остальные 29: среднее **11.795 с**, медиана **11.035 с**, p90 **17.416 с**, диапазон 5.529–19.979 с.

Для аналогичной смеси 1–7 моделируемых этажей и 3–7 фасадов, того же железа и 1 worker линейная экстраполяция среднего даёт **3.28 ч на 1000 hotel calls**; бюджет по p90 — **4.84 ч**. Отдельно добавляются подготовка региона (здесь 12.36 мин с cached PBF), загрузка нового PBF/образа при отсутствии кэша, валидация и нужные LOS caches. Одна построенная LOS cache заняла 18.217 с вместе с инициализацией региона; строить все 1000 caches и их амортизированное время не измерено. Ускорение от нескольких workers не измерялось.

30 пар hotel JSON/NPZ занимают **43798223 B**; при той же смеси это около **1.46 GB на 1000**, отдельно от общей сетки, seed, карты, raw inputs и LOS caches. Измеренный максимум lifetime RSS одного процесса 1.81 GB — ориентир для этого bbox, а не гарантия памяти для большего города. Больший bbox, плотность зданий, высотные отели, неполные данные и native execution могут заметно изменить время/качество. Это оценка порядка затрат, не прогон 1000 объектов и не SLA.

## Параметризация и проверки

Две минимальные параметризации устраняют ограничения переноса: `validate` теперь требует точную сумму `CONFIG.zones[*].hotel_limit` вместо константы 150–250; нормализация имён в `merge_hotels` удаляет транслитерированное `CONFIG.name` вместо одного `sochi`. Fallback адреса уже использовал `CONFIG.name`. Дополнительно `rebuild` принимает короткий ID через `REGION=gelendzhik` или `--region gelendzhik`, разрешая его в `pipeline/regions/gelendzhik.yaml` до импорта стадий; явные пути и default Сочи сохранены. Оба коротких варианта проверены read-only/network-none dry-run без data mounts: одинаковый план на 30 отелей и 14 стадий, default Сочи — 250. Дополнительных пересчётов не было. Остальные provenance/graph/hash/geometry/size guards не ослаблялись, форматы Source/Hotel/Claim/NPZ не менялись.

Оставшиеся зависимости переноса: pipeline ожидает зоны с ключами A/B; EPSG:32637 зафиксирован и подходит обоим этим регионам, но не произвольному миру; для нового региона нужен явный terrain bbox и корректные DEM tiles; default cache IDs принадлежат Сочи, поэтому нужен явный ID или исключение cache stage. В `fetch_open` остаётся приоритет названий фото Сочи для offline thumbnails; при их количестве 0 он не влияет на результат этого эксперимента. Это не доказательство полностью автоматического глобального выбора проекции, береговой маски и портфеля.

Проверки прошли: Hotel/Source/Claim модели, 30 уникальных ID и квоты A/B, graph topology/access и DEM узлов, evidence existence, полные manifest и geo input hashes, numeric-only NPZ с `allow_pickle=False`, лимиты файлов, representative PMTiles, LOS baseline hash. `ruff check` и `py_compile` изменённых Python файлов прошли. Отдельная read-only/network-none регрессия исходного snapshot Сочи с изменёнными валидатором/normalizer также прошла: **250 отелей, 20340 geo claims, 2 LOS caches**. Повторная read-only проверка Геленджика прошла: 30 / 2376 / 1. Изменения не merged и не pushed; история не переписывалась.
