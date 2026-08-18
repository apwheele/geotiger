"""Explicit source/download helpers.

Network access is intentionally isolated here. The rest of the package only
accepts local GeoDataFrames or files.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd


def download_tiger_ranges(
    state: str,
    *,
    county: str | None = None,
    year: int = 2024,
    cache: bool = True,
    **kwargs: Any,
) -> gpd.GeoDataFrame:
    """Download TIGER/Line address ranges through ``pygris``.

    This function is the package's only built-in network boundary. It is
    deliberately not called by :class:`geotiger.Geocoder`.
    """

    try:
        import pygris
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise RuntimeError(
            "Install GeoTIGER with its pygris dependency to download TIGER ranges"
        ) from exc
    function = getattr(pygris, "address_ranges", None)
    if function is None:
        raise RuntimeError("The installed pygris version does not expose address_ranges")
    available = inspect.signature(function).parameters
    if county is None and "county" in available:
        county_function = getattr(pygris, "counties", None)
        if county_function is None:
            raise ValueError(
                "This pygris version requires a county for address_ranges; "
                "supply county explicitly."
            )
        county_available = inspect.signature(county_function).parameters
        county_kwargs = {
            key: value
            for key, value in {"state": state, "year": year, "cache": cache, **kwargs}.items()
            if key in county_available and value is not None
        }
        county_frame = county_function(**county_kwargs)
        county_column = next(
            (
                column
                for column in ("GEOID", "COUNTYFP", "COUNTYFP10")
                if column in county_frame.columns
            ),
            None,
        )
        if county_column is None:
            raise ValueError("Could not find county FIPS in the pygris counties result")
        parts = [
            download_tiger_ranges(
                state,
                county=str(value),
                year=year,
                cache=cache,
                **kwargs,
            )
            for value in county_frame[county_column].tolist()
        ]
        combined = (
            gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=parts[0].crs)
            if parts
            else gpd.GeoDataFrame()
        )
        if len(combined) and "STUSPS" not in combined.columns:
            combined["STUSPS"] = state
        return combined
    call_kwargs: dict[str, Any] = {}
    options = {"state": state, "county": county, "year": year, "cache": cache, **kwargs}
    for key, value in options.items():
        if key in available and value is not None:
            call_kwargs[key] = value
    try:
        ranges = function(**call_kwargs)
    except TypeError:
        # A few older pygris versions used positional state/county arguments.
        positional = [state] + ([county] if county is not None else [])
        positional_kwargs = {
            key: value
            for key, value in call_kwargs.items()
            if key not in {"state", "county"}
        }
        ranges = function(*positional, **positional_kwargs)
    if not isinstance(ranges, gpd.GeoDataFrame):
        ranges = gpd.GeoDataFrame(ranges)
    if len(ranges) and "STUSPS" not in ranges.columns:
        ranges["STUSPS"] = state
    return ranges


def save_ranges(ranges: gpd.GeoDataFrame, path: str | Path) -> Path:
    """Save downloaded ranges in a local geospatial format."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() in {".parquet", ".geoparquet"}:
        ranges.to_parquet(output)
    elif output.suffix.lower() in {".gpkg", ".geojson", ".json", ".shp"}:
        ranges.to_file(output)
    else:
        raise ValueError("Range output must end in .parquet, .gpkg, .geojson, or .shp")
    return output


def load_ranges(path: str | Path, *, layer: str | None = None) -> gpd.GeoDataFrame:
    """Load a local range file without any network access."""

    path = Path(path)
    if path.suffix.lower() in {".parquet", ".geoparquet"}:
        return gpd.read_parquet(path)
    return gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
