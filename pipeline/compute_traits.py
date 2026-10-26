"""Materialize optional traits in an isolated runtime directory, never in seed.

Example: python -m pipeline.compute_traits --output runtime/night-geo
The default includes all twelve solar months. Existing geo JSON/NPZ and the
hotel portfolio are read-only; the parent process reviews the resulting ledger.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from backend.contracts import Claim
from backend.quality import known_value
from geo.traits import METHODS, TRAITS, compute_trait


def materialize(data_dir: Path, output: Path, *, hotel_ids=None, characteristics=None, month=None):
    data_dir, output = Path(data_dir).resolve(), Path(output).resolve()
    if output == data_dir or output.is_relative_to(data_dir/"seed") or output.is_relative_to(data_dir/"geo"):
        raise ValueError("output must be isolated from source seed/geo artifacts")
    output.mkdir(parents=True, exist_ok=True)
    hotels = json.loads((data_dir/"seed"/"hotels.json").read_text())
    requested = set(hotel_ids or [])
    if requested:
        missing = requested-{hotel["id"] for hotel in hotels}
        if missing:
            raise ValueError("unknown hotel IDs: "+", ".join(sorted(missing)))
        hotels = [hotel for hotel in hotels if hotel["id"] in requested]
    characteristics = sorted(characteristics or TRAITS)
    if set(characteristics)-TRAITS:
        raise ValueError("unsupported geo characteristic")
    counts = {cid: {"claims": 0, "hotels_with_claims": 0, "known_hotels": 0,
                    "known_hotels_confidence_ge_06": 0, "positive_hotels": 0} for cid in characteristics}
    warnings = defaultdict(list)
    # The shipped new-house demonstration is active on first startup. Its
    # baseline must not be exported as current sunlight after that change.
    active_geo = {}
    event_path = data_dir/"seed"/"demo_change_event.json"
    if event_path.is_file():
        event = json.loads(event_path.read_text())
        if event.get("hotel_id"):
            artifact = event.get("geo", {}).get("artifact")
            active_geo[event["hotel_id"]] = artifact
    ledger = output/"geo_traits.jsonl.gz"
    temporary = output/"geo_traits.jsonl.gz.tmp"
    with temporary.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as stream:
        for index, hotel in enumerate(hotels, 1):
            for cid in characteristics:
                solar = cid in {"morning_sun", "evening_heat"}
                if cid == "railway_noise" and hotel["id"] in active_geo:
                    result = {"claims": [], "warnings": ["Активное изменение требует нового расчёта железнодорожного экранирования"]}
                elif solar and hotel["id"] in active_geo and not active_geo[hotel["id"]]:
                    result = {"claims": [], "warnings": ["Активное изменение без authoritative geo artifact; солнечное значение неизвестно"]}
                else:
                    kwargs = {"geo_artifact": active_geo[hotel["id"]]} if solar and hotel["id"] in active_geo else {}
                    result = compute_trait(hotel, data_dir, cid, month=month, **kwargs)
                claims = result["claims"]
                for row in claims:
                    Claim.model_validate(row)
                    stream.write((json.dumps(row, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":"), allow_nan=False)+"\n").encode())
                counts[cid]["claims"] += len(claims)
                counts[cid]["hotels_with_claims"] += bool(claims)
                counts[cid]["known_hotels"] += any(known_value(c["value"]) for c in claims)
                counts[cid]["known_hotels_confidence_ge_06"] += any(known_value(c["value"]) and c["confidence"] >= .6 for c in claims)
                counts[cid]["positive_hotels"] += any(c["value"].get("available") is True for c in claims)
                if result["warnings"]:
                    warnings[cid].append({"hotel_id": hotel["id"], "warnings": result["warnings"]})
            if index % 25 == 0:
                print(f"Traits: {index}/{len(hotels)} hotels", flush=True)
    temporary.replace(ledger)
    report = {"version": 1, "generated_at": datetime.now(UTC).isoformat(), "hotels": len(hotels),
              "months": [month] if month else list(range(1, 13)), "methods": {cid: METHODS[cid] for cid in characteristics},
              "counts": counts, "warnings": dict(warnings),
              "authoritative_geo_overrides": active_geo,
              "ledger": {"name": ledger.name, "bytes": ledger.stat().st_size,
                         "sha256": hashlib.sha256(ledger.read_bytes()).hexdigest()},
              "coverage_definition": "At least one known trait value in any facade/floor/month; not same-room fulfilment or ground-truth accuracy",
              "limitations": ["No new hotel/floor identities; no existing seed or NPZ changed",
                              "Solar and noise are model proxies with reduced confidence",
                              "Missing OSM surface/accessibility tags remain unknown"]}
    (output/"report.json").write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)+"\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("runtime/night-geo"))
    parser.add_argument("--hotel-id", action="append")
    parser.add_argument("--characteristic", action="append", choices=sorted(TRAITS))
    parser.add_argument("--month", type=int, choices=range(1, 13))
    args = parser.parse_args(argv)
    report = materialize(args.data_dir, args.output, hotel_ids=args.hotel_id,
                         characteristics=args.characteristic, month=args.month)
    print(json.dumps({key: report[key] for key in ("hotels", "months", "counts", "ledger")}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
