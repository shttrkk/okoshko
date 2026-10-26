"""Convert rendered building snapshots to WebP and write their seed index.

The images are views of an open-data 3D model (OSM/Overture footprints and
heights on the local PMTiles basemap), not photographs; the index says so.
Run after scripts/render_building_snapshots.mjs; needs Pillow.
"""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/building_snapshots"
OUT = ROOT / "data/seed/photos/models"
INDEX = ROOT / "data/seed/building_snapshots.json"
SIZE = (720, 450)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    hotels = {h["id"] for h in json.loads((ROOT / "data/seed/hotels.json").read_text())}
    models = {}
    for path in sorted(RAW.glob("*.jpg")):
        hotel_id = path.stem
        if hotel_id not in hotels:
            continue
        target = OUT / f"{hotel_id}.webp"
        Image.open(path).convert("RGB").resize(SIZE, Image.LANCZOS).save(target, "WEBP", quality=70, method=6)
        data = target.read_bytes()
        models[hotel_id] = {
            "url": f"/data/seed/photos/models/{hotel_id}.webp",
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    INDEX.write_text(json.dumps({
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "method": "Вид 3D-модели здания по открытым данным: контур и этажность OSM/Overture на локальной "
                  "подложке PMTiles. Здание отеля выделено цветом. Это модель, а не фотография.",
        "attribution": "© OpenStreetMap contributors (ODbL), Overture Maps Foundation",
        "models": models,
    }, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(models)} snapshots, {sum(m['bytes'] for m in models.values()) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
