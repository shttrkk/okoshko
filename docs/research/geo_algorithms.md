# Гео-вычисления характеристик отелей: вид, закат, шум, пешая доступность (методы, формулы, библиотеки, валидация)

Контекст: кейс Островка ([materials/CASE.md](/Users/shttrkk/ostrovok-hack/materials/CASE.md)) требует, чтобы хотя бы одна характеристика вычислялась по геоданным, а не находилась в тексте. Жюри прямо отвергает вариант «спросить LLM, есть ли вид на море». Целевой формат вывода: «закат из окон западной стороны, с 5-го этажа, апрель — август». План: офлайн-предрасчёт для 100–500 отелей одного региона, результаты лежат в репозитории, рантайм — Docker на Python.

Версии библиотек ниже сверены с PyPI JSON API 26.10.2026 (собственная проверка). Замеры производительности — собственные: Apple M4, 10 ядер, numba 0.68.0, numpy 2.5.4. Код бенчмарка: `/private/tmp/claude-501/-Users-shttrkk/39778f38-8c50-4686-8954-dff5ab3d7924/scratchpad/bench_rays.py`.

---

## 1. Построение DSM: DEM (Copernicus GLO-30 / SRTM) + здания (OSM / Overture), разрешение, собственное здание отеля

### Takeaway
Рабочая схема — двухмасштабная. Ближняя зона радиусом 2–4 км — локальный растр 1–2 м: рельеф плюс растеризованные здания (высота из `height`, иначе из `building:levels × 3 м`, иначе из распределения-заглушки). Дальняя зона до 30–50 км (горизонт моря, горы) — Copernicus GLO-30 без зданий. Главная ловушка: Copernicus — это уже DSM (крыши и деревья «размазаны» в 30-м пиксель), поэтому при наивном сложении DEM и высот OSM здания учитываются дважды.

### Cited Findings
- Copernicus DEM — это **Digital Surface Model**: он описывает поверхность Земли вместе со зданиями, инфраструктурой и растительностью — [Copernicus Data Space, COP-DEM](https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM).
- Точность GLO-30 по спецификации: абсолютная вертикальная < 4 м (LE90); относительная < 2 м на уклонах до 20% и < 4 м на более крутых — [Copernicus Data Space](https://dataspace.copernicus.eu/node/1306).
- В продукте есть попиксельная маска ошибки высоты (Height Error Mask, HEM). Это стандартное отклонение, выведенное из интерферометрической когерентности и геометрии съёмки — [Google Earth Engine catalog](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_DEM_GLO30).
- Высоты GLO-30 отсчитываются от геоида EGM2008 (EPSG:3855) — [Google Earth Engine catalog](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_DEM_GLO30).
- GLO-30 лежит в открытом S3: бакет `copernicus-dem-30m`, регион `eu-central-1`, доступ с `--no-sign-request` (аккаунт AWS не нужен), лицензия Copernicus — [AWS Registry of Open Data](https://registry.opendata.aws/copernicus-dem/).
- OSM: обычная высота этажа — 3 м, и 3D-рендеры берут её по умолчанию, если высоты нет. `building:levels` считает только надземные этажи без крыши, а `height` включает крышу — [OSM Wiki Key:building:levels](https://wiki.openstreetmap.org/wiki/Key:building:levels); [OSM Wiki Simple 3D Buildings](https://wiki.openstreetmap.org/wiki/Simple_3D_buildings).
- Simple 3D Buildings: высота = этажи × средняя высота этажа (по умолчанию 3,0 м), множитель можно менять в зависимости от типа здания — [OSM Wiki Simple3DBuildingsV1](https://wiki.OpenStreetMap.org/wiki/Simple3DBuildingsV1).
- Прецедент дефолтов из открытого кода (NoiseModelling `Import_OSM.groovy`). Без тегов высота здания = `4.0 + U(0; 2.1)` м. При `building:levels` высота = `levels × 3.0 + U(0; 2.1)` м. Тег `height` имеет приоритет — [NoiseModelling Import_OSM.groovy (GitHub)](https://github.com/Universite-Gustave-Eiffel/NoiseModelling/blob/main/noisemodelling-scripts/src/main/groovy/org/noise_planet/noisemodelling/scripts/Import_and_Export/Import_OSM.groovy).
- Overture buildings: в релизе 2023-07-26-alpha высоту имели 27 871 758 из 785 524 851 зданий (≈3,5%, мой расчёт), из них не меньше 10 млн — из USGS lidar — [OMF Webinar (Linux Foundation)](https://project.linuxfoundation.org/hubfs/Overture%20Maps/OMF%20Webinar.pdf). Первый релиз высот покрывал больше 6 млн зданий по USGS 3DEP lidar (> 34 000 км²) — [Overture technical spotlights](https://overturemaps.org/resources/technical-spotlights). OSM — источник с высшим приоритетом конфляции, около 635 млн footprints — [Overture docs: buildings](https://docs.overturemaps.org/guides/buildings).
- Магистерская работа по Вене: уровень детализации 3D-модели заметно меняет рассчитанные виды; для гедонических моделей рекомендован LoD1 (призмы) — [UNIGIS thesis 1452](https://unigis.at/files/Mastertheses/Full/1452.pdf).
- Для анализа видов рекомендуют DSM, а не bare-earth DEM: DSM учитывает деревья и здания, которые закрывают вид — [URI, digitalcommons](https://digitalcommons.uri.edu/enre_facpubs/200).
- Собственный замер: `rasterio.features.rasterize` 20 000 прямоугольных footprints в сетку 2000×2000 при 2 м занял **0,18 с** (rasterio 1.5.2, shapely 2.2.0).

### Inferences
- **Двойной учёт зданий.** В городе Copernicus над кварталом уже приподнят на часть высоты застройки. Варианты: (a) брать «землю» под зданием как низкий перцентиль (например, 10-й) DEM в буфере 30–60 м вокруг footprint (грубая морфологическая оценка DTM); (b) использовать bare-earth производную Copernicus (например, FABDEM; лицензию проверить, см. Gaps); (c) явно заложить ±2–5 м в неопределённость. Для морских курортов (здания на склоне к морю) лучше (a) плюс Monte Carlo.
- **Разрешение.** 30 м не различает улицы-каньоны шириной 10–20 м: любой вид «между домами» на 30-м растре неверен. 5 м — пограничный вариант (узкие разрывы между домами схлопываются). 1–2 м — правильно для ближней зоны. Объём: область 10×10 км при 2 м = 25 млн ячеек ≈ 100 МБ float32. Это помещается в память и в git LFS, но лучше хранить исходники (footprints + DEM-тайлы) и строить DSM при сборке.
- **Море.** В DSM задать воду = 0 м по маске моря (полигоны воды или береговой линии OSM), а не доверять шумным значениям DEM над водой. Маску моря хранить отдельным boolean-растром на той же сетке: она нужна для метрик вида.
- **Высоты без тегов.** Брать не константу, а эмпирическое распределение высот размеченных зданий того же типа (`building=apartments/house/hotel/...`) в регионе: оно станет источником для Monte Carlo (раздел 9). Если высоты есть в Overture, использовать их с приоритетом `height` OSM > Overture > `levels` × U(2.8; 3.5) + крыша U(0; 2) > распределение.
- **Собственное здание отеля.** Отель в OSM часто точка (`tourism=hotel` node). Footprint — полигон `building`, содержащий точку, иначе ближайший в пределах ~30 м; результат пометить флагом точности привязки. Собственное здание в DSM **оставляем** (другие крылья Г-образного здания действительно закрывают вид). Наблюдатель ставится на 1 м наружу от фасада, а луч начинается с `start_dist ≈ 1.5 × cell`, чтобы первый пиксель крыши самого отеля не «закрыл» всё. Лучи берутся только в секторе ±60–75° от внешней нормали, поэтому сразу уходят от здания.

**Псевдокод построения DSM (ближняя зона):**
```python
# deps: rasterio>=1.4 (manylinux wheels с собственным GDAL), shapely>=2, geopandas>=1, pyproj>=3.7, numpy
def build_dsm(bbox_utm, cell=2.0):
    dem = warp_to_grid("copernicus_glo30.vrt", bbox_utm, cell, resampling="bilinear")  # EGM2008
    sea = rasterize(osm_sea_polygons, grid) > 0
    dem[sea] = 0.0
    ground = dem.copy()
    bld = load_buildings(osm_pbf, overture_parquet)            # GeoDataFrame в UTM
    bld["h"], bld["h_src"] = resolve_height(bld)               # height | overture | levels*3 | prior
    # оценка земли под зданием: 10-й перцентиль DEM в буфере 40 м (снимаем "размазанные" крыши)
    bld["base"] = zonal_percentile(dem, bld.buffer(40).difference(bld.unary_union), q=10)
    bld = bld.sort_values("h")                                 # высокие перезаписывают низкие
    top = rasterize(zip(bld.geometry, bld.base + bld.h), grid, fill=np.nan)
    dsm = np.where(np.isnan(top), ground, np.maximum(top, ground))
    bid = rasterize(zip(bld.geometry, bld.index), grid, fill=-1)  # id здания в пикселе: для «кто закрыл вид»
    return dsm, sea, bid
```

### Gaps
- Не нашёл свежих (2025–2026) данных о доле зданий с высотой в Overture и OSM по России и курортным регионам. Её стоит посчитать самим по выгрузке региона: это важный вход для доверия.
- Не нашёл независимой валидации ошибки GLO-30 именно над городской застройкой: доступна только спецификация < 4 м LE90.
- FABDEM (bare-earth на базе Copernicus) по памяти распространяется под некоммерческой лицензией. В этой сессии я её не проверял; перед использованием прочитать лицензию.
- Наличие слоя Water Body Mask в выдаче GLO-30 на AWS не подтвердил источником.

---

## 2. Viewshed / line-of-sight: GDAL, xarray-spatial, WhiteboxTools, richdem, свой numba ray casting; кривизна Земли и рефракция

### Takeaway
Для задачи «много наблюдателей × этажей × узкие азимутальные секторы × метрики по лучам» лучше всего подходит **свой radial ray casting на numba**: десятки строк кода, микросекунды на луч, полный контроль над кривизной, маской моря и id зданий-блокеров. GDAL viewshed полезен как эталон для проверки и для картинок «зона видимости». Начиная с GDAL 3.12 в нём есть ограничение сектора азимута и pitch, но Python-пакет `gdal` на PyPI не имеет wheels (нужен образ `osgeo/gdal` или conda).

### Cited Findings
- `gdal_viewshed` появился в GDAL 3.1.0 и реализует Wang et al. (2000) «Generating Viewsheds without Using Sightlines». Параметры: `-oz` — высота наблюдателя над DEM (по умолчанию 2), `-tz` — высота цели (0), `-md` — макс. дистанция, `-cc` — коэффициент кривизны. Режимы: `NORMAL`, `DEM`, `GROUND` и `ACCUM` (кумулятивный по сетке наблюдателей `-os`, `-j` потоков) — [GDAL gdal_viewshed](https://gdal.org/en/stable/programs/gdal_viewshed.html).
- `-cc`: CurvCoeff = 1 − RefractionCoeff. По умолчанию 0,85714 (≈ 1 − 1/7) для земной CRS с GDAL 3.4, 1,0 для неземных с 3.6 — [GDAL gdal_viewshed](https://gdal.org/en/stable/programs/gdal_viewshed.html). Результаты осмысленны только в проекционной CRS — [GDAL docs (man page)](https://www.gdal.org/programs/gdal_viewshed.html).
- Новый CLI `gdal raster viewshed` (с GDAL 3.11) в 3.12 получил `--start-angle`/`--end-angle` (сектор азимута по часовой от севера), `--high-pitch`/`--low-pitch`, `--min-distance`, а также `--sd-filename` (растр стандартных отклонений высот) — [GDAL gdal raster viewshed](https://gdal.org/en/stable/programs/gdal_raster_viewshed.html).
- Python-сигнатура (SWIG): `gdal.ViewshedGenerate(srcBand, driverName, targetRasterName, creationOptions, observerX, observerY, observerHeight, targetHeight, visibleVal, invisibleVal, outOfRangeVal, noDataVal, dfCurvCoeff, mode, maxDistance, callback=None, callback_data=None, heightMode=GVOT_NORMAL, options=None)`. Перечисления: `GVM_Diagonal/Edge/Max/Min`, `GVOT_NORMAL/MIN_TARGET_HEIGHT_FROM_DEM/MIN_TARGET_HEIGHT_FROM_GROUND` — [GDAL swig/include/Operations.i](https://github.com/OSGeo/gdal/blob/master/swig/include/Operations.i).
- PyPI `gdal` 3.13.3 (18.08.2026) — **0 wheel-файлов**, только sdist (собственная проверка PyPI JSON). Для сравнения: `rasterio` 1.5.2 имеет manylinux wheels, но viewshed не предоставляет — [PyPI gdal](https://pypi.org/project/GDAL/), [PyPI rasterio](https://pypi.org/project/rasterio/).
- `xrspatial.viewshed(raster, x, y, observer_elev=0, target_elev=0, max_distance=...)` возвращает для видимых ячеек вертикальный угол (0 — вниз, 90 — горизонт, 180 — вверх), остальные — `INVISIBLE`. Бэкенды CPU, GPU и dask используют разные алгоритмы и могут слегка расходиться — [xarray-spatial docs](https://xarray-spatial.readthedocs.io/en/stable/reference/_autosummary/xrspatial.viewshed.viewshed.html). Версия 0.10.19 (07.10.2026), wheel `py3-none-any`, Python ≥ 3.12 (PyPI).
- `richdem` — последний релиз 0.3.4 от 13.07.2018 (PyPI), то есть пакет фактически не поддерживается. `whitebox` (Python-обёртка WhiteboxTools) — 2.3.6 от 23.02.2025 (PyPI).
- Дальность горизонта: геометрическая s ≈ 3,57·√h км (h в метрах); с учётом стандартной рефракции s ≈ 3,86·√h км, что соответствует эффективному радиусу Земли ≈ 7440 км. Видимость объекта высотой h₂: D ≈ 3,86·(√h₁ + √h₂). Рефракция заметно меняется день ото дня — [SDSU: Distance to the horizon](https://aty.sdsu.edu/explain/atmos_refr/horizon.html); [mathscinotes](https://www.mathscinotes.com/2013/08/distance-to-the-horizon-assuming-refraction/); [ScienceABC](https://www.scienceabc.com/pure-sciences/how-far-away-are-you-from-the-horizon-when-you-are-looking-at-it-from-a-beach.html).
- Собственный замер numba ray casting (код в разделе 3): 5 точек фасада × 10 этажей × 61 азимут (±60°, шаг 2°), луч 3,9 км с шагом 2 м по растру 2000×2000 @ 2 м → **2,9–3,7 мс на фасад**. Дальняя зона: 1 точка × 10 этажей × 361 азимут × 30 км по 30-м растру → **0,3 мс**. Первая JIT-компиляция ≈ 0,5–0,8 с (кешируется через `cache=True`).

### Inferences
- **Формула кривизны** для луча: `z_eff(d) = z(d) − d²/(2R) · cc`, где R = 6 371 000 м, cc = 0,85714 (как в GDAL). Падение: 1 км — 0,07 м; 5 км — 1,7 м; 10 км — 6,7 м; 20 км — 26,9 м; 30 км — 60,5 м (мой расчёт). Внутри города (< 3 км) кривизной можно пренебречь, для горизонта моря и гор (10–50 км) она обязательна.
- **Горизонт моря** для глаза на h: 2 м → 5,5 км; 10 м → 12,2 км; 20 м → 17,3 км; 50 м → 27,3 км; 100 м → 38,6 км (по 3,86·√h). Угол понижения горизонта ≈ √(2h/R): для 20 м ≈ 0,14°, для 100 м ≈ 0,32° (мой расчёт). Отсюда максимальная длина луча для моря: 30–40 км.
- **Чем рисковать не надо.** xarray-spatial не документирует параметр кривизны: для дальних лучей придётся вычитать `d²/2R·cc` из растра под каждого наблюдателя. GDAL ViewshedGenerate считает полный растр на одного наблюдателя, поэтому для N точек × M этажей это N×M вызовов с полным I/O; узкий сектор доступен только с 3.12. WhiteboxTools скачивает бинарник при первом запуске (лишняя сетевая зависимость в Docker).
- **Рекомендация.** Свой ray casting на numba как основной метод. `gdal raster viewshed` (образ `ghcr.io/osgeo/gdal:ubuntu-small-3.12+`) — для 3–5 отелей как кросс-проверка (раздел 10) и для визуализации карты видимости на демо.

### Gaps
- Не нашёл опубликованных бенчмарков скорости `gdal_viewshed` на растрах 2000×2000 и больше; производительность GDAL для нашего сценария не измерял (нет системного GDAL локально).
- Не подтвердил, учитывает ли xarray-spatial кривизну Земли внутри алгоритма: документация параметр не упоминает.

---

## 3. Пофасадный и поэтажный анализ: фасады из footprint, нормали, точки наблюдения, сектор окна

### Takeaway
Фасады выводятся из полигона здания: кольцо ориентируется против часовой, у каждого ребра берётся внешняя нормаль, близкие по азимуту рёбра сливаются, затем на каждом фасаде через 5–10 м ставятся точки на 1 м снаружи. Для каждой точки и этажа пускаются лучи в секторе ±60° от нормали. Итог хранится как матрица `facade × floor × azimuth`, из которой получаются фразы «море видно с ЮЗ-фасада начиная с 4-го этажа, 35° горизонта».

### Cited Findings
- Пофасадная и поэтажная видимость — устоявшийся приём в гедонических исследованиях: в Йокогаме viewshed считали **для каждого этажа каждого кондоминиума** по классификации земель 0,5 м, и вид на океан значимо влиял на цену — [Yamagata, CUPUM 2013](https://cupum2013.geo.uu.nl/download/usb/contents/windows/pages/..\..\pdf\shortpapers\62_Yamagata.pdf).
- NoiseModelling ставит фасадные приёмники в 50 см вокруг фасадов и связывает их со зданием через `BUILD_PK`. Тот же принцип «точка снаружи фасада» используется в акустике — [NoiseModelling: Input receivers](https://noisemodelling.readthedocs.io/en/latest/Input_receivers.html).
- Готового open-source пакета для поэтажного и пофасадного вида из окна я не нашёл. Ближайшее — скрипты автоматизации viewshed от зданий на QGIS/GRASS — [Viewshed-automation-workflow (GitHub)](https://github.com/lucasdemassy/Viewshed-automation-workflow).
- Собственный тест на синтетике: доля азимутов с видимым морем по этажам для одной точки = [0, 0, 0, 0, 0, 0,25, 0,26, 0,28, 0,33, 0,33]. Метод естественно даёт порог «с 6-го этажа».

### Inferences
- **Нумерация этажей.** В России «1-й этаж» = уровень земли, в Европе «1st floor» = первый над землёй. Формула глаза: `z_eye = base + (floor − 1) · h_floor + 1.5 м` при российской нумерации; h_floor ≈ 3,0 м (жилое) или 3,3–3,6 м (отель, 1-й этаж часто 4–5 м). Неизвестную высоту первого этажа учесть в Monte Carlo.
- **Сектор окна.** Физически из окна видно примерно ±60–70° от нормали (с балкона — до ±90°). Брать ±60° как «вид из окна», ±90° как «с балкона». Это допущение, источника нет; держать параметром.
- **Слияние рёбер.** Рёбра короче 3 м отбрасывать; соседние с разницей азимутов < 20° сливать; классифицировать в 8 румбов (С, СВ, ..., СЗ). Для фразы пользователю использовать румб, для вычислений — точный азимут нормали.
- **Номер этажа.** Если `building:levels` неизвестен, а отель занимает часть здания, этажи номеров спросить у отельера. Это напрямую соответствует задаче кейса «вопрос отельеру».

**Псевдокод фасадов и сканирования:**
```python
from shapely.geometry.polygon import orient
import numpy as np, math

def facades(poly, min_len=3.0, merge_deg=20.0):
    ring = list(orient(poly, sign=1.0).exterior.coords)   # CCW
    edges = []
    for (x1, y1), (x2, y2) in zip(ring[:-1], ring[1:]):
        dx, dy = x2 - x1, y2 - y1; L = math.hypot(dx, dy)
        if L < min_len: continue
        nx, ny = dy / L, -dx / L                           # внешняя нормаль для CCW
        az = (math.degrees(math.atan2(nx, ny)) + 360) % 360   # по часовой от севера
        edges.append(dict(p1=(x1, y1), p2=(x2, y2), len=L, az=az, n=(nx, ny)))
    return merge_collinear(edges, merge_deg)               # объединить соседние с |Δaz| < 20°

def sample_points(f, step=8.0, offset=1.0):
    k = max(1, int(f["len"] // step))
    ts = (np.arange(k) + 0.5) / k
    p = np.array(f["p1"]) + np.outer(ts, np.subtract(f["p2"], f["p1"]))
    return p + offset * np.array(f["n"])

# numba-ядро (проверено в бенчмарке):
@njit(cache=True)
def cast_ray(dsm, sea, x0, y0, z0, az_deg, step, cell, max_dist, start_dist, cc=0.85714):
    az = math.radians(az_deg); dx, dy = math.sin(az), -math.cos(az)  # строка 0 = север
    max_tan = -1e9; vis_sea = 0; first_sea = -1.0; d = start_dist
    while d < max_dist:
        c = x0 + dx*d/cell; r = y0 + dy*d/cell
        ic, ir = int(c), int(r)
        if ic < 0 or ir < 0 or ic >= dsm.shape[1] or ir >= dsm.shape[0]: break
        z = dsm[ir, ic] - d*d/(2*6371000.0)*cc
        t = (z - z0)/d
        if t >= max_tan:                    # ячейку не закрывает ничто ближе
            if sea[ir, ic]:
                vis_sea += 1
                if first_sea < 0: first_sea = d
            max_tan = t
        d += step
    return math.degrees(math.atan(max_tan)), vis_sea, first_sea   # угол горизонта, кол-во видимых морских ячеек, глубина

@njit(parallel=True, cache=True)
def facade_scan(dsm, sea, pts, z0s, az_center, half_fov, n_az, step, cell, max_dist, start_dist):
    # -> horizon[pt, floor, az], seacnt[pt, floor, az]; prange по (pt, floor)
    ...
# Агрегация: sea_visible[floor, az] = any_pt(seacnt > 0) или доля точек;
# min_floor_sea = min floor, где доля азимутов с морем >= порога.
```
- Для полноты дальний горизонт (> 3–4 км) считать вторым проходом по 30-м DEM с той же функцией: на стыке взять `max(max_tan_near, max_tan_far)`.
- Хранить для каждого `(facade, floor, az)` id первого блокирующего здания (из растра `bid`). Это даёт объяснимость («вид закрывает дом по ул. X, 9 этажей») и триггер актуализации: если здание изменилось в OSM, пересчитать.

### Gaps
- Нет открытых данных о реальном положении окон, балконов и типах номеров. Распределение «какие номера на каком фасаде» придётся спрашивать у отельера или выводить из фото.

---

## 4. Метрики вида: море (доля, угловая ширина), горы (видимые вершины), достопримечательность (LOS до POI)

### Takeaway
Метрики берутся из гедонической литературы: **ширина** (угол горизонта с морем), **глубина** (расстояние до ближайшей видимой воды и видимая площадь), **максимальный непрерывный сегмент**. Для гор — видимые вершины `natural=peak` с LOS до точки вершины и угловая доля «горного» горизонта выше порога. Для достопримечательности — LOS до нескольких точек по высоте объекта: «видна целиком / верхняя часть / не видна».

### Cited Findings
- Gillespie, Lyons, Hynes (Land Economics, 2023): мера **ширины и глубины** вида на море через GIS 3D-симуляцию, факт наличия вида — через NLP по текстам объявлений. Выборка — более 100 тыс. листингов в Ирландии за 2014–2019; средняя премия за вид на море 8,1%, за широкий вид до 15% — [Land Economics abstract](https://le.uwpress.org/content/early/2023/10/09/le.100.2.071522-0056R.abstract).
- Hindsley, Hamilton, Morgan: по LiDAR строили 4 непрерывные меры вида — total view, maximum view segment, mean view segment, proximity to view content — [Appalachian State WP 11-06](https://econ.appstate.edu/RePEc/pdf/wp1106.pdf).
- Хайфа (Dai, Felsenstein, Grinberger): видимость моря и зелени повышает стоимость даже с контролем на близость — [TAU paper](https://www.alrov.sites.tau.ac.il/_files/ugd/bb244f_f9ae0850d7c947f7842d57217e57a526.pdf).
- В GDAL есть режим `DEM`/`GROUND`: он выдаёт минимальную высоту цели, при которой ячейка видна. Это готовый способ ответить «с какой высоты будет видна башня» — [GDAL gdal_viewshed](https://gdal.org/en/stable/programs/gdal_viewshed.html).

### Inferences
- **Метрики моря** (на фасад × этаж, сектор ±60°, шаг 1–2°):
  - `sea_width_deg` = число азимутов, где луч дошёл до видимой морской ячейки, × шаг. Это главная метрика: «видно 35° моря».
  - `sea_fraction` = sea_width / ширина сектора.
  - `max_sea_segment_deg` — самый длинный непрерывный кусок (аналог maximum view segment); отличает «панораму» от «щели между домами».
  - `sea_depth_m` — медиана `first_sea` по лучам с морем («до моря 300 м»).
  - `sea_solid_angle` ≈ Σ по видимым морским ячейкам (cell² · sinθ / d²) — видимая угловая площадь; вес дальней воды у горизонта мал. Опционально.
  - Классы: «панорамный» (ширина ≥ 60°), «вид на море» (≥ 15° и сегмент ≥ 10°), «боковой / частичный» (3–15°), «нет» (< 3°). Пороги подобрать на разметке (раздел 10).
- **Горы:** (1) вершины из OSM `natural=peak` с `ele` в радиусе 50 км: LOS-тест до точки (вершина, ele) с кривизной, плюс угловая высота над горизонтом ≥ 1°; (2) «горный горизонт»: доля азимутов сектора, где угол горизонта дальней зоны > 2° и ячейка-блокер на расстоянии > 3 км с высотой > 300 м. Prominence есть в OSM только иногда (`prominence=*`); для текста брать `ele` и `name`.
- **Достопримечательность:** POI с высотой (`height`, `building:levels`, `man_made=tower`, Wikidata P2048). LOS до 3 точек: верх, 2/3, 1/3 высоты, плюс угловой размер `atan(h/d)`. Ответ: «шпиль виден с 7-го этажа (только верхняя треть)». Если POI — полигон, взять несколько точек по контуру и отбросить из DSM собственный footprint POI, иначе луч упрётся в его же стену.
- **Деревья.** В курортных городах (Сочи) кроны часто закрывают нижние этажи. В DSM их нет (или они размазаны в Copernicus). Это главная систематическая ошибка «вид есть на бумаге, нет в жизни» для этажей 1–3. Нужно понизить уверенность для низких этажей при наличии `natural=tree`/`landuse=forest`/`leisure=park` в первых 50 м луча.

### Gaps
- Не нашёл стандартных порогов («сколько градусов моря = вид на море»), принятых в индустрии бронирования. Пороги нужно калибровать на своей разметке.
- Полный текст Gillespie et al. (как именно считались breadth и depth) не прочитан: доступна только аннотация.

---

## 5. Закат и рассвет: азимут солнца по месяцам, профиль горизонта, «закат над морем»

### Takeaway
Для каждой точки фасада и этажа один раз считаем **профиль горизонта** `H(az)` (угол самого высокого препятствия по азимуту, с кривизной). Затем для каждого месяца берём траекторию солнца за час до заката (pvlib или astral) и проверяем, есть ли момент, когда видимая высота солнца больше `H(az)` и при этом солнце в «закатном» диапазоне 0–5°. «Закат над морем» — на азимуте заката луч уходит до горизонта моря без наземного блокера.

### Cited Findings
- Astral 3.x: расчёты солнца адаптированы из таблиц NOAA. Рефракция учтена в `sunrise()`/`sunset()` (солнце на полдиаметра ниже горизонта, 32′), но без поправок на температуру и давление. Высоту наблюдателя можно передать числом или кортежем «(вертикальная, горизонтальная) дистанция до препятствия». Азимут считается по часовой от севера — [Astral docs](https://astral.readthedocs.io/en/latest/). Версия astral 3.2 (PyPI).
- pvlib 0.16.1 (26.09.2026, wheel `py3-none-any`, Python ≥ 3.11) — альтернатива с SPA-алгоритмом (PyPI).
- Собственный расчёт (astral 3.2, 15-е число месяца), азимут заката/восхода в градусах:

| Месяц | Сочи 43,6° с.ш. | Калининград 54,7° | СПб 59,9° |
|---|---|---|---|
| Янв | 241 / 119 | 233 / 127 | 226 / 134 |
| Мар | 268 / 92 | 268 / 93 | 267 / 93 |
| Апр | 285 / 76 | 289 / 72 | 292 / 69 |
| Май | 298 / 63 | 306 / 55 | 312 / 48 |
| Июн | 304 / 56 | 315 / 45 | 325 / 36 |
| Авг | 290 / 70 | 296 / 64 | 300 / 59 |
| Сен | 275 / 85 | 276 / 84 | 277 / 83 |
| Дек | 238 / 122 | 228 / 132 | 220 / 140 |

- Сочи, 21 июня: за 60 мин до заката солнце на азимуте 294,2° и высоте 8,8°; за 30 мин — 299,1° и 4,0°; в момент заката — 304,2° (собственный расчёт astral).

### Inferences
- **Генерация фразы.** Возьмём фасад с нормалью 270° и окном ±60° (210–330°): в Сочи он ловит закат во все месяцы. ЮЗ-фасад 225° (165–285°) — только сентябрь – апрель. Именно так получается «закат из окон западной стороны, апрель — август»: месяцы, где азимут заката попадает в сектор фасада **и** солнце не перекрыто `H(az)`.
- **Профиль горизонта** считается тем же `cast_ray` (возвращает `max_tan`) на 360 азимутов с шагом 1° — это 0,3 мс на точку и этаж в дальней зоне. Объединить ближнюю и дальнюю зоны: `H(az) = max(H_near, H_far)`.
- **Критерий «видимый закат»** за месяц m: существует t ∈ [sunset − 40 мин, sunset], для которого `alt_app(t) ≥ H(az(t)) + 0.25°` (запас на полдиска) и `alt_app(t) ≤ 5°`. Минимальный порог: хотя бы 10 минут закатного солнца в окне. «Видна сама линия заката» (солнце касается горизонта): `H(az_sunset) ≤ dip + 0.5°`, где dip ≈ √(2h/R).
- **«Закат над морем»**: луч по азимуту заката (± полуширина диска) достигает морской ячейки, после которой до горизонта моря нет препятствия выше линии визирования (т.е. `max_tan` достигается на морской ячейке или на горизонте). Иначе закат «за горы» или «за дома». В Сочи летний азимут заката (~300–304°) идёт почти вдоль побережья на СЗ, поэтому «закат над морем» летом зависит от точки и не очевиден. Это хороший пример ценности расчёта против интуиции.
- **Рассвет** считается так же; для гор — «рассвет из-за гор» (`H(az_sunrise)` большой: солнце появляется позже астрономического восхода).
- Вычислительно: 12 месяцев × ~20 временных шагов × интерполяция профиля = микросекунды. Узкое место — только профиль горизонта.

**Псевдокод:**
```python
import pandas as pd, pvlib   # или astral.sun.azimuth/elevation
def sunset_months(lat, lon, H_profile_deg, facade_az, half_fov=60, year=2026):
    months = []
    for m in range(1, 13):
        day = pd.Timestamp(year, m, 15, tz="UTC")
        times = pd.date_range(day + pd.Timedelta("12h"), day + pd.Timedelta("23h59min"), freq="2min")
        sp = pvlib.solarposition.get_solarposition(times, lat, lon)
        w = sp[(sp.apparent_elevation > -0.3) & (sp.apparent_elevation < 5)]
        w = w[w.azimuth.between(180, 360)]                    # вечер
        in_fov = angdiff(w.azimuth, facade_az).abs() <= half_fov
        visible = w.apparent_elevation > np.interp(w.azimuth, np.arange(360), H_profile_deg) + 0.25
        minutes = 2 * (in_fov & visible).sum()
        if minutes >= 10: months.append(m)
    return months  # -> "апрель — август" через схлопывание диапазонов
```

### Gaps
- Не нашёл источника о допустимой точности модели рефракции у горизонта. Она меняется день ото дня (см. SDSU), поэтому пороги ±0,5° — допущение.

---

## 6. Шум: CNOSSOS-EU / упрощённые модели, прокси трафика из OSM, экранирование, ж/д, аэропорты, бары; NoiseModelling

### Takeaway
За хакатон реалистичен **упрощённый пофасадный индекс шума в дБ-подобных единицах**. Эмиссия дороги берётся по классу OSM `highway` с дефолтными потоками из NoiseModelling (они происходят из Good Practice Guide v2), затухание — 3–4,5 дБ на удвоение расстояния для линейного источника. Экранирование — бинарный LOS-тест фасадной точки до участка дороги по тому же DSM: если фасад «смотрит» от дороги, его закрывает собственное здание. Плюс точечные ночные источники (бары, клубы) и ж/д. NoiseModelling 6.0 (Java, H2GIS, полный CNOSSOS) реален только как опциональная эталонная прогонка 2–3 отелей.

### Cited Findings
- CNOSSOS-EU (Директива (EU) 2015/996): удельная звуковая мощность потока — мощность одного ТС + **10·lg(Q_m / (1000·v_m))**, где Q — ТС/ч, v — км/ч. Пять категорий ТС; шум качения (A + B·lg(v/v_ref)) и двигателя (A + B·(v − v_ref)/v_ref) раздельно, v_ref = 70 км/ч — [CadnaA: CNOSSOS-EU Road](https://doku.datakustik.com/CadnaA/en/Str_CNOSSOS.html); [Council of the EU, ST-5159-2015-ADD-1](https://data.consilium.europa.eu/doc/document/ST-5159-2015-ADD-1/en/pdf).
- В NoiseModelling источник дорожного шума принудительно ставится на 5 см над землёй (по CNOSSOS) — [NoiseModelling docs (OSM tutorial)](https://noisemodelling.readthedocs.io/en/v6.0.0/_sources/Tutorial_Noise_Map_From_OSM.rst.txt).
- **Дефолтный трафик по классам OSM в NoiseModelling** (`Import_OSM.groovy`, класс `Road`; ссылается на Good Practice Guide for Strategic Noise Mapping v2): массивы `aadf_d=[26103,17936,7124,1400,700,350,175]`, `aadf_e=[7458,3826,1069,400,200,100,50]`, `aadf_n=[3729,2152,712,200,100,50,25]`; доля грузовых `hv_d=[.25,.2,.2,.15,.10,.05,.02]`, `hv_n=[.45,.2,.1,.05,.03,.01,0]`; скорости `[130,110,80,80,50,30,30]`. Категории: motorway, trunk, primary, secondary, tertiary+unclassified, residential, service+living_street. Часы: день 12, вечер 4, ночь 8; для oneway поток делится на 2 — [NoiseModelling Import_OSM.groovy](https://github.com/Universite-Gustave-Eiffel/NoiseModelling/blob/main/noisemodelling-scripts/src/main/groovy/org/noise_planet/noisemodelling/scripts/Import_and_Export/Import_OSM.groovy). В пересчёте на ТС/ч (мой расчёт, aadf/часы): motorway 2175 / 1864 / 466 (день/вечер/ночь); trunk 1495 / 956 / 269; primary 594 / 267 / 89; secondary 117 / 100 / 25; tertiary 58 / 50 / 12; residential 29 / 25 / 6; service 15 / 12 / 3.
- Затухание: линейный источник (дорога) — 3 дБ(A) на удвоение расстояния над жёсткой поверхностью, ~4,5 дБ над мягкой; точечный — 6 дБ на удвоение — [FHWA noise barrier design](https://www.fhwa.dot.gov/Environment/noise/noise_barriers/design_construction/design/design03.cfm); [acoustical.co.uk: Distance attenuation](https://www.acoustical.co.uk/?p=301).
- Экранирование рядами зданий (WisDOT FDM): 3 дБ при застройке 40–65% длины, 5 дБ при 65–90%, +1,5 дБ за каждый следующий ряд, максимум 10 дБ — [Wisconsin DOT FDM 23-15](https://wisconsindot.gov/rdwy/fdm/fd-23-15.pdf).
- NoiseModelling: OSM-пайплайн `Import_OSM` → `Delaunay_Grid` (или фасадные приёмники) → `Road_Emission_from_Traffic` → `Noise_level_from_source` (распространение по CNOSSOS-EU) → `Create_Isosurface`. Требует Java и H2/H2GIS. Туториал рассчитан на GUI; импорт OSM может давать несовместимую геометрию, тогда нужно уменьшить область — [NoiseModelling: Noise Map from OSM](https://noisemodelling.readthedocs.io/en/latest/Tutorial_Noise_Map_From_OSM.html). В ветке 4.x есть сборка без GUI (командная строка, bash, Groovy) — [NoiseModelling: Pilot with scripts](https://noisemodelling.readthedocs.io/en/4.x/Get_Started_Script.html). Фасадные приёмники: 50 см от фасада, `BUILD_PK` — [Input receivers](https://noisemodelling.readthedocs.io/en/latest/Input_receivers.html). Последний релиз — v6.0.0 от 20.05.2026 (GitHub API releases/latest) — [NoiseModelling releases](https://github.com/Universite-Gustave-Eiffel/NoiseModelling/releases).

### Inferences
- **Упрощённая модель (реализуема за 2–3 часа):**
  - Для каждой фасадной точки P на высоте этажа и каждого сегмента дороги s (нарезка по 10 м) в радиусе 300–500 м:
    - `Lw'_s = C_class + 10·lg(Q_s/v_s)` плюс поправка на скорость (`maxspeed` из OSM, иначе дефолт класса). Константу `C` калибровать так, чтобы primary на 10 м давала ≈ 70 дБ(A) днём (допущение, см. Gaps).
    - Вклад: `L_s = Lw'_s + 10·lg(Δl) − 20·lg(d) − 11 + ΔL_ground − ΔL_screen` (точечная аппроксимация сегмента длиной Δl, сумма даёт линейный источник). Проще: `L_road = L_ref(class) − k·lg(d/10)` с k = 10 (3 дБ/удв.) или 15 (4,5 дБ/удв.).
    - `ΔL_screen`: LOS-тест от P до середины сегмента по DSM. Если путь пересекает здание (включая собственное — фасад «смотрит» от дороги), вычесть 10–15 дБ (для своего здания) или 5–10 дБ (для чужих). Ещё грубее — по углу между нормалью фасада и направлением на дорогу: |Δ| > 90° даёт −10 дБ.
    - Энергетическая сумма: `L_P = 10·lg Σ 10^(L_s/10)`.
  - Ночь: те же формулы с `Q_n` (ночные потоки из таблицы выше) плюс ночные точечные источники: `amenity=bar|pub|nightclub` (только если `opening_hours` захватывает 23:00–03:00 или тег отсутствует), затухание 6 дБ/удвоение, вклад только при LOS или с экраном −10 дБ.
  - Ж/д и трамвай: `railway=rail|light_rail|tram` как линейные источники с дефолтной интенсивностью (поездов/ч неизвестно, можно взять из GTFS, если есть); `railway=station` — отдельный бонус шума днём.
  - Аэропорт: `aeroway=runway` (направление ВПП из геометрии). Точка в коридоре ±10° от продолжения оси ВПП ближе 10–15 км — флаг «под глиссадой». Иначе — расстояние до аэродрома.
  - Вывод: «тихая сторона» — фасад с `L_n` на 10+ дБ ниже самого шумного фасада. Шкала для пользователя: тихо / умеренно / шумно, с порогами по квантилям внутри региона (это честнее абсолютных дБ).
- **Почему не NoiseModelling в рантайме:** Java + H2GIS + Groovy в Docker-образе Python, GUI-ориентированная документация и риски импорта OSM. Как офлайн-эталон для 2–3 отелей его можно использовать: сравнить ранжирование фасадов (Spearman) с упрощённой моделью.
- **Валидация шума:** для России открытых карт END нет (официальные карты шума ЕС по директиве END покрывают страны ЕС). Если регион в ЕС, сравнить с картами EEA. Иначе — со спецификой отзывов («шумно, дорога под окном») как слабой меткой.

### Gaps
- Не нашёл первоисточник с численной константой «уровень дБ(A) на 10 м от дороги класса X» для калибровки упрощённой модели. Полный CNOSSOS требует таблиц коэффициентов A/B по октавам (Annex), их я не извлёк.
- Не нашёл стандартной коррекции «собственного экрана» фасада, обращённого от дороги (в литературе фигурирует экранирование рядами зданий 3–10 дБ). Величины 10–15 дБ — допущение.
- Время работы NoiseModelling на городском квартале и документация headless-режима для 5.x/6.x в этой сессии не подтверждены.
- Не искал данные о покрытии EEA noise maps и их формате (Lden ≥ 55 дБ, Lnight ≥ 50 дБ). Если регион в ЕС, это отдельный поиск.

---

## 7. Пешая доступность: osmnx + DEM, Тоблер, пересечение магистралей, лестницы, изохроны

### Takeaway
osmnx 2.1.1 строит пешеходный граф и навешивает высоты с растра (`add_node_elevations_raster`) и уклоны (`add_edge_grades`). Вес ребра — время по функции Тоблера с штрафами за лестницы и пересечение магистралей без перехода. Маршруты до пляжа, метро и центра — Dijkstra; изохроны — `networkx.ego_graph` по времени. Детектор «нужно переходить трассу» — пересечение геометрии маршрута с `highway ∈ {trunk, primary, motorway}` без узла `highway=crossing` в точке пересечения.

### Cited Findings
- osmnx 2.1.1 (21.07.2026, Python ≥ 3.11, wheel `py3-none-any`). Сигнатуры: `add_node_elevations_raster(G, filepath, *, band=1, cpus=None)`; `add_edge_grades(G, *, add_absolute=True)` (нужны `elevation` и `length`); `shortest_path(G, orig, dest, *, weight='length', cpus=1)` (Dijkstra, `None` при недостижимости); `graph_from_point(center_point, dist, *, dist_type='bbox', network_type='all', simplify=True, ...)` с `network_type ∈ {all, all_public, bike, drive, drive_service, walk}`; `features_from_point(center_point, tags, dist)` — [osmnx User Reference](https://osmnx.readthedocs.io/en/stable/user-reference.html).
- Функция Тоблера: **W = 6·exp(−3,5·|S + 0,05|)** км/ч, S — уклон (dh/dx). На ровном ≈ 5 км/ч, максимум 6 км/ч на спуске −5%; вне дорог ×0,6 — [Wikipedia: Tobler's hiking function](https://en.wikipedia.org/wiki/Tobler%27s_hiking_function). Пик на мягком спуске критикуют как, возможно, нереалистичный — [arXiv 2303.16065](https://arxiv.org/pdf/2303.16065v6).

### Inferences
- **Ловушка DEM.** Copernicus — DSM, поэтому на мостах и в плотной застройке «высота узла» может оказаться крышей. Нужно: сглаживать высоты узлов вдоль ребра, обрезать |grade| ≤ 0,3, рёбра `bridge=yes`/`tunnel=yes` считать с уклоном, равным линейной интерполяции между концами. Для коротких рёбер (< 10 м) уклон по 30-м DEM — шум; агрегировать на уровне маршрута (суммарный подъём, м).
- **Вес ребра:** `t = length / (W(grade)·1000/60)` мин; `highway=steps`: +0,5 с на ступень или ×2 по времени, флаг `has_steps` (важно для семей с колясками и пожилых); `wheelchair=no` — флаг.
- **Детектор опасного перехода:**
```python
major = roads[roads.highway.isin(["motorway","trunk","primary","motorway_link","trunk_link"])]
def risky_crossings(route_geom, crossing_nodes):
    pts = route_geom.intersection(major.unary_union)       # точки пересечения маршрута с магистралями
    out = []
    for p in explode_points(pts):
        has_x = crossing_nodes.distance(p).min() < 15     # highway=crossing / footway=crossing / подземный переход
        out.append(dict(pt=p, road=nearest_name(p), controlled=has_x))
    return out
```
  В графе `walk` пешеход формально может «пройти» вдоль или поперёк trunk/primary там, где OSM не запрещает. Поэтому важно ловить пересечения геометрически, а не только по рёбрам. Отдельно отметить «переход через подземный/надземный переход» (`tunnel=yes`/`bridge=yes` на `footway=crossing`) — это плюс, а не минус.
- **Цели:** пляж (`natural=beach`, вход — ближайший узел графа к полигону, лучше к точкам `entrance`/`access=yes`), метро (`railway=subway_entrance`), центр (вручную заданная точка или `place=city` centroid), остановки ОТ.
- **Изохроны:** `nx.ego_graph(G, src, radius=15, distance="t_min")` → convex hull или alpha-shape узлов → «в 15 мин пешком: 3 пляжа, 12 кафе».
- **Вывод пользователю:** «до пляжа 650 м по прямой, 11 мин пешком с подъёмом 35 м на обратном пути, нужно перейти Курортный проспект (есть регулируемый переход)». Асимметрия «туда-обратно» по Тоблеру — сильная деталь для курортов на склонах.

### Gaps
- Не проверил точный OSM-фильтр `network_type="walk"` в osmnx 2.x: какие классы (`trunk`?) включены или исключены. Нужно посмотреть исходник `osmnx._overpass`.
- Время построения графа региона и Overpass-лимиты не измерены. Вероятно, разумнее строить граф из `.osm.pbf` (Geofabrik) одним проходом, чем делать 300 запросов к Overpass.

---

## 8. Ночная инфраструктура (opening_hours), семьи с детьми, на машине

### Takeaway
`opening-hours-py` (Rust-ядро, manylinux wheels, версия 2.1.4 от 07.07.2026) парсит OSM `opening_hours` и отвечает, что открыто в 02:00. Это детерминированная характеристика «что работает ночью» с честной пометкой о покрытии тега. Семейные и автомобильные признаки — прямые OSM-запросы плюс сетевые расстояния.

### Cited Findings
- `opening-hours-py` 2.1.4 (07.07.2026): Rust-парсер OSM opening_hours, wheels `manylinux_2_17` (x86-64, aarch64 и др., abi3). API: `oh = OpeningHours("Mo-Fr 10:00-18:00; Sa-Su 10:00-12:00"); oh.state(); oh.next_change()`. Метаданные требуют Python ~= 3.10, а текст README говорит о «3.9 и выше» — расхождение — [PyPI opening-hours-py](https://pypi.org/project/opening-hours-py/).
- Альтернатива `osm-humanized-opening-hours` (pure Python, человекочитаемые описания): разработка приостановлена, последние изменения около 2021 г. — [PyPI osm-humanized-opening-hours](https://pypi.org/project/osm-humanized-opening-hours/); [ecosyste.ms](https://awesome.ecosyste.ms/projects/github.com%2Frezemika%2Fhumanized_opening_hours).

### Inferences
- **Ночь:** для `amenity=pharmacy`, `shop=convenience|supermarket`, `amenity=cafe|restaurant|fast_food|fuel` в пешем радиусе 10–15 мин проверить состояние в 01:00, 03:00 и 05:00 для типичного вторника и субботы. `24/7` — отдельный класс. Тег отсутствует — «неизвестно», а не «закрыто»; это входит в доверие. Выход: «круглосуточная аптека в 6 мин пешком (по OSM, тег `24/7`)».
- **Семьи:** `leisure=playground` (сетевое расстояние, без пересечения магистрали — переиспользовать детектор из раздела 7), `highway=pedestrian`/`living_street` рядом, ближайшая детская больница: `amenity=hospital` + `healthcare:speciality=paediatrics` или имя с «детск». Время на машине — граф `drive`.
- **Машина:** «подъезд к входу» — расстояние от входа отеля (`entrance=*` или ближайшая точка footprint) до ближайшего ребра графа `drive` с допустимым доступом (`motor_vehicle!=no`, `access!=private`). Больше 50 м или внутри `highway=pedestrian` area — «пешеходная зона». Парковки — `amenity=parking` с `fee`/`capacity`/`access`. Зарядки — `amenity=charging_station` с `socket:*`.
- Всё это быстро (миллисекунды на отель после загрузки данных региона) и полностью объяснимо.

### Gaps
- Покрытие тега `opening_hours` в выбранном регионе неизвестно; его нужно измерить и показать в UI (например, «часы работы известны у 40% аптек рядом»).

---

## 9. Оценка уверенности: Monte Carlo по неопределённым входам

### Takeaway
Уверенность — это **вероятность исхода при разыгрывании неопределённых входов**: высот зданий без тегов, ошибки DEM (σ ≈ 2,4 м из спецификации LE90 < 4 м), высоты этажа и положения окна на фасаде. 50–200 прогонов на отель занимают секунды на numba. Результат формулируется как «вид на море с 5-го этажа, p = 0,82», а уровень доверия дополнительно понижается за низкое покрытие источников (доля блокирующих зданий с реальной высотой).

### Cited Findings
- Спецификация Copernicus: абсолютная вертикальная точность < 4 м (LE90) — [Copernicus Data Space](https://dataspace.copernicus.eu/node/1306). Попиксельный HEM (σ) — [GEE catalog](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_DEM_GLO30).
- GDAL 3.12 `gdal raster viewshed --sd-filename` принимает растр стандартных отклонений высот, то есть сам GDAL движется к вероятностной видимости — [GDAL gdal raster viewshed](https://gdal.org/en/stable/programs/gdal_raster_viewshed.html).
- NoiseModelling уже рандомизирует высоту зданий без тегов (`4.0 + U(0; 2.1)`), то есть неопределённость высот признана практикой — [Import_OSM.groovy](https://github.com/Universite-Gustave-Eiffel/NoiseModelling/blob/main/noisemodelling-scripts/src/main/groovy/org/noise_planet/noisemodelling/scripts/Import_and_Export/Import_OSM.groovy).
- Валидация viewshed по LiDAR-DSM в Гандии: общая точность 90%, kappa 0,80 — ориентир достижимого качества при хорошем DSM — [Revista de Teledetección, polipapers](https://polipapers.upv.es/index.php/raet/article/view/2293).

### Inferences
- **σ DEM:** LE90 = 1,645σ, отсюда σ ≈ 4/1,645 ≈ 2,4 м (верхняя граница). Лучше брать HEM попиксельно. Ошибка пространственно коррелирована: разыгрывать не белый шум, а сглаженное поле (гауссово ядро ~100–300 м) или один общий сдвиг на прогон.
- **Распределения входов:**
  - `height` есть: N(h, 1 м).
  - `levels` есть: `levels × U(2.8; 3.5) + U(0; 2.5)` (крыша).
  - ничего нет: выборка из эмпирического распределения размеченных зданий того же `building=*` в радиусе 1 км.
  - высота 1-го этажа: U(3; 5) м; положение окна: равномерно по точкам фасада.
- **Алгоритм:**
```python
def mc_view(hotel, K=100):
    res = []
    for k in range(K):
        h = sample_heights(buildings_in_sector, rng)          # векторно
        dsm_k = rebuild_dsm_fast(ground + dem_noise(rng), footprints_raster_ids, h)  # перезапись по id-растру: O(ячеек)
        res.append(facade_scan(dsm_k, sea, pts, z_floors(rng), ...))
    P_sea[floor] = mean_k( sea_width_k[floor] >= THR )
    min_floor_p80 = min floor with P_sea >= 0.8
    return P_sea, min_floor_p80
```
  `rebuild_dsm_fast`: растр id зданий строится один раз; на прогон нужен `dsm = ground + h_by_id[bid]` — одна векторная операция numpy на 4–25 млн ячеек (~10–50 мс), либо пересчёт только окна вокруг отеля.
- **Итоговая уверенность характеристики** = `p_outcome × coverage_factor`, где `coverage_factor` = доля «блокирующих» зданий (первых препятствий на лучах) с реальным `height`/`levels`, плюс штраф за привязку footprint по расстоянию и за деревья в первых 50 м. Хранить вместе со значением: источник (OSM дата выгрузки, Copernicus версия), дату расчёта и id зданий-блокеров — для актуализации (кейс, доп. задача 4).
- **Когда спрашивать отельера:** p ∈ [0,3; 0,7] или неизвестны этажи номеров. Вопрос: «Какие номера и с какого этажа выходят на ЮЗ (на море)?».

### Gaps
- Не нашёл опубликованного метода «вероятностный viewshed с Monte Carlo по высотам OSM»: схема выше — собственный дизайн, её надо проверять на разметке.

---

## 10. Валидация: фото с геометками, тексты отелей, протокол ручной разметки

### Takeaway
Валидация в три слоя. (1) Слабые метки из текстов — описания и отзывы с «вид на море» / «шумно»: это и проверка, и демонстрация обнаружения противоречий. (2) Ручная разметка стратифицированной выборки 40–60 отелей по панорамам и фото с направлением съёмки (Mapillary `compass_angle`, Wikimedia Commons). (3) Кросс-проверка своего ray casting против GDAL viewshed на нескольких точках.

### Cited Findings
- Gillespie et al. комбинировали NLP-метку «есть вид на море» из текста и GIS-меру ширины и глубины. Это прямой прецедент «текст + геометрия» — [Land Economics](https://le.uwpress.org/content/early/2023/10/09/le.100.2.071522-0056R.abstract).
- Mapillary API v4: entity-эндпоинты дают лишь очень ограниченный bbox-поиск, для массовых пространственных запросов предполагаются vector tiles. Слой `image` (z14, точки) содержит `compass_angle` и `is_pano` — [Mapillary API docs](https://www.mapillary.com/developer/api-documentation); [Mapillary blog: API v4](https://blog.mapillary.com/update/2021/06/23/getting-started-with-the-new-mapillary-api-v4.html). Аналога v3 «lookat» нет. Обходной путь — bbox/tiles и сравнение `compass_angle` с азимутом на цель — [Mapillary forum](https://forum.mapillary.com/t/apiv4-equivalent-to-lookat-for-finding-images-looking-at-a-point/5829).
- Точность LiDAR-viewshed в Гандии — 90%, kappa 0,80; это ориентир «хорошего» результата — [polipapers](https://polipapers.upv.es/index.php/raet/article/view/2293).

### Inferences
- **Протокол:**
  1. Стратифицировать отели по предсказанию (море: нет / частично / да × p высокая / низкая) и выбрать 40–60 штук.
  2. Два разметчика независимо по фото отеля (сайт, агрегаторы), Mapillary и Commons ставят метку «вид на море из номеров с фасада X» и «шумная улица да/нет».
  3. Посчитать Cohen's kappa между разметчиками, precision/recall и калибровку (reliability diagram p vs факт).
  4. Подобрать пороги ширины моря (раздел 4).
- **Текстовые слабые метки:** регулярки и LLM-извлечение (это допустимо, LLM здесь извлекает факт из текста, а не угадывает) из описаний «вид на море», «sea view», «панорамный». Конфликт «текст говорит есть, геометрия — нет» → флаг противоречия, вопрос отельеру. Это закрывает пункт кейса «система замечает противоречия».
- **Mapillary как проверка шума и экранирования:** фото с улицы показывает тип улицы и наличие трамвая. Для вида из окна Mapillary почти бесполезен (съёмка с уровня земли).
- **Кросс-проверка движка:** на 3–5 точках прогнать `gdal raster viewshed --start-angle/--end-angle` (GDAL ≥ 3.12 в Docker `osgeo/gdal`) и сравнить маски видимых морских ячеек с numba-движком (IoU > 0,9 ожидается, если кривизна задана одинаково).

### Gaps
- Не проверял лицензии и доступность Wikimedia Commons API для поиска фото по координатам с направлением съёмки (шаблон Location с heading) — это отдельный шаг.

---

## 11. Prior art: open-source и исследования

### Takeaway
Готового open-source «вид из окна отеля по этажам» нет. Есть строительные блоки: GDAL, xarray-spatial, GRASS r.viewshed, xDEM, OSM2LES, pybdshadow. Есть научная база в гедонических исследованиях (LiDAR/DSM, поэтажный viewshed, метрики ширины, глубины и сегментов) и в шумовом моделировании (NoiseModelling + OSM). Это хороший нарратив для жюри: «метод из академической оценки недвижимости, адаптированный к отелям».

### Cited Findings
- Скрипты автоматизации viewshed от зданий (QGIS/GRASS) — [GitHub lucasdemassy/Viewshed-automation-workflow](https://github.com/lucasdemassy/Viewshed-automation-workflow).
- xDEM — Python-пакет анализа DEM (выравнивание, оценка ошибок), сам видимость не считает — [GitHub GlacioHack/xdem](https://github.com/GlacioHack/xdem).
- OSM2LES переносит footprints OSM в 2D DEM — [libraries.io OSM2LES](https://libraries.io/pypi/OSM2LES).
- GRASS `r.viewshed` — классический инструмент видимости — [GRASS manual r.viewshed](https://grass.osgeo.org/grass86/manuals/r.viewshed.html).
- Гедонические работы: Yamagata (поэтажный viewshed, Йокогама) — [CUPUM 2013](https://cupum2013.geo.uu.nl/download/usb/contents/windows/pages/..\..\pdf\shortpapers\62_Yamagata.pdf); Hindsley et al. (LiDAR, 4 меры вида) — [WP](https://econ.appstate.edu/RePEc/pdf/wp1106.pdf); Gillespie et al. (3D + NLP, Ирландия) — [Land Economics](https://le.uwpress.org/content/early/2023/10/09/le.100.2.071522-0056R.abstract); Вена (LoD1 достаточно) — [UNIGIS](https://unigis.at/files/Mastertheses/Full/1452.pdf).
- NoiseModelling (Université Gustave Eiffel) — open-source CNOSSOS-EU с импортом OSM — [docs](https://noisemodelling.readthedocs.io/en/latest/Tutorial_Noise_Map_From_OSM.html).

### Inferences
- `pybdshadow` (Python-пакет теней зданий, GitHub `ni1o1/pybdshadow`) встретился в результатах поиска как близкий по смыслу инструмент; полезен как референс для «солнце + здания», но вид не считает. Сам репозиторий я не открывал.
- Коммерческие ShadeMap и Shadowmap (тени и солнце по 3D-зданиям) — хорошая аналогия для демо, но их методы и API я не исследовал.

### Gaps
- Не нашёл открытого проекта, который считает вид из окна для отелей или недвижимости по OSM в виде готовой библиотеки. Это аргумент в пользу собственного движка как новизны решения.

---

## 12. Производительность и бюджет предрасчёта для ~300 отелей

### Takeaway
Вычислительная часть копеечная: весь пакет «вид, закат и Monte Carlo» для 300 отелей укладывается примерно в 10–20 минут на ноутбуке (M4, 10 ядер). Узкие места — загрузка и подготовка данных: OSM-выгрузка региона, тайлы DEM, построение DSM и пешеходного графа. Их нужно делать один раз на регион и кешировать.

### Cited Findings (собственные замеры, Apple M4 10 ядер, numba 0.68.0)
- Ближняя зона 2 м: 1 фасад = 5 точек × 10 этажей × 61 азимут × луч 3,9 км с шагом 2 м → **2,9–3,7 мс**.
- Дальняя зона 30 м: 1 точка × 10 этажей × 361 азимут × 30 км → **0,3 мс**.
- Растеризация 20 000 footprints в 2000×2000 @ 2 м → **0,18 с** (rasterio 1.5.2).
- JIT-компиляция numba: 0,5–0,8 с один раз (с `cache=True` — один раз на образ).

### Inferences
- **На отель (вид + закат):** ~6 фасадов × 3,5 мс ≈ 20 мс на детерминированный прогон; плюс профиль горизонта 360° для закатов (≈ 1–2 мс на точку и этаж). С Monte Carlo K = 100 и пересборкой DSM по id-растру (~10–50 мс на прогон для окна 4×4 км) — **~3–7 с на отель**, то есть **15–35 мин на 300 отелей** при последовательной обработке. С K = 50 и окном вокруг отеля 3×3 км — < 10 мин.
- **Шум (упрощённая модель):** 300–2000 сегментов дорог в радиусе 500 м × ~50 фасадных точек × LOS-тест — < 1 с на отель на numba.
- **Пешая доступность:** граф региона из PBF строится один раз (минуты, не измерено). Dijkstra от отеля до всех целей одним `single_source_dijkstra` — десятки миллисекунд.
- **Данные:** Copernicus-тайлы 1°×1° (~ десятки МБ каждый) качаются с S3 без ключа; OSM PBF региона (Geofabrik) — десятки–сотни МБ. В репозиторий коммитить не сырые данные, а результаты (JSON/Parquet на отель с матрицами `facade×floor`) и, возможно, обрезанный DSM для визуализации.
- **Docker:** если нужен GDAL viewshed — базовый образ `ghcr.io/osgeo/gdal` (Python-пакет `gdal` на PyPI без wheels). Если только numba-движок — достаточно `python:3.12-slim` + wheels: `rasterio`, `shapely`, `geopandas`, `pyproj`, `numba`, `osmnx`, `pvlib` или `astral`, `opening-hours-py`. Рантайм читает готовые результаты и не считает тяжёлое.
- **Версии (PyPI, 26.10.2026):** rasterio 1.5.2 (Py ≥ 3.12), numba 0.68.0, shapely 2.2.0, geopandas 1.2.0, pyproj 3.8.0 (Py ≥ 3.12), pyogrio 0.13.0, osmnx 2.1.1, pvlib 0.16.1, astral 3.2, xarray-spatial 0.10.19, opening-hours-py 2.1.4, duckdb 2.0.0 (для чтения Overture GeoParquet), gdal 3.13.3 (только sdist). Все, кроме gdal, имеют manylinux или pure-Python wheels. Минимальный общий Python — **3.12**.

### Gaps
- Не измерял время построения пешеходного графа osmnx для города размера Сочи и время скачивания и обработки Overture buildings через duckdb для региона.
- Бенчмарк проведён на синтетическом DSM; на реальных данных время луча то же (O(длина/шаг)), но подготовка данных может занять больше.
