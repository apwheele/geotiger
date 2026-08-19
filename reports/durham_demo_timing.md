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
| Expanded reference rows | 7,698,884 |
| Prepared intersection points | 37,410 |
| Interpolation CRS | EPSG:2264 |
| Interpolation offsets | 0 m end / 0 m side |
| DuckDB threads | 4 |
| Candidate rows, deduplicated run | 339,768 |
| Automatic matches | 126,735 |
| Review | 370 |
| Unmatched | 7,983 |

The fresh, vectorized preparation with intersections took **168.31 seconds**
on the development workstation. This is a one-time operation; subsequent runs
read the prepared Parquet table and DuckDB database from the local cache.

## Geocoding timing

Both modes returned the same 126,735 matches, 370 review rows, and 7,983
unmatched rows:

| Mode | Candidate-query inputs | Parse | Candidate query | Total geocode | Throughput |
| --- | ---: | ---: | ---: | ---: | ---: |
| Normal | 135,088 | 4.848 s | 12.749 s | 20.423 s | 6,614/s |
| `deduplicate_inputs=True` | 12,841 | 1.764 s | 4.571 s | 9.829 s | 13,744/s |

Blocking partitions intersections from ordinary addresses and tries exact,
spacing, canonical-name/route, and phonetic keys in progressively broader
indexed passes. The broad street-signature fallback remains disabled. With
deduplication, repeated address/locality combinations are parsed and queried
once, then expanded back to every original crime record.

Relative to the former Durham County-only reference, the combined geocoder
adds 674 automatic matches and reduces unmatched records by 742. The larger
DuckDB tables make candidate retrieval slower, but the deduplicated run remains
above the package's 10,000-records-per-second target on this workload.
