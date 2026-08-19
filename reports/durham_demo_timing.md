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
| Candidate rows | 286,305 |
| Automatic matches | 116,649 |
| Review | 10 |
| Unmatched | 18,429 |

| Phase | Seconds |
| --- | ---: |
| TIGER preparation with intersections (fresh, vectorized) | 25.784 |
| Input parsing (per-run normalization cache) | 4.179 |
| DuckDB candidate query | 8.344 |
| Fuzzy scoring | 0.160 |
| Aggregation | 0.517 |
| Geocoding total | 13.930 |

The geocoding pass processed **9,698 input rows/second**. Blocking partitions
intersection inputs from ordinary addresses: intersections use one exact,
order-independent `intersection_key` join, while ordinary addresses use the
exact-street and house-number-tolerance passes. The run used exact
normalized-street matching with house-number tolerance and disabled the broad
street-prefix fallback (`street_fallback=False`) because the Durham public
addresses are standardized block addresses. A per-run normalization cache
avoids reparsing repeated address/locality combinations. The one-time TIGER
expansion is separate from the geocoding run and is cached locally afterward.
The vectorized preparation path reduced a fresh expansion of the same 15,799
segments from the prior 167.445-second run to 25.784 seconds while adding 7,622
intersection points; subsequent notebook runs load the prepared cache instead.

## Repeated-input benchmark

On the same 135,088-row input, both modes returned 116,649 matches, 10 review
rows, and 18,429 unmatched rows:

| Mode | Candidate-query inputs | Candidate query | Total geocode | Throughput |
| --- | ---: | ---: | ---: | ---: |
| Normal | 135,088 | 6.996 s | 15.687 s | 8,612/s |
| `deduplicate_inputs=True` | 12,841 | 1.277 s | 6.808 s | 19,844/s |

The intersection-only join now reads the separate `address_intersections`
table. This is more useful for the bulk hash join than a conventional index on
the mixed table; the main address table still handles ordinary street inputs.
