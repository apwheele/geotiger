# Durham full-data demo timing

This run used the cached public Durham crime table and cached Durham County
TIGER/Line ranges on the development workstation.

| Quantity | Value |
| --- | ---: |
| Crime input rows | 135,088 |
| Expanded reference rows | 1,542,604 |
| Interpolation CRS | EPSG:2264 |
| Interpolation offsets | 0 m end / 0 m side |
| DuckDB threads | 4 |
| Candidate rows | 280,353 |
| Automatic matches | 111,517 |
| Review | 10 |
| Unmatched | 23,561 |

| Phase | Seconds |
| --- | ---: |
| TIGER preparation (fresh, vectorized) | 21.191 |
| Input parsing | 7.672 |
| DuckDB candidate query | 6.205 |
| Fuzzy scoring | 0.170 |
| Aggregation | 0.774 |
| Geocoding total | 15.538 |

The geocoding pass processed **8,694 input rows/second**. This run used exact
normalized-street matching with house-number tolerance and disabled the broad
street-prefix fallback (`street_fallback=False`) because the Durham public
addresses are standardized block addresses. The one-time TIGER expansion is
separate from the geocoding run and is cached locally afterward. The vectorized
preparation path reduced a fresh expansion of the same 15,799 segments from the
prior 167.445-second run to 21.191 seconds; subsequent notebook runs load the
prepared cache instead.
