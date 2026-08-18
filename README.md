# GeoTIGER

GeoTIGER is a local-first US address geocoder. It expands Census TIGER/Line
address ranges into a reusable point-address table, stores that table in
DuckDB, and resolves input records with deterministic blocking plus fuzzy
component scoring. Once the reference database has been prepared, geocoding
does not make network requests and input records stay on the local machine.

The package is designed for analysts who need a reproducible alternative to a
hosted geocoding API or desktop GIS geocoder:

- TIGER/Line address ranges are the default source, downloaded explicitly with
  `pygris` or supplied as a GeoDataFrame/GeoParquet file.
- Street numbers are expanded one-by-one with parity-aware interpolation on the
  correct side of each street segment.
- Interpolation is performed in a projected CRS and accounts for endpoint and
  side offsets, including segments whose geometry has been clipped.
- DuckDB performs the blocking join in parallel; RapidFuzz computes normalized
  Levenshtein scores locally.
- Every potential candidate is returned, alongside the best match, score,
  margin, status, and run timings.
- Folium maps are generated without a default basemap, so viewing a result does
  not require sending data to a tile service.

## Quick start

```powershell
uv sync --extra dev

# Download TIGER/Line ranges (this is the only network step).
uv run geotiger download --state NC --county Durham --year 2024 --output data/durham_ranges.parquet

# Prepare a local reusable DuckDB reference database.
uv run geotiger prepare --ranges data/durham_ranges.parquet --database data/durham.duckdb

# Geocode a CSV with address, city, state, and zip columns.
uv run geotiger geocode --database data/durham.duckdb --input data/addresses.csv \
  --output results/durham_geocoded.parquet --address-column address \
  --city-column city --state-column state --zip-column zip
```

The Python API is intentionally explicit:

```python
import pandas as pd

from geotiger import GeoTIGERStore, Geocoder, prepare_ranges

store = GeoTIGERStore("data/durham.duckdb")
store.create()
prepare_ranges(ranges_gdf, store, source="tiger_2024")

inputs = pd.read_csv("data/addresses.csv")
result = Geocoder(store).geocode(
    inputs,
    address_column="address",
    city_column="city",
    state_column="state",
    zip_column="zip",
)

result.matches.to_parquet("results/matches.parquet")
result.candidates.to_parquet("results/all_potential_matches.parquet")
print(result.timings.to_dict())
```

## Matching behavior

The default blocking rules are intentionally conservative. A candidate must
share the input state, and when supplied, city and ZIP code. Those fields are
not fuzzy-resolved across blocks. Candidates are additionally limited to a
street initial block and a configurable house-number window. Within a block,
the score is a weighted, available-field-normalized average of:

| Component | Default weight |
| --- | ---: |
| House number | 0.40 |
| Street name and type | 0.35 |
| City | 0.10 |
| State | 0.05 |
| ZIP code | 0.10 |

The default automatic assignment threshold is 90. Scores from 75 through 90
are marked `review`; lower scores are `unmatched`. `min_margin` can be raised
from its default of 0 to require a score gap from the runner-up when a project
needs especially conservative automatic assignment. Thresholds, weights,
blocking, and interpolation settings are all configurable.

## Local/privacy boundary

`download` and `from_pygris` explicitly fetch TIGER data. `prepare` and
`geocode` operate on local inputs only. GeoTIGER does not call a geocoding API.
The default result map has no basemap tiles; if you opt into online tiles,
Folium will request them when the HTML map is viewed.

## Performance

Run the included reproducible synthetic benchmark:

```powershell
uv run python scripts/benchmark.py --n 10000 --out reports/timing_10k.json
```

The generated JSON records hardware, Python/package versions, reference-table
size, candidate counts, phase timings, and throughput. See
[`docs/timing.md`](docs/timing.md) for the benchmark methodology and the
checked-in baseline report.

## Development

```powershell
uv run pytest
uv run ruff check .
```

GeoTIGER is MIT licensed. Census TIGER/Line data remains subject to its own
US Census Bureau terms and attribution requirements.
