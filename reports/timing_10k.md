# GeoTIGER timing report

Generated: `2026-08-19 00:42:05 +0100`  
Machine: `Windows-10-10.0.19045-SP0`  
Python: `3.12.12`

## Workload

- Input rows: **10,000**
- Prepared reference rows: **20,000**
- Candidate rows scored: **477,500**
- DuckDB threads: **4**

## Timings

| Phase | Seconds |
| --- | ---: |
| Prepare/interpolate ranges | 2.238 |
| Load DuckDB reference | 0.151 |
| Parse inputs | 1.108 |
| DuckDB candidate query | 5.568 |
| Fuzzy scoring | 1.269 |
| Match aggregation | 1.237 |
| Geocoding total | 9.184 |

Geocoding throughput was **1088.8 input rows/second**.

## Outcomes

- Automatically matched: **10,000**
- Review: **0**
- Unmatched: **0**

This is a synthetic, warm-process benchmark. It measures local parsing,
DuckDB blocking, and RapidFuzz scoring; it does not include downloading
TIGER/Line data. Actual runtimes vary with CPU, locality block sizes, TIGER
vintage, candidate density, and whether the DuckDB file is already on disk.
