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
| Candidate rows | 280,853 |
| Automatic matches | 111,518 |
| Review | 10 |
| Unmatched | 23,560 |

| Phase | Seconds |
| --- | ---: |
| TIGER preparation | 167.445 |
| Input parsing | 6.491 |
| DuckDB candidate query | 5.688 |
| Fuzzy scoring | 0.130 |
| Aggregation | 0.353 |
| Geocoding total | 12.665 |

The geocoding pass processed **10,666 input rows/second**. This run used exact
normalized-street matching with house-number tolerance and disabled the broad
street-prefix fallback (`street_fallback=False`) because the Durham public
addresses are standardized block addresses. The one-time TIGER expansion is
separate from the geocoding run and is cached locally afterward.

