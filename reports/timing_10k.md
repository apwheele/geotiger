# GeoTIGER timing report

Generated: `2026-08-19 17:55:54 +0100`

Machine: `Windows-10-10.0.19045-SP0`
Python: `3.12.12`

## Workload

- Input rows: **10,000**
- Prepared reference rows: **20,000**
- Candidate rows scored: **10,000**
- Lookup hits: **0**
- Historical-cache hits: **0**
- DuckDB threads: **4**

## Timings

| Phase | Seconds |
| --- | ---: |
| Prepare/interpolate ranges | 0.420 |
| Load DuckDB reference | 0.298 |
| Parse inputs | 0.697 |
| Explicit lookup table | 0.026 |
| Historical cache | 0.019 |
| DuckDB candidate query | 0.165 |
| Fuzzy scoring | 0.036 |
| Match aggregation | 0.042 |
| Geocoding total | 1.030 |

Geocoding throughput was **9706.9 input rows/second**.

## Outcomes

- Automatically matched: **10,000**
- Review: **0**
- Unmatched: **0**

This is a synthetic, warm-process benchmark. It measures local parsing,
DuckDB blocking, and RapidFuzz scoring; it does not include downloading
TIGER/Line data. Actual runtimes vary with CPU, locality block sizes, TIGER
vintage, candidate density, and whether the DuckDB file is already on disk.
