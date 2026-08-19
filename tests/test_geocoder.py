import pandas as pd
from conftest import make_range_frame, make_store
from shapely.geometry import LineString

from geotiger import Geocoder, GeocoderConfig, InterpolationConfig, prepare_ranges
from geotiger.geocoder import DEFAULT_WEIGHTS


def test_default_weights_prioritize_street_identity_over_locality_fields():
    assert sum(DEFAULT_WEIGHTS.values()) == 1.0
    assert DEFAULT_WEIGHTS["street"] > DEFAULT_WEIGHTS["house_number"]
    assert DEFAULT_WEIGHTS["city"] <= 0.05
    assert DEFAULT_WEIGHTS["zip5"] <= 0.05


def test_geocode_returns_match_and_all_potential_candidates():
    store = make_store()
    records = pd.DataFrame(
        [
            {"address": "100 Main St", "city": "Durham", "state": "NC", "zip": "27514"},
            {"address": "201 Market Ave", "city": "Durham", "state": "NC", "zip": "27514"},
        ]
    )
    result = Geocoder(store).geocode(records)
    assert list(result.matches["match_status"]) == ["matched", "matched"]
    assert result.matches["auto_assigned"].all()
    assert (result.matches["score"] >= 90).all()
    assert len(result.candidates) >= len(records)
    assert result.timings.input_count == 2
    assert result.timings.candidate_count == len(result.candidates)
    assert result.timings.throughput_per_second > 0


def test_optional_input_deduplication_preserves_results():
    store = make_store()
    records = pd.DataFrame(
        [
            {"address": "100 N Main St", "city": "Durham", "state": "NC", "zip": "27514"},
            {"address": "100 N Main St", "city": "Durham", "state": "NC", "zip": "27514"},
            {"address": "201 Market Ave", "city": "Durham", "state": "NC", "zip": "27514"},
        ]
    )
    ordinary = Geocoder(store).geocode(records)
    deduplicated = Geocoder(
        store,
        config=GeocoderConfig(deduplicate_inputs=True),
    ).geocode(records)

    assert deduplicated.matches["matched_address_id"].tolist() == ordinary.matches[
        "matched_address_id"
    ].tolist()
    assert len(deduplicated.candidates) == len(ordinary.candidates)
    assert deduplicated.timings.candidate_input_count == 3
    assert deduplicated.timings.candidate_query_input_count == 2
    assert deduplicated.timings.deduplicate_inputs


def test_geocoder_matches_prepared_intersection_points():
    ranges = make_range_frame().copy()
    ranges.loc[1, "FULLNAME"] = "First Avenue"
    ranges.loc[1, "geometry"] = LineString([(-78.945, 35.995), (-78.945, 36.005)])
    prepared = prepare_ranges(
        ranges,
        config=InterpolationConfig(end_offset_m=0, side_offset_m=0),
    )
    store = make_store()
    store.ingest_candidates(prepared, replace=True)

    result = Geocoder(store).geocode(
        pd.DataFrame(
            [{"address": "First Avenue at Main Street", "state": "NC", "zip": "27514"}]
        )
    )

    assert result.matches.loc[0, "match_status"] == "matched"
    assert result.matches.loc[0, "matched_is_intersection"]
    assert result.matches.loc[0, "matched_intersection_key"] == "FIRST AVE || MAIN ST"


def test_exact_house_number_first_is_fast_but_tolerance_fallback_is_available():
    store = make_store()
    records = pd.DataFrame(
        [{"address": "100 Main St", "city": "Durham", "state": "NC", "zip": "27514"}]
    )
    exact = Geocoder(store).geocode(records)
    all_window = Geocoder(
        store,
        config=GeocoderConfig(exact_house_number_first=False),
    ).geocode(records)
    assert len(exact.candidates) == 1
    assert len(all_window.candidates) > len(exact.candidates)


def test_exact_street_mode_accepts_spacing_only_street_variants():
    ranges = make_range_frame().iloc[[0]].copy()
    ranges.loc[:, "FULLNAME"] = "Snow Crest Trail"
    store = make_store()
    store.ingest_candidates(
        prepare_ranges(
            ranges,
            config=InterpolationConfig(end_offset_m=0, side_offset_m=0),
            source="snow",
        ),
        replace=True,
    )
    result = Geocoder(
        store,
        config=GeocoderConfig(strict_locality=False, street_fallback=False),
    ).geocode(pd.DataFrame([{"address": "100 SNOWCREST TRL", "state": "NC"}]))

    assert result.matches.loc[0, "match_status"] == "matched"
    assert result.matches.loc[0, "score_street"] > 90


def test_locality_blocking_rejects_wrong_state_and_zip():
    store = make_store()
    records = pd.DataFrame(
        [
            {"address": "100 Main St", "city": "Durham", "state": "VA", "zip": "27514"},
            {"address": "100 Main St", "city": "Durham", "state": "NC", "zip": "00000"},
        ]
    )
    result = Geocoder(store).geocode(records)
    assert result.matches["match_status"].tolist() == ["unmatched", "unmatched"]
    assert result.candidates.empty


def test_threshold_and_margin_are_configurable():
    store = make_store()
    records = pd.DataFrame(
        [{"address": "100 Main St", "city": "Durham", "state": "NC", "zip": "27514"}]
    )
    result = Geocoder(store, config=GeocoderConfig(auto_match_threshold=101)).geocode(records)
    assert result.matches.loc[0, "match_status"] == "review"
    assert not result.matches.loc[0, "auto_assigned"]


def test_empty_batches_return_schema_and_timings():
    result = Geocoder(make_store()).geocode(
        pd.DataFrame(columns=["address", "city", "state", "zip"])
    )
    assert result.matches.empty
    assert result.candidates.empty
    assert result.timings.input_count == 0
    assert "match_status" in result.matches.columns
