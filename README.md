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
- Street intersections are prepared by default from crossing local street
  geometries and are matched with an order-independent two-street key. Input
  forms such as `Main St / First Ave`, `Main St AT First Ave`, and
  `Intersection of Main St & First Ave` are identified explicitly.
- Intersection points are geometric crossings; if the source does not carry
  grade-separation information, bridges and tunnels may need
  `InterpolationConfig(include_intersections=False)` or a local authoritative
  intersection table.
- Interpolation is performed in a local projected CRS (North Carolina defaults
  to EPSG:2264 state plane; other states use an appropriate NAD83 state-plane
  CRS when available) and accounts for endpoint and side offsets, including
  segments whose geometry has been clipped. Stored result coordinates remain
  WGS84 latitude/longitude for mapping.
- DuckDB performs the blocking join in parallel; RapidFuzz computes normalized
  Levenshtein scores locally.
- Every potential candidate is returned, alongside the best match, score,
  margin, status, and run timings.
- Folium maps and static Matplotlib maps are generated without a default
  basemap, so viewing a result does not require sending data to a tile service.

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

prepared = prepare_ranges(ranges_gdf, source="tiger_2024")
with GeoTIGERStore("data/durham.duckdb") as store:
    store.ingest_candidates(prepared, replace=True)

inputs = pd.read_csv("data/addresses.csv")
with GeoTIGERStore("data/durham.duckdb") as store:
    result = Geocoder(store).geocode(inputs)

result.matches.to_parquet("results/matches.parquet")
result.candidates.to_parquet("results/all_potential_matches.parquet")
print(result.timings.to_dict())
```

## Local address and parcel references

Local address points and parcels can be prepared into the same canonical table
as TIGER ranges. Address tables may use a full address plus longitude/latitude
or point geometry, or separate house-number and street columns. Parcel tables
may use a situs address and polygon geometry; polygons are reduced to an
interior representative point so the result stays inside the parcel.

```python
from geotiger import (
    CombinedGeocoder,
    GeoTIGERStore,
    prepare_addresses,
    prepare_combined,
    prepare_parcels,
    prepare_ranges,
    save_prepared,
)

address_points = prepare_addresses(
    address_points_gdf,
    address_column="SITE_ADDR",
    city_column="CITY",
    state="NC",
    zip_column="ZIP",
    source="county_address_points",
)
parcel_points = prepare_parcels(
    parcels_gdf,
    address_column="SITUS_ADDRESS",
    parcel_id_column="PIN",
    source="county_parcels",
)

# This is one prepared dataset, not one database per source. Already-prepared
# tables are accepted directly by prepare_combined.
prepared = prepare_combined(
    addresses=address_points,
    parcels=parcel_points,
    ranges=prepare_ranges(tiger_ranges_gdf, source="tiger_2024"),
)
save_prepared(prepared, "data/combined_reference.parquet")

with GeoTIGERStore("data/combined.duckdb") as store:
    geocoder = CombinedGeocoder.from_tables(
        store,
        addresses=address_points_gdf,
        parcels=parcels_gdf,
        ranges=tiger_ranges_gdf,
        address_options={"address_column": "SITE_ADDR"},
        parcel_options={"address_column": "SITUS_ADDRESS", "parcel_id_column": "PIN"},
        range_options={"source": "tiger_2024"},
    )
    result = geocoder.geocode(inputs)
```

`individual > parcel > tiger` is the default source preference used by
`CombinedGeocoder`. Match score remains primary; the preference is a
deterministic tie-break for otherwise equivalent candidates. Change it with
`source_preference=("parcel", "individual", "tiger")`. Prepared rows retain
`source_type`, `source`, `source_priority`, and `source_record_id`; selected
matches expose the corresponding `matched_source_*` fields for audit joins.
All reference inputs must include coordinates. A regular table's geometry is
assumed to be EPSG:4326 when no CRS is supplied; pass `input_crs` for projected
X/Y or geometry data. Only TIGER ranges require interpolation, and they still
use the local state-plane projection described above.

## Explicit aliases and historical cache

An explicit local lookup table is useful for business or landmark descriptions
that are not literal postal addresses. The table can point directly to a
prepared address ID:

```python
lookup = pd.DataFrame(
    [{
        "alias": "McDonalds First St",
        "address_id": "county_address_points:42:0",
        "city": "Durham",
        "state": "NC",
        "zip": "27514",
    }]
)
store.ingest_lookup(lookup)
```

Alternatively, `CombinedGeocoder.add_lookup_table` accepts an
`actual_address` column and resolves that target locally once before storing
the alias mapping. Lookup hits return `match_method="lookup"` and bypass
candidate matching.

Historical results can be persisted after a reviewed workflow:

```python
result = geocoder.geocode(inputs)
geocoder.cache_result(result)  # automatic matches only by default
next_result = geocoder.geocode(inputs)
```

The second run returns `match_method="history_cache"` for exact normalized
input keys. Use `GeocoderConfig(use_history_cache=False)` or
`GeocoderConfig(use_lookup_table=False)` to disable either shortcut layer.
The checked-in [shortcut timing report](reports/timing_shortcuts.md) measures
both population cost and hit-run throughput.

## Matching behavior

The default blocking rules are intentionally conservative. A candidate must
share the input state, and when supplied, city and ZIP code. Those fields are
not fuzzy-resolved across blocks. Candidates are additionally limited to a
compact first/last street-name signature and a configurable house-number window. Within a block,
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
blocking, and interpolation settings are all configurable. By default, the
candidate query first checks for an exact expanded house number and only uses
the tolerance window for records without an exact candidate. Set
`exact_house_number_first=False` when you need every address in the tolerance
window returned for review. Set `street_fallback=False` to keep only exact
normalized-street candidates (with house-number tolerance still available);
this is useful for a large, well-standardized local dataset.

Some raw TIGER/Line address-range vintages contain ZIP codes but no textual
city field. With `strict_locality=True`, an input city therefore will not match
an un-enriched reference row. Keep that setting for strict city/state/ZIP
validation, enrich the range table with local place names, or explicitly set
`strict_locality=False` when state/ZIP blocking is the intended rule.

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

The current synthetic baseline is about 20,000 input rows/second on the
development workstation. The full 135,088-row Durham demo processed about
11,394 input rows/second with 7,622 prepared intersection points; its one-time
intersection-aware TIGER expansion took 26.6 seconds after vectorization; see
[`reports/durham_demo_timing.md`](reports/durham_demo_timing.md). DuckDB uses
its configured thread pool for joins, while parsing and scoring use optimized
batch operations in the local Python process.

The 10,000-row shortcut benchmark processed explicit lookup hits at about
18,600 rows/second and historical-cache hits at about 21,200 rows/second.
Those figures include parsing, local shortcut queries, and result assembly;
the one-time table population costs were about 1.5 seconds for 10,000 aliases
and 0.7 seconds for 10,000 historical mappings on that run. See
[`reports/timing_shortcuts.md`](reports/timing_shortcuts.md).

## Development

```powershell
uv run pytest
uv run ruff check .
```

Run the full Durham demonstration with JupyterLab:

```powershell
uv sync --extra dev --extra demo
uv run jupyter lab notebooks/durham_demo.ipynb
```

For a minimal five-row workflow using Washington, DC address points, open
[`notebooks/dc_five_addresses_demo.ipynb`](notebooks/dc_five_addresses_demo.ipynb).
It shows preparation, local geocoding, timings, an offline static map, and an
offline Folium map. The Durham notebook includes an embedded unmatched-record
diagnostic; the written review is in
[`reports/durham_unmatched_review.md`](reports/durham_unmatched_review.md).

The compact public-data cache is included under
[`notebooks/assets`](notebooks/assets). Prepared DuckDB and geocoded outputs
are generated under `data/durham_demo/` and remain local/ignored.

GeoTIGER is MIT licensed. Census TIGER/Line data remains subject to its own
US Census Bureau terms and attribution requirements.
