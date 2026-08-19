import pandas as pd
from conftest import make_range_frame, make_store

from geotiger import CombinedGeocoder, Geocoder, GeoTIGERStore, InterpolationConfig
from geotiger.interpolate import prepare_ranges


def test_lookup_alias_maps_directly_to_a_prepared_address():
    store = make_store()
    target_id = store.connection.execute(
        "SELECT address_id FROM addresses WHERE street_norm = 'MAIN ST' ORDER BY address_id LIMIT 1"
    ).fetchone()[0]
    stored = store.ingest_lookup(
        pd.DataFrame(
            [
                {
                    "alias": "McDonalds First St",
                    "address_id": target_id,
                    "city": "Durham",
                    "state": "NC",
                    "zip": "27514",
                }
            ]
        )
    )

    result = Geocoder(store).geocode(
        pd.DataFrame(
            [{"address": "McDonalds First St", "city": "Durham", "state": "NC", "zip": "27514"}]
        )
    )

    assert stored == 1
    assert store.lookup_count() == 1
    assert result.matches.loc[0, "match_method"] == "lookup"
    assert result.matches.loc[0, "matched_address_id"] == target_id
    assert result.timings.lookup_hit_count == 1
    assert result.timings.lookup_seconds >= 0


def test_combined_lookup_table_can_resolve_actual_address_once():
    ranges = make_range_frame()
    prepared = prepare_ranges(
        ranges,
        config=InterpolationConfig(end_offset_m=0, side_offset_m=0),
        source="fixture",
    )
    store = GeoTIGERStore(":memory:", threads=1)
    combined = CombinedGeocoder.from_tables(store, ranges=ranges)
    stored = combined.add_lookup_table(
        pd.DataFrame(
            [
                {
                    "alias": "McDonalds First St",
                    "actual_address": "100 Main St",
                    "city": "Durham",
                    "state": "NC",
                    "zip": "27514",
                }
            ]
        )
    )

    result = combined.geocode(
        pd.DataFrame(
            [{"address": "McDonalds First St", "city": "Durham", "state": "NC", "zip": "27514"}]
        )
    )

    assert len(prepared) == store.count()
    assert stored == 1
    assert result.matches.loc[0, "match_method"] == "lookup"
    assert result.matches.loc[0, "match_status"] == "matched"


def test_historical_cache_is_written_and_used_before_candidate_matching():
    store = make_store()
    records = pd.DataFrame(
        [{"address": "100 Main St", "city": "Durham", "state": "NC", "zip": "27514"}]
    )
    first = Geocoder(store).geocode(records)
    stored = store.cache_matches(first)
    second = Geocoder(store).geocode(records)

    assert stored == 1
    assert store.history_cache_count() == 1
    assert second.matches.loc[0, "match_method"] == "history_cache"
    assert second.matches.loc[0, "match_status"] == "matched"
    assert second.timings.history_cache_hit_count == 1
    assert second.timings.history_cache_seconds >= 0
