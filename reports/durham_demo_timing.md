# Durham full-data demo timing

This run used the cached public Durham crime table and cached Durham County
TIGER/Line ranges on the development workstation.

| Quantity | Value |
| --- | ---: |
| Crime input rows | 135,088 |
| Expanded reference rows | 1,550,226 |
| Prepared intersection points | 7,622 |
| Interpolation CRS | EPSG:2264 |
| Interpolation offsets | 0 m end / 0 m side |
| DuckDB threads | 4 |
| Candidate rows | 311,243 |
| Automatic matches | 126,061 |
| Review | 302 |
| Unmatched | 8,725 |

| Phase | Seconds |
| --- | ---: |
| TIGER preparation with intersections (fresh, vectorized) | 28.333 |
| Input parsing (deduplicated) | 1.926 |
| DuckDB candidate query | 2.185 |
| Component-aware fuzzy scoring | 0.898 |
| Aggregation | 0.592 |
| Geocoding total | 6.449 |

The main deduplicated geocoding pass processed **20,947 input rows/second**.
Blocking partitions intersections from ordinary addresses and tries exact,
spacing, canonical-name/route, and phonetic keys in progressively broader
indexed passes. The broad street-signature fallback remains disabled. Street
name, suffix, and directional components are scored separately, and
`match_method` identifies the pass that supplied each candidate.

With `deduplicate_inputs=True`, repeated address/locality combinations are
parsed and queried once, then expanded back to every original input row. The
one-time TIGER expansion is separate from geocoding and is cached locally
afterward. A fresh expansion of the same 15,799 segments took 28.333 seconds,
produced 1,550,226 rows including 7,622 intersections, and populated all
canonical and phonetic keys.

## Repeated-input benchmark

On the same 135,088-row input, both modes returned 126,061 matches, 302 review
rows, and 8,725 unmatched rows:

| Mode | Candidate-query inputs | Parse | Candidate query | Total geocode | Throughput |
| --- | ---: | ---: | ---: | ---: | ---: |
| Normal | 135,088 | 4.928 s | 9.906 s | 17.076 s | 7,911/s |
| `deduplicate_inputs=True` | 12,841 | 1.926 s | 2.185 s | 6.449 s | 20,947/s |

The intersection-only join now reads the separate `address_intersections`
table. This is more useful for the bulk hash join than a conventional index on
the mixed table; the main address table still handles ordinary street inputs.
