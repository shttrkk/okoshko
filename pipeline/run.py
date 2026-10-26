"""Reproducible data pipeline entrypoint: python -m pipeline.run."""
import argparse
import importlib
import os

STAGES = ["fetch_osm", "fetch_overture", "merge_hotels", "fetch_dem", "fetch_open", "fetch_ookla", "fetch_tiles", "export_seed", "validate"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["all"] + STAGES, default="all")
    parser.add_argument("--region", default=os.environ.get("REGION", "pipeline/regions/sochi.yaml"), help="YAML region configuration (or REGION env)")
    args = parser.parse_args()
    os.environ["REGION"] = args.region
    for name in STAGES if args.stage == "all" else [args.stage]:
        print("Starting " + name, flush=True)
        importlib.import_module("pipeline." + name).run()


if __name__ == "__main__":
    main()
