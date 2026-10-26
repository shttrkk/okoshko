# Кодирование выходных NPZ без изменения расчётов

`python -m pipeline.compact_geo --data-dir data` проверяет кандидатов и SHA-зависимости без изменения файлов. `python -m pipeline.compact_geo --data-dir data --backup-dir /tmp/okno-npz-originals --apply` сначала копирует **все** исходные архивы и их физические SHA-256 в `/tmp`, затем атомарно перекодирует только `data/geo/<hotel_id>.npz` для отелей из `data/seed/hotels.json`. Без `--backup-dir` создаётся уникальный каталог `/tmp/okno-npz-originals-*`.

Исходные `.npy` members копируются байт в байт в ZIP_LZMA: фиксированная дата ZipInfo `1980-01-01`, Unix create_system, права 0600, сохранённый порядок members. На каждом файле до замены и ещё раз после полного прогона `numpy.load(..., allow_pickle=False)` проверяет порядок ключей, dtype, shape и побитовое равенство `tobytes()` каждому backup. Object arrays запрещены. В backup остаются `originals.json` и `repack_report.json`; резервные копии не входят в репозиторий. Повторный запуск с тем же backup проверяет старые SHA и равенство массивов.

Не меняются scientific arrays, JSON, claims, модели, оценки confidence и input hashes. Исключены `terrain_30m.npz`, `*.los.npz` и change-output archives. Любая найденная ссылка на физический SHA кандидата в geo JSON, claims ledgers либо manifest/source metadata останавливает операцию.

Проверенная карта зависимостей:

- У baseline NPZ в JSON есть `dsm.artifact`, но физический SHA этих выходных архивов не записан в исследованных 259 metadata/claim artifacts.
- SHA `terrain_30m.npz` входит в `inputs`, `evidence.inputs.input_sha256` и `data/geo/validation.json`: архив terrain нельзя перекодировать.
- Физический SHA `osm-node-10058556917.los.npz` указан в change JSON как `los_cache_sha256`: LOS cache тоже нельзя перекодировать.
- `baseline_sha256` внутри LOS NPZ — hash **baseline JSON**, не baseline NPZ. JSON и эта цепочка сохраняются без изменений.
- `geo/runtime_cache.py` читает baseline NPZ через numpy и не проверяет hash самого output NPZ. `pipeline.compute_validate` проверяет numeric arrays и input hashes; scientific code менять не требуется.

Выборка из 8 baseline архивов и 1 change output: ZIP_LZMA сохранил все массивы побитово, ожидаемая экономия baseline — около 37 MB (34.45%). DEFLATE9 экономит только около 1.76 MB. Полное чтение всех массивов: median 4.84 ms у исходного DEFLATE против 16.72 ms у LZMA; это локальный warm-cache microbenchmark, не оценка latency поиска или сети. Runtime Docker Python 3.12 / numpy 2.2.5 поддерживает `lzma` и ZIP_LZMA. Cached-change с новым baseline NPZ дополнительно проверен в Docker: 121 synthetic claim, 1.46 s; все записи выполнялись только в изолированном `/tmp` каталоге.

Это экономия текущего дерева. Уже существовавшие git blobs не исчезают от нового коммита; решение об архивировании истории и первом push принимает оркестратор.


## Финальная проверка 26 октября 2026

Перекодированы ровно 250 baseline outputs: **107 479 046 → 70 881 187 bytes**, экономия **36 597 859 bytes** (34.90 MiB). Полный набор NPZ, включая неизменённые terrain, LOS и change-output, уменьшился примерно с 128.12 до 91.53 MB. Для всех 250 архивов до атомарной замены и повторно после прогона проверено полное побитовое равенство ключей/типов/форм/значений исходному backup. `originals.json` и `repack_report.json` сохранены в `/tmp/okno-npz-originals-q141pdi3`.

- `pytest geo/tests -q`: 21 passed.
- Строгий `pipeline.compute_validate` на временном geo-каталоге с symlinks: 250 отелей, 20 340 claims, contract/provenance/numeric invariants PASS. Source JSON и `data/geo/validation.json` не переписывались; отчёт остался в `/tmp/okno-npz-strict-a1zpad96`.
- Повторное ZIP_LZMA-кодирование архива героя дало тот же SHA-256, то есть те же физические bytes.
- Docker Python: загрузка LZMA через `allow_pickle=False`, совпадение всех массивов с исходным образом и isolated cached-change PASS.

Эти проверки доказывают неизменность представления расчётов и совместимость декодирования; они не являются измерением реальной точности геомодели.
