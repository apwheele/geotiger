# GeoTIGER shortcut timing report

Generated: 2026-08-19 12:01:26 +0100
Machine: Windows-10-10.0.19045-SP0
Python: 3.12.12

## Workload

- Input rows: **10,000**
- Prepared reference rows: **20,000**
- DuckDB threads: **4**

## Comparison

| Run | Populate local table (s) | Geocoding total (s) | Rows/sec | Lookup hits | Cache hits |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ordinary candidate matching | 0.000 | 0.585 | 17102.6 | 0 | 0 |
| Explicit alias lookup | 1.482 | 0.537 | 18611.0 | 10,000 | 0 |
| Historical cache reuse | 0.704 | 0.471 | 21230.4 | 0 | 10,000 |

The population column is the one-time local cost to add the lookup or
historical cache table. Geocoding runs are local and include parsing,
shortcut-table checks, scoring/aggregation, and result assembly. Alias and
cache hits bypass ordinary candidate scoring.
