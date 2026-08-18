"""Reproducible local benchmark for a 10k-row geocoding run."""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from geotiger import Geocoder, GeoTIGERStore, InterpolationConfig, prepare_ranges


def synthetic_ranges(street_count: int) -> gpd.GeoDataFrame:
    rows = []
    for index in range(street_count):
        longitude = -79.0 + index * 0.002
        rows.append(
            {
                "TLID": f"synthetic-{index}",
                "FULLNAME": "Main Street",
                "LFROMADD": 1000,
                "LTOADD": 1198,
                "RFROMADD": 1001,
                "RTOADD": 1199,
                "ZIPL": f"27{index:03d}",
                "ZIPR": f"27{index:03d}",
                "CITYL": f"City {index}",
                "CITYR": f"City {index}",
                "STUSPS": "NC",
                "geometry": LineString([(longitude, 35.9), (longitude + 0.001, 35.9)]),
            }
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def synthetic_inputs(street_count: int, rows_per_street: int) -> pd.DataFrame:
    rows = []
    for index in range(street_count):
        for offset in range(rows_per_street):
            number = 1000 + offset * 2
            rows.append(
                {
                    "record_id": len(rows),
                    "address": f"{number} Main St",
                    "city": f"City {index}",
                    "state": "NC",
                    "zip": f"27{index:03d}",
                }
            )
    return pd.DataFrame(rows)


def markdown_report(report: dict) -> str:
    timings = report["timings"]
    return f"""# GeoTIGER timing report

Generated: `{report['generated_at']}`  
Machine: `{report['platform']}`  
Python: `{report['python']}`

## Workload

- Input rows: **{report['input_rows']:,}**
- Prepared reference rows: **{report['reference_rows']:,}**
- Candidate rows scored: **{timings['candidate_count']:,}**
- DuckDB threads: **{timings['duckdb_threads']}**

## Timings

| Phase | Seconds |
| --- | ---: |
| Prepare/interpolate ranges | {report['prepare_seconds']:.3f} |
| Load DuckDB reference | {report['ingest_seconds']:.3f} |
| Parse inputs | {timings['parse_seconds']:.3f} |
| DuckDB candidate query | {timings['candidate_query_seconds']:.3f} |
| Fuzzy scoring | {timings['scoring_seconds']:.3f} |
| Match aggregation | {timings['aggregation_seconds']:.3f} |
| Geocoding total | {timings['total_seconds']:.3f} |

Geocoding throughput was **{timings['throughput_per_second']:.1f} input rows/second**.

## Outcomes

- Automatically matched: **{timings['matched_count']:,}**
- Review: **{timings['review_count']:,}**
- Unmatched: **{timings['unmatched_count']:,}**

This is a synthetic, warm-process benchmark. It measures local parsing,
DuckDB blocking, and RapidFuzz scoring; it does not include downloading
TIGER/Line data. Actual runtimes vary with CPU, locality block sizes, TIGER
vintage, candidate density, and whether the DuckDB file is already on disk.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=10_000, help="Number of synthetic input rows")
    parser.add_argument("--out", type=Path, default=Path("reports/timing_10k.json"))
    args = parser.parse_args()
    street_count = max(1, min(200, args.n // 100))
    rows_per_street = max(1, args.n // street_count)

    started = time.perf_counter()
    prepared = prepare_ranges(
        synthetic_ranges(street_count),
        config=InterpolationConfig(end_offset_m=5, side_offset_m=5),
        source="synthetic-benchmark",
    )
    prepare_seconds = time.perf_counter() - started
    store = GeoTIGERStore(":memory:")
    ingest_started = time.perf_counter()
    store.ingest_candidates(prepared)
    ingest_seconds = time.perf_counter() - ingest_started
    records = synthetic_inputs(street_count, rows_per_street).head(args.n)
    result = Geocoder(store).geocode(records)
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "input_rows": len(records),
        "reference_rows": len(prepared),
        "prepare_seconds": prepare_seconds,
        "ingest_seconds": ingest_seconds,
        "timings": result.timings.to_dict(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    args.out.with_suffix(".md").write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
