"""Benchmark explicit aliases and historical-cache reuse."""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import pandas as pd
from benchmark import synthetic_inputs, synthetic_ranges

from geotiger import Geocoder, GeocoderConfig, GeoTIGERStore, InterpolationConfig, prepare_ranges


def make_reference() -> pd.DataFrame:
    return prepare_ranges(
        synthetic_ranges(100),
        config=InterpolationConfig(end_offset_m=5, side_offset_m=5),
        source="shortcut-benchmark",
    )


def make_store(prepared: pd.DataFrame) -> GeoTIGERStore:
    store = GeoTIGERStore(":memory:")
    store.ingest_candidates(prepared)
    return store


def report_markdown(report: dict) -> str:
    def row(label: str, value: dict) -> str:
        timing = value["timings"]
        return (
            f"| {label} | {value.get('populate_seconds', 0.0):.3f} | "
            f"{timing['total_seconds']:.3f} | {timing['throughput_per_second']:.1f} | "
            f"{timing.get('lookup_hit_count', 0):,} | "
            f"{timing.get('history_cache_hit_count', 0):,} |"
        )

    return f"""# GeoTIGER shortcut timing report

Generated: {report['generated_at']}
Machine: {report['platform']}
Python: {report['python']}

## Workload

- Input rows: **{report['input_rows']:,}**
- Prepared reference rows: **{report['reference_rows']:,}**
- DuckDB threads: **{report['duckdb_threads']}**

## Comparison

| Run | Populate local table (s) | Geocoding total (s) | Rows/sec | Lookup hits | Cache hits |
| --- | ---: | ---: | ---: | ---: | ---: |
{row('Ordinary candidate matching', report['baseline'])}
{row('Explicit alias lookup', report['lookup'])}
{row('Historical cache reuse', report['history_cache'])}

The population column is the one-time local cost to add the lookup or
historical cache table. Geocoding runs are local and include parsing,
shortcut-table checks, scoring/aggregation, and result assembly. Alias and
cache hits bypass ordinary candidate scoring.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=10_000)
    parser.add_argument("--out", type=Path, default=Path("reports/timing_shortcuts.json"))
    args = parser.parse_args()

    started = time.perf_counter()
    prepared = make_reference()
    input_rows = synthetic_inputs(100, max(1, args.n // 100)).head(args.n)
    reference_seconds = time.perf_counter() - started

    baseline_store = make_store(prepared)
    baseline = Geocoder(baseline_store).geocode(input_rows)

    cache_store = make_store(prepared)
    cache_geocoder = Geocoder(cache_store)
    populate_started = time.perf_counter()
    cache_geocoder.cache_result(baseline)
    cache_populate_seconds = time.perf_counter() - populate_started
    history_cache = Geocoder(cache_store).geocode(input_rows)

    id_by_address = (
        prepared.loc[prepared["parity"].eq("even")]
        .drop_duplicates(["house_number", "city_norm", "zip5"])
        .set_index(["house_number", "city_norm", "zip5"])["address_id"]
        .to_dict()
    )
    lookup_rows = []
    for index, row in input_rows.reset_index(drop=True).iterrows():
        key = (
            1000 + (index % max(1, args.n // 100)) * 2,
            str(row["city"]).upper(),
            str(row["zip"]),
        )
        lookup_rows.append(
            {
                "alias": f"McDonalds First St {index}",
                "address_id": id_by_address[key],
                "city": row["city"],
                "state": "NC",
                "zip": row["zip"],
            }
        )
    lookup_store = make_store(prepared)
    lookup_geocoder = Geocoder(
        lookup_store,
        config=GeocoderConfig(use_history_cache=False),
    )
    lookup_table = pd.DataFrame(lookup_rows)
    lookup_populate_started = time.perf_counter()
    lookup_store.ingest_lookup(lookup_table)
    lookup_populate_seconds = time.perf_counter() - lookup_populate_started
    aliases = lookup_table[["alias", "city", "state", "zip"]].rename(
        columns={"alias": "address"}
    )
    lookup = lookup_geocoder.geocode(aliases)

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "input_rows": len(input_rows),
        "reference_rows": len(prepared),
        "reference_prepare_seconds": reference_seconds,
        "duckdb_threads": baseline_store.threads,
        "baseline": {"populate_seconds": 0.0, "timings": baseline.timings.to_dict()},
        "lookup": {
            "populate_seconds": lookup_populate_seconds,
            "timings": lookup.timings.to_dict(),
        },
        "history_cache": {
            "populate_seconds": cache_populate_seconds,
            "timings": history_cache.timings.to_dict(),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    args.out.with_suffix(".md").write_text(report_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
