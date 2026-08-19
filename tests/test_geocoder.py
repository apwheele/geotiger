import pandas as pd
from conftest import make_store

from geotiger import Geocoder, GeocoderConfig


def test_geocode_returns_match_and_all_potential_candidates():
    store = make_store()
    records = pd.DataFrame(
        [
            {"address": "100 N Main St", "city": "Durham", "state": "NC", "zip": "27514"},
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
