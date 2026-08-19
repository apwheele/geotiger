from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pygris
import pytest
from shapely.geometry import Point

from geotiger.sources import download_tiger_ranges, load_ranges, save_ranges


def _point_frame(label: str = "001") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [{"county": label, "geometry": Point(-78.9, 36.0)}],
        geometry="geometry",
        crs="EPSG:4326",
    )


def test_download_forwards_supported_options_and_converts_plain_frame(monkeypatch):
    calls = []

    def address_ranges(state, county, year=2024, cache=True):
        calls.append((state, county, year, cache))
        return pd.DataFrame({"county": [county]})

    monkeypatch.setattr(pygris, "address_ranges", address_ranges)
    result = download_tiger_ranges("NC", county="063", year=2023, cache=False)

    assert isinstance(result, gpd.GeoDataFrame)
    assert calls == [("NC", "063", 2023, False)]


def test_statewide_download_enumerates_counties_and_combines_results(monkeypatch):
    requested = []

    def counties(state, year=2024, cache=True):
        return pd.DataFrame({"COUNTYFP": ["001", "003"]})

    def address_ranges(state, county=None, year=2024, cache=True):
        requested.append(county)
        return _point_frame(str(county))

    monkeypatch.setattr(pygris, "counties", counties)
    monkeypatch.setattr(pygris, "address_ranges", address_ranges)

    result = download_tiger_ranges("DE", county=None)

    assert requested == ["001", "003"]
    assert result["county"].tolist() == ["001", "003"]
    assert result.crs.to_epsg() == 4326


def test_download_supports_positional_only_older_pygris(monkeypatch):
    calls = []

    def address_ranges(state, county, /, year=2024, cache=True):
        calls.append((state, county, year, cache))
        return _point_frame(str(county))

    monkeypatch.setattr(pygris, "address_ranges", address_ranges)

    result = download_tiger_ranges("NC", county="063", year=2022, cache=False)

    assert len(result) == 1
    assert calls == [("NC", "063", 2022, False)]


def test_range_file_round_trip_and_extension_validation(tmp_path):
    ranges = _point_frame()
    parquet = tmp_path / "ranges.parquet"

    assert save_ranges(ranges, parquet) == parquet
    loaded = load_ranges(parquet)

    assert loaded.crs.to_epsg() == 4326
    assert loaded["county"].tolist() == ["001"]
    with pytest.raises(ValueError, match="Range output"):
        save_ranges(ranges, tmp_path / "ranges.txt")
