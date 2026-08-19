# GeoTIGER

GeoTIGER is a local geocoding option for US address data. Download Census
TIGER/Line address ranges, prepare a local reference table, and geocode a
Pandas table without sending the address data to an online geocoder.

The download is the only network step. Preparation and geocoding are local.
Street interpolation uses a projected state-plane CRS; returned matches keep
WGS84 latitude/longitude.

## Quick start

```powershell
uv sync --extra dev
```

```python
import pandas as pd

from geotiger import (
    GeoTIGERStore,
    Geocoder,
    download_tiger_ranges,
    prepare_ranges,
    state_plane_crs,
)

# Network step; cache=True lets pygris reuse the download.
ranges = download_tiger_ranges(
    state="NC",
    county="Durham",
    year=2024,
    cache=True,
)

# Defaults include intersection preparation and use a local projection.
prepared = prepare_ranges(
    ranges,
    state="NC",
    source="tiger_2024_durham",
)

with GeoTIGERStore("data/durham.duckdb") as store:
    store.ingest_candidates(prepared, replace=True)

# Input address data can be an in-memory DataFrame; it needs no coordinates.
inputs = pd.DataFrame([
    {"address": "601 N Mangum St", "city": "Durham", "state": "NC", "zip": "27701"},
])
with GeoTIGERStore("data/durham.duckdb") as store:
    result = Geocoder(store).geocode(inputs)

result.matches[["address", "match_status", "match_latitude", "match_longitude"]]
```

For a whole state, use `county=None`:

```python
state_ranges = download_tiger_ranges("DE", county=None, year=2024, cache=True)
state_prepared = prepare_ranges(
    state_ranges,
    state="DE",
    source="tiger_2024_de",
)
```

For a large state, prepare counties one at a time and ingest them into the
same DuckDB file to limit peak memory use.

## Useful options

- `InterpolationConfig` controls the projected CRS, endpoint/side offsets, and
  intersection preparation. `include_intersections=True` is the default.
- `GeocoderConfig(deduplicate_inputs=True)` deduplicates repeated normalized
  inputs during candidate lookup, then expands the results back to every row.
- With `strict_locality=True` (the default), state/city/ZIP are locality
  blocks when supplied; default scoring therefore emphasizes street identity
  (`0.62`) and house-number proximity (`0.30`).
- `prepare_addresses` and `prepare_parcels` add local address points or parcel
  representatives to the same reference schema. `CombinedGeocoder` combines
  individual addresses, parcels, and TIGER ranges, preferring individual
  addresses, then parcels, then TIGER for otherwise equivalent matches.
- Local lookup aliases and a reviewed historical-address cache can bypass
  repeated candidate matching.

## Demo notebooks

- [DC five-address demo](notebooks/dc_five_addresses_demo.ipynb): downloads and
  prepares DC TIGER data, geocodes five coordinate-free inputs, and renders a
  static map plus an offline Folium map.
- [Durham crime demo](notebooks/durham_demo.ipynb): prepares Durham TIGER data
  and geocodes the large public crime table locally.
- [Statewide TIGER demo](notebooks/statewide_tiger_demo.ipynb): builds a whole
  Delaware reference with `county=None` and geocodes a sample from it.

The Durham unmatched review is in
[reports/durham_unmatched_review.md](reports/durham_unmatched_review.md).

## Privacy and testing

GeoTIGER does not call a geocoding API. After TIGER data is downloaded, the
reference database and all geocoding runs are local. Maps are created without
a default online basemap.

```powershell
uv run pytest
uv run ruff check .
```

Timing reports are in `reports/`; `result.timings.to_dict()` provides timings
for each run.
