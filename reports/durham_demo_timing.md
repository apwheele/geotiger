# Durham full-data demo timing

This run used the cached public Durham crime table and one local geocoder built
from the 2024 TIGER/Line ranges for Durham, Orange, and Wake Counties. Each
county's direct `pygris` result was cached separately and concatenated in
memory. The working frame corrects the one known Wake TIGER typo on Weymouth
Woods Trail (`25799` to `2799`); the downloaded cache remains unchanged.

| Quantity | Value |
| --- | ---: |
| Crime input rows | 135,088 |
| Durham County TIGER segments | 15,799 |
| Orange County TIGER segments | 9,777 |
| Wake County TIGER segments | 58,130 |
| Combined TIGER segments | 83,706 |
| Expanded reference rows | 7,697,931 |
| Prepared intersection points | 37,337 |
| Interpolation CRS | EPSG:2264 |
| Interpolation offsets | 0 m end / 0 m side |
| DuckDB threads | 4 |
| Candidate rows, deduplicated run | 768,831 |
| Automatic matches | 129,110 (95.57%) |
| Review | 283 (0.21%) |
| Unmatched | 5,695 (4.22%) |

The fresh preparation with intersections took **173.34 seconds** on the
development workstation. This is a one-time operation; subsequent runs read
the prepared Parquet table and DuckDB database from the local cache.

## Geocoding timing

Both modes returned the same 129,110 matches, 283 review rows, and 5,695
unmatched rows:

| Mode | Candidate-query inputs | Parse | Candidate query | Total geocode | Throughput |
| --- | ---: | ---: | ---: | ---: | ---: |
| Normal | 135,088 | 6.227 s | 10.672 s | 21.577 s | 6,261/s |
| `deduplicate_inputs=True` | 12,837 | 3.283 s | 4.644 s | 12.803 s | 10,552/s |

Blocking partitions intersections from ordinary addresses and tries exact,
spacing, canonical-name/route, and phonetic keys in progressively broader
indexed passes. The broad street-signature fallback remains disabled. With
deduplication, repeated address/locality combinations are parsed and queried
once, then expanded back to every original crime record.

The 100-block house window and component-aware street matching raise the
automatic match rate from about 93.8% to **95.57%**. Deduplicated throughput
stays above the package's 10,000-records-per-second target on this workload.
