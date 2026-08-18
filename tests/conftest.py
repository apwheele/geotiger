from __future__ import annotations

import geopandas as gpd
from shapely.geometry import LineString

from geotiger import GeoTIGERStore, InterpolationConfig, prepare_ranges


def make_range_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "TLID": "main-1",
                "FULLNAME": "Main Street",
                "LFROMADD": 100,
                "LTOADD": 110,
                "RFROMADD": 101,
                "RTOADD": 111,
                "ZIPL": "27514",
                "ZIPR": "27514",
                "CITYL": "Durham",
                "CITYR": "Durham",
                "STUSPS": "NC",
                "geometry": LineString([(-78.95, 36.00), (-78.94, 36.00)]),
            },
            {
                "TLID": "market-1",
                "FULLNAME": "Market Avenue",
                "LFROMADD": 200,
                "LTOADD": 210,
                "RFROMADD": 201,
                "RTOADD": 211,
                "ZIPL": "27514",
                "ZIPR": "27514",
                "CITYL": "Durham",
                "CITYR": "Durham",
                "STUSPS": "NC",
                "geometry": LineString([(-78.95, 36.01), (-78.94, 36.01)]),
            },
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )


def make_store() -> GeoTIGERStore:
    store = GeoTIGERStore(":memory:", threads=2)
    prepared = prepare_ranges(
        make_range_frame(),
        config=InterpolationConfig(end_offset_m=0, side_offset_m=5),
        source="test",
    )
    store.ingest_candidates(prepared)
    return store

