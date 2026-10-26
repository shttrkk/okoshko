"""Bounded quarterly performance coverage; absent tests remain unknown."""
import json
import calendar

import duckdb

from pipeline.common import CONFIG, COVERAGE_BBOXES, RAW, SEED, dump, read, sha, source, utcnow


def run():
    records, downloads, failures = [], [], []
    c = duckdb.connect()
    c.execute("LOAD httpfs; LOAD spatial; SET threads=2; SET memory_limit='1GB';")
    year, quarter = CONFIG["ookla"]["year"], CONFIG["ookla"]["quarter"]
    quarter_label = f"{year}Q{quarter}"
    for kind in ["fixed", "mobile"]:
        url = f"https://ookla-open-data.s3.us-west-2.amazonaws.com/parquet/performance/type={kind}/year={year}/quarter={quarter}/{CONFIG['ookla']['quarter_start']}_performance_{kind}_tiles.parquet"
        path = RAW / f"ookla_{kind}.json"
        mp = RAW / f"ookla_{kind}.metadata.json"
        try:
            if path.exists() and mp.exists() and read(mp).get("coverage_bboxes") == COVERAGE_BBOXES and read(mp).get("url") == url:
                rows, meta = read(path), read(mp)
            else:
                filters = [f"(tile_x BETWEEN {w} AND {e} AND tile_y BETWEEN {s} AND {n})" for w, s, e, n in COVERAGE_BBOXES]
                sql = f"SELECT quadkey,tile,tile_x,tile_y,avg_d_kbps,avg_u_kbps,avg_lat_ms,tests,devices FROM read_parquet('{url}') WHERE " + " OR ".join(filters)
                result = c.execute(sql)
                names = [d[0] for d in result.description]
                rows = [dict(zip(names, r), kind=kind, quarter=quarter_label, source_id="ookla") for r in result.fetchall()]
                dump(path, rows)
                meta = dict(url=url, retrieved_at=utcnow(), sha256=sha(path), bytes=path.stat().st_size, sql=sql,
                            coverage_bboxes=COVERAGE_BBOXES, sha_scope="materialized bbox query result")
                dump(mp, meta)
            downloads.append(meta)
            records.extend(rows)
            print(f"Ookla {kind}: {len(rows)} covered tiles", flush=True)
        except Exception as e:
            failures.append({"source": "ookla_" + kind, "error": str(e)[:500]})
    dump(SEED / "connectivity.json", {"tiles": records, "quarter": quarter_label, "coverage_bboxes": COVERAGE_BBOXES,
        "status": "available" if records else "unknown", "limitations": ["Aggregated area Speedtest samples are not hotel Wi-Fi measurements.", "No tile/test coverage means unknown, never zero throughput."], "failures": failures})
    sources = [source("ookla", "Ookla Open Data Speedtest performance", "https://registry.opendata.aws/speedtest-global-performance/", "CC-BY-NC-SA-4.0",
                     "Speedtest by Ookla Global Fixed and Mobile Network Performance Maps, based on analysis by Ookla of Speedtest Intelligence data for 2026Q3. Provided by Ookla and accessed 2026-10-26.",
                     f"{year}-{quarter*3:02d}-{calendar.monthrange(year,quarter*3)[1]:02d}T23:59:59Z", downloads[0]["retrieved_at"],
                     "Noncommercial prototype. Per-tile sample counts preserved; not a promise about hotel connectivity.")] if records else []
    dump(RAW / "ookla_stage.json", {"sources": sources, "downloads": downloads, "counts": {"tiles": len(records)}, "failures": failures,
         "limitations": [] if records else ["No public Ookla 2026Q3 fixed/mobile tiles with centroid in either study area; connectivity stays unknown."]})


if __name__ == "__main__":
    run()
