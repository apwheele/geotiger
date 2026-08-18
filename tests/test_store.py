from conftest import make_range_frame

from geotiger import GeoTIGERStore
from geotiger.interpolate import InterpolationConfig, prepare_ranges


def test_store_round_trips_candidates_and_metadata(tmp_path):
    prepared = prepare_ranges(
        make_range_frame(),
        config=InterpolationConfig(end_offset_m=0),
        source="fixture",
    )
    database = tmp_path / "addresses.duckdb"
    with GeoTIGERStore(database, threads=1) as store:
        inserted = store.ingest_candidates(prepared, source="override")
        store.set_metadata(vintage=2024)
        assert inserted == len(prepared)
        assert store.count() == len(prepared)
        assert store.metadata()["vintage"] == "2024"
    assert database.exists()
