# Timing methodology

`scripts/benchmark.py` creates synthetic horizontal TIGER-like street ranges,
expands both parity sides, loads them into an in-memory DuckDB database, and
geocodes a deterministic input table. The default workload contains 10,000
inputs. It reports each phase separately:

1. range interpolation and point projection;
2. DuckDB reference-table ingestion;
3. `usaddress` parsing;
4. exact historical-cache lookup;
5. exact explicit-alias lookup;
6. blocked candidate query;
7. normalized-Levenshtein scoring;
8. best-match aggregation and status assignment.

The benchmark is intentionally local and synthetic. It does not download
TIGER data and does not make any geocoding requests. Candidate density is the
main variable that can make a real run slower: a broad ZIP/city block or a
large house-number tolerance increases the number of fuzzy comparisons.
GeoTIGER therefore uses adaptive blocking by default: it tries exact
normalized street and exact expanded house number first, then tries canonical
street-name and phonetic keys, applies house-number tolerance, and finally
uses broad street-signature fallback only for unresolved records. The Durham
notebook disables the broadest fallback but keeps the indexed variant passes,
which handle common component differences without a cross join.

Run it with:

```powershell
uv run python scripts/benchmark.py --n 10000 --out reports/timing_10k.json
```

The generated JSON is machine-readable and the matching `.md` file is a
compact analyst report. Treat the checked-in report as a reproducibility
example rather than a universal performance guarantee; rerun it on the target
analyst workstation when timing is operationally important.

The checked-in benchmark has empty shortcut tables, so its lookup and cache
phases measure the low overhead of checking those local tables. Use
`scripts/benchmark_shortcuts.py` to compare a normal candidate run with
explicit alias hits and historical-cache hits.
