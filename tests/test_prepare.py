import geopandas as gpd
import pandas as pd
from conftest import make_range_frame
from shapely import wkt
from shapely.geometry import Polygon

from geotiger import (
    CombinedGeocoder,
    GeoTIGERStore,
    InterpolationConfig,
    prepare_addresses,
    prepare_combined,
    prepare_parcels,
)


def make_address_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "address": "100 N Main St",
                "city": "Durham",
                "state": "NC",
                "zip": "27514",
                "lon": -78.95,
                "lat": 36.0,
                "address_id": "addr-1",
            }
        ]
    )


def make_parcel_table() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "SITUS_ADDRESS": "100 N Main St",
                "CITY": "Durham",
                "STATE": "NC",
                "ZIP": "27514",
                "PIN": "parcel-1",
            }
        ],
        geometry=[
            Polygon(
                [
                    (-78.951, 35.999),
                    (-78.949, 35.999),
                    (-78.949, 36.001),
                    (-78.951, 36.001),
                ]
            )
        ],
        crs="EPSG:4326",
    )


def test_prepare_addresses_builds_common_point_reference_schema():
    prepared = prepare_addresses(make_address_table())

    assert len(prepared) == 1
    assert prepared.loc[0, "house_number"] == 100
    assert prepared.loc[0, "street_norm"] == "N MAIN ST"
    assert prepared.loc[0, "side"] == "P"
    assert prepared.loc[0, "source_type"] == "individual"
    assert prepared.loc[0, "source_record_id"] == "addr-1"
    assert prepared.loc[0, "latitude"] == 36.0
    assert prepared.loc[0, "longitude"] == -78.95


def test_prepare_addresses_accepts_intersection_without_house_number():
    records = pd.DataFrame(
        [
            {
                "address": "Main St & Market Ave",
                "city": "Durham",
                "state": "NC",
                "lon": -78.95,
                "lat": 36.0,
                "address_id": "intersection-1",
            }
        ]
    )

    prepared = prepare_addresses(records)

    assert len(prepared) == 1
    assert prepared.loc[0, "is_intersection"]
    assert prepared.loc[0, "house_number"] is None
    assert prepared.loc[0, "intersection_key"] == "MAIN ST || MARKET AVE"


def test_prepare_parcels_uses_an_interior_representative_point():
    prepared = prepare_parcels(make_parcel_table())
    point = wkt.loads(prepared.loc[0, "geometry_wkt"])
    polygon = make_parcel_table().geometry.iloc[0]

    assert polygon.contains(point)
    assert prepared.loc[0, "source_type"] == "parcel"
    assert prepared.loc[0, "source_record_id"] == "parcel-1"


def test_prepare_combined_returns_one_table_with_preference_priorities():
    prepared = prepare_combined(
        addresses=make_address_table(),
        parcels=make_parcel_table(),
        ranges=make_range_frame().iloc[[0]],
        range_options={
            "config": InterpolationConfig(end_offset_m=0, side_offset_m=0),
            "source": "fixture_tiger",
        },
    )

    assert set(prepared["source_type"]) == {"individual", "parcel", "tiger"}
    assert prepared.groupby("source_type")["source_priority"].first().to_dict() == {
        "individual": 0,
        "parcel": 10,
        "tiger": 20,
    }
    assert list(prepared["source_priority"]) == sorted(prepared["source_priority"])
    prepped_again = prepare_combined(
        addresses=prepare_addresses(make_address_table()),
        parcels=prepare_parcels(make_parcel_table()),
    )
    assert len(prepped_again) == 2
    assert set(prepped_again["source_type"]) == {"individual", "parcel"}


def test_combined_geocoder_prefers_individual_points_on_equal_scores():
    store = GeoTIGERStore(":memory:", threads=1)
    combined = CombinedGeocoder.from_tables(
        store,
        addresses=make_address_table(),
        parcels=make_parcel_table(),
    )
    result = combined.geocode(
        pd.DataFrame(
            [{"address": "100 N Main St", "city": "Durham", "state": "NC", "zip": "27514"}]
        )
    )

    assert store.count() == 2
    assert result.matches.loc[0, "match_status"] == "matched"
    assert result.matches.loc[0, "matched_source_type"] == "individual"
    assert result.matches.loc[0, "matched_source_record_id"] == "addr-1"

    parcel_first = CombinedGeocoder.from_tables(
        GeoTIGERStore(":memory:", threads=1),
        addresses=make_address_table(),
        parcels=make_parcel_table(),
        source_preference=("parcel", "individual", "tiger"),
    )
    parcel_result = parcel_first.geocode(
        pd.DataFrame(
            [{"address": "100 N Main St", "city": "Durham", "state": "NC", "zip": "27514"}]
        )
    )
    assert parcel_result.matches.loc[0, "matched_source_type"] == "parcel"
