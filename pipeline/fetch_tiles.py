"""Extract pinned Protomaps tiles; no requests to tile.openstreetmap.org."""
import platform
import subprocess
import zipfile
from urllib.parse import quote

from shapely.geometry import box, mapping
from shapely.ops import unary_union

from pipeline.common import CONFIG, COVERAGE_BBOXES, RAW, ROOT, dump, fetch, read, sha, source, utcnow

VERSION = "1.31.2"
URL = CONFIG["tiles"]["url"]
CHECKSUMS = {
    "go-pmtiles-1.31.2_Darwin_arm64.zip": "40528f7f616fcbf91207cd48c8fc023d213f6d86c0cbf1f748732803d1880f3d",
    "go-pmtiles-1.31.2_Darwin_x86_64.zip": "1f0dc02eee6c58312dd6c509faee1b5c32f0596568af1bf51f1b034e7a88a65b",
    "go-pmtiles_1.31.2_Linux_arm64.tar.gz": "f8bd47e7ea866863489cad588fbaf2f31f42e5821f7a03f009b3769f05801cb1",
    "go-pmtiles_1.31.2_Linux_x86_64.tar.gz": "3ed7dbf4ec2e6dfe5e25b6f70d1ffc932729f93c86db353bf514dd71010a312f",
}  # Publisher release digests, https://api.github.com/repos/protomaps/go-pmtiles/releases/tags/v1.31.2


def run():
    tile_dir = ROOT / "data/tiles"
    output = tile_dir / "city.pmtiles"
    previous_path = RAW / "tiles_stage.json"
    if previous_path.exists() and output.exists():
        previous = read(previous_path)
        d = previous["download"]
        if d.get("url") == URL and d.get("coverage_bboxes") == COVERAGE_BBOXES and d.get("maxzoom") == CONFIG["tiles"]["maxzoom"] and d.get("sha256") == sha(output) and len(previous.get("assets", [])) >= 8:
            print(str(output), output.stat().st_size, "cached", flush=True)
            return
    system = {"Darwin": "Darwin", "Linux": "Linux"}[platform.system()]
    arch = "arm64" if platform.machine() in {"arm64", "aarch64"} else "x86_64"
    asset = f"go-pmtiles{'-' if system == 'Darwin' else '_'}{VERSION}_{system}_{arch}"
    # Current release publishes ZIP assets for Darwin and tar.gz for Linux.
    extension = "zip" if system == "Darwin" else "tar.gz"
    path, toolmeta = fetch(f"https://github.com/protomaps/go-pmtiles/releases/download/v{VERSION}/{asset}.{extension}", asset + "." + extension)
    expected_sha256 = CHECKSUMS[path.name]
    if sha(path) != expected_sha256:
        raise ValueError("PMTiles CLI checksum does not match the pinned publisher digest")
    target = RAW / "pmtiles-cli"
    target.mkdir(exist_ok=True)
    if extension == "zip":
        with zipfile.ZipFile(path) as z:
            z.extractall(target)
    else:
        import tarfile
        with tarfile.open(path) as tar:
            tar.extractall(target, filter="data")
    cli = target / "pmtiles"
    cli.chmod(0o755)
    tile_dir.mkdir(exist_ok=True)
    region = tile_dir / "coverage.geojson"
    dump(region, {"type": "Feature", "properties": {"coverage_bboxes": COVERAGE_BBOXES}, "geometry": mapping(unary_union([box(*b) for b in COVERAGE_BBOXES]))})
    output = tile_dir / "city.pmtiles"
    temp = RAW / "city.pmtiles"
    if temp.exists():
        temp.unlink()
    subprocess.run([str(cli), "extract", URL, str(temp), "--region=" + str(region), "--maxzoom=" + str(CONFIG["tiles"]["maxzoom"]), "--download-threads=2", "--quiet"], check=True)
    assert temp.stat().st_size < 50_000_000, "PMTiles exceeds 50 MB file budget"
    temp.replace(output)
    meta = dict(url=URL, snapshot=CONFIG["tiles"]["snapshot"], retrieved_at=utcnow(), sha256=sha(output), bytes=output.stat().st_size,
                sha_scope="extracted city PMTiles", coverage_bboxes=COVERAGE_BBOXES, maxzoom=CONFIG["tiles"]["maxzoom"], tool={**toolmeta, "verified_sha256": expected_sha256})
    assets = []
    for font in ["Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic"]:
        for span in ["0-255", "1024-1279"]:
            url = "https://protomaps.github.io/basemaps-assets/fonts/" + quote(font) + "/" + span + ".pbf"
            p, record = fetch(url, font.replace(" ", "_") + "_" + span + ".pbf")
            target = tile_dir / "fonts" / font / (span + ".pbf")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(p.read_bytes())
            assets.append({**record, "artifact": str(target.relative_to(ROOT)), "license": "OFL-1.1"})
    for theme in ["light", "dark"]:
        for suffix in [".json", ".png", "@2x.json", "@2x.png"]:
            name = theme + suffix
            p, record = fetch("https://protomaps.github.io/basemaps-assets/sprites/v4/" + name, "sprites_v4_" + name)
            target = tile_dir / "sprites" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(p.read_bytes())
            assets.append({**record, "artifact": str(target.relative_to(ROOT)), "license": "MIT"})
    for url, name in [("https://raw.githubusercontent.com/protomaps/basemaps-assets/main/fonts/OFL.txt", "LICENSE.NOTO.txt"),
                      ("https://raw.githubusercontent.com/tangrams/icons/master/LICENSE.md", "LICENSE.SPRITES.txt")]:
        p, record = fetch(url, name)
        target = tile_dir / name
        target.write_text("\n".join(line.rstrip() for line in p.read_text().splitlines()).rstrip() + "\n")
        assets.append({**record, "artifact": "data/tiles/" + name, "artifact_sha256": sha(target),
                       "transformation": "Trailing whitespace normalized; legal text unchanged"})
    s = source("protomaps", "Protomaps daily basemap", URL, "ODbL-1.0 (data); BSD-3-Clause (tools); OFL-1.1 (fonts); MIT (sprites)",
               "© Protomaps; © OpenStreetMap contributors", CONFIG["tiles"]["snapshot"] + "T00:00:00Z", meta["retrieved_at"],
               "Offline regional archive extracted from pinned daily build, zooms 0–15. Attribution must remain visible on map.")
    dump(RAW / "tiles_stage.json", {"source": s, "download": meta, "assets": assets})
    print(str(output), output.stat().st_size, flush=True)


if __name__ == "__main__":
    run()
