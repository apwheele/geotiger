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
| Candidate rows | 284,975 |
| Automatic matches | 115,919 |
| Review | 10 |
| Unmatched | 19,159 |

| Phase | Seconds |
| --- | ---: |
| TIGER preparation with intersections (fresh, vectorized) | 26.602 |
| Input parsing (per-run normalization cache) | 3.969 |
| DuckDB candidate query | 6.465 |
| Fuzzy scoring | 0.142 |
| Aggregation | 0.546 |
| Geocoding total | 11.856 |

The geocoding pass processed **11,394 input rows/second**. Blocking partitions
intersection inputs from ordinary addresses: intersections use one exact,
order-independent `intersection_key` join, while ordinary addresses use the
exact-street and house-number-tolerance passes. The run used exact
normalized-street matching with house-number tolerance and disabled the broad
street-prefix fallback (`street_fallback=False`) because the Durham public
addresses are standardized block addresses. A per-run normalization cache
avoids reparsing repeated address/locality combinations. The one-time TIGER
expansion is separate from the geocoding run and is cached locally afterward.
The vectorized preparation path reduced a fresh expansion of the same 15,799
segments from the prior 167.445-second run to 26.602 seconds while adding 7,622
intersection points; subsequent notebook runs load the prepared cache instead.
