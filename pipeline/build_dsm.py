"""Offline GeoTIFF-to-numeric-UTM conversion; GDAL is absent from runtime.

python -m pipeline.build_dsm tile1.tif tile2.tif --output data/geo/terrain_30m.npz
Requires rasterio only in the offline pipeline environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def convert(inputs, output, *, bbox=(39.35, 43.10, 40.45, 43.98), resolution=30):
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject, transform_bounds

    west, south, east, north = transform_bounds(4326, 32637, *bbox, densify_pts=21)
    width, height = int(np.ceil((east-west)/resolution)), int(np.ceil((north-south)/resolution))
    affine = from_origin(west, north, resolution, resolution)
    destination = np.full((height, width), np.nan, dtype=np.float32)
    metadata = []
    for input_path in inputs:
        path = Path(input_path)
        with rasterio.open(path) as source:
            layer = np.full_like(destination, np.nan)
            reproject(rasterio.band(source, 1), layer, src_transform=source.transform,
                      src_crs=source.crs, dst_transform=affine, dst_crs=CRS.from_epsg(32637),
                      src_nodata=source.nodata, dst_nodata=np.nan, resampling=Resampling.bilinear)
            valid = np.isfinite(layer)
            destination[valid] = layer[valid]
            metadata.append({"filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                             "native_crs": str(source.crs), "native_shape": list(source.shape)})
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, terrain=destination, x_min=np.asarray(west), y_max=np.asarray(north),
                        cell=np.asarray(float(resolution)))
    document = {"source_id": "copernicus_dem", "inputs": metadata, "bbox_wgs84": list(bbox), "crs": "EPSG:32637",
                "resolution_m": resolution, "vertical_datum": "EGM2008", "surface": "DSM, not bare-earth",
                "resampling": "bilinear", "valid_fraction": float(np.isfinite(destination).mean()),
                "limitation": "No extrapolation outside downloaded tiles. Building bases are estimated separately at 10th percentile; residual canopy/roof errors remain."}
    output.with_suffix(".json").write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2)+"\n")
    return document


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, default=Path("data/geo/terrain_30m.npz"))
    args = parser.parse_args()
    print(json.dumps(convert(args.inputs, args.output), ensure_ascii=False))
