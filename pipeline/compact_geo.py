"""Losslessly encode per-hotel output NPZ as deterministic ZIP_LZMA.

Scientific arrays, JSON, claims, terrain and LOS/input archives are never edited.
The apply mode first copies every original and its physical SHA-256 to /tmp.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from geo.io import read_json


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hotel_outputs(data_dir: Path) -> list[Path]:
    hotels = read_json(data_dir / "seed/hotels.json")
    paths = []
    for hotel_id in sorted({hotel["id"] for hotel in hotels}):
        if not re.fullmatch(r"(?:osm-(?:node|way)-\d+|overture-[0-9a-f-]+)", hotel_id):
            raise ValueError(f"Unexpected hotel ID: {hotel_id}")
        path = data_dir / "geo" / (hotel_id + ".npz")
        metadata = read_json(path.with_suffix(".json"))
        if metadata.get("hotel_id") != hotel_id or metadata.get("dsm", {}).get("artifact") != "/data/geo/" + path.name:
            raise ValueError(f"Archive is not a declared hotel output: {path}")
        if not path.is_file():
            raise FileNotFoundError(path)
        paths.append(path)
    return paths


def audit_output_hash_references(data_dir: Path, outputs: list[Path]) -> dict:
    """Physical output digests must not be an immutable scientific input."""
    digests = {sha256(path): path.name for path in outputs}
    paths = set((data_dir / "geo").glob("*.json")) | set((data_dir / "geo").glob("*.json.gz"))
    paths |= {data_dir / "seed/manifest.json", data_dir / "seed/sources.json"}
    paths |= set(data_dir.rglob("*.jsonl")) | set(data_dir.rglob("*.jsonl.gz"))
    references = []
    for path in sorted(paths):
        if not path.is_file():
            continue
        text = gzip.decompress(path.read_bytes()).decode() if path.suffix == ".gz" else path.read_text()
        for digest in set(re.findall(r"\b[0-9a-f]{64}\b", text)) & digests.keys():
            references.append({"metadata": str(path), "archive": digests[digest]})
    if references:
        raise ValueError("Output archives are referenced by physical SHA-256: " + json.dumps(references))
    return {"metadata_files_checked": len(paths), "physical_output_hash_references": 0}


def assert_equal(original: Path, encoded: Path) -> int:
    with np.load(original, allow_pickle=False) as before, np.load(encoded, allow_pickle=False) as after:
        if before.files != after.files:
            raise ValueError(f"Array keys/order changed: {original}")
        for key in before.files:
            a, b = before[key], after[key]
            if a.dtype.kind not in "biuf" or a.dtype != b.dtype or a.shape != b.shape or a.tobytes() != b.tobytes():
                raise ValueError(f"Scientific array changed: {original}:{key}")
        return len(before.files)


def backup_all(paths: list[Path], backup_dir: Path) -> dict:
    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = backup_dir / "originals.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        if set(manifest["files"]) != {path.name for path in paths}:
            raise ValueError("Backup belongs to a different hotel set")
        for path in paths:
            original = backup_dir / path.name
            if sha256(original) != manifest["files"][path.name]["sha256"]:
                raise ValueError(f"Backup SHA-256 mismatch: {original}")
            assert_equal(original, path)
        return manifest
    if any(backup_dir.iterdir()):
        raise ValueError("Use an empty backup directory or a complete originals.json backup")
    manifest = {"format": "npz-originals-v1", "created_at": datetime.now(UTC).isoformat(), "files": {}}
    for path in paths:
        destination = backup_dir / path.name
        shutil.copyfile(path, destination)
        digest = sha256(path)
        if sha256(destination) != digest:
            raise ValueError(f"Backup copy mismatch: {path}")
        manifest["files"][path.name] = {"bytes": path.stat().st_size, "sha256": digest}
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return manifest


def write_lzma(source: Path, destination: Path):
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_LZMA) as encoded:
        for member in original.infolist():
            if not member.filename.endswith(".npy") or "/" in member.filename or "\\" in member.filename:
                raise ValueError(f"Unexpected NPZ member: {member.filename}")
            info = zipfile.ZipInfo(member.filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o600 << 16
            info.compress_type = zipfile.ZIP_LZMA
            encoded.writestr(info, original.read(member.filename))


def compact(data_dir: Path, backup_dir: Path | None = None, *, apply: bool = False) -> dict:
    paths = hotel_outputs(data_dir)
    report = {"archives": len(paths), "original_bytes": sum(path.stat().st_size for path in paths),
              "encoding": "ZIP_LZMA", "excluded": ["terrain_30m.npz", "*.los.npz", "*-change-*.npz"],
              "apply": apply, "hash_audit": audit_output_hash_references(data_dir, paths)}
    if not apply:
        return report
    backup_dir = backup_dir or Path(tempfile.mkdtemp(prefix="okno-npz-originals-", dir="/tmp"))
    if not backup_dir.resolve().is_relative_to(Path("/tmp").resolve()):
        raise ValueError("Original archives must be backed up under /tmp")
    backup_all(paths, backup_dir)
    print(f"Backed up {len(paths)} original NPZ + SHA-256: {backup_dir}", flush=True)
    records = []
    for index, path in enumerate(paths, 1):
        handle = tempfile.NamedTemporaryFile(prefix=path.stem + "-", suffix=".npz.tmp", dir=path.parent, delete=False)
        temporary = Path(handle.name)
        handle.close()
        try:
            write_lzma(path, temporary)
            keys = assert_equal(backup_dir / path.name, temporary)
            # Replacement happens only after complete numeric/bitwise validation.
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        records.append({"name": path.name, "keys": keys, "bytes": path.stat().st_size, "sha256": sha256(path), "bitwise_equal": True})
        if index == 1 or index % 25 == 0 or index == len(paths):
            print(f"Verified ZIP_LZMA {index}/{len(paths)}", flush=True)
    for path in paths:
        assert_equal(backup_dir / path.name, path)
    report.update(backup_dir=str(backup_dir), encoded_bytes=sum(path.stat().st_size for path in paths), records=records)
    report["saved_bytes"] = report["original_bytes"] - report["encoded_bytes"]
    (backup_dir / "repack_report.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true", help="Apply only after backing up every original under /tmp")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    report = compact(args.data_dir, args.backup_dir, apply=args.apply)
    print(json.dumps({key: value for key, value in report.items() if key != "records"} | {"elapsed_seconds": round(time.perf_counter()-started, 3)}, sort_keys=True))


if __name__ == "__main__":
    main()
