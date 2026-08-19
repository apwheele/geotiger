import folium
import matplotlib
import pandas as pd

from geotiger.viz import matches_map, matches_static_map

matplotlib.use("Agg")


def _matches() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "match_latitude": 38.90,
                "match_longitude": -77.03,
                "match_status": "matched",
                "score": 99.0,
                "matched_house_number": 100.0,
                "matched_street_norm": "FIRST ST",
                "matched_city": "WASHINGTON",
                "matched_state": "DC",
                "matched_zip5": "20001",
                "match_method": "street_exact",
            },
            {
                "match_latitude": 38.91,
                "match_longitude": -77.02,
                "match_status": "review",
                "score": 82.0,
                "matched_house_number": 200.0,
                "matched_street_norm": "SECOND ST",
                "matched_city": "WASHINGTON",
                "matched_state": "DC",
                "matched_zip5": "20002",
            },
        ]
    )


def test_folium_map_is_offline_by_default_and_adds_markers():
    fmap = matches_map(_matches())

    children = list(fmap._children.values())
    assert fmap.location == [38.905, -77.025]
    assert sum(isinstance(child, folium.CircleMarker) for child in children) == 2
    assert not any(isinstance(child, folium.TileLayer) for child in children)
    assert "100 FIRST ST, WASHINGTON, DC 20001" in fmap.get_root().render()


def test_maps_handle_empty_coordinates_and_static_axes():
    empty = pd.DataFrame(
        columns=["match_latitude", "match_longitude", "match_status"]
    )

    fmap = matches_map(empty)
    ax = matches_static_map(empty)
    populated_ax = matches_static_map(_matches())

    assert fmap.location == [39.5, -98.35]
    assert ax.texts[0].get_text() == "No geocoded coordinates"
    assert populated_ax.get_xlabel() == "Longitude"
    assert len(populated_ax.collections) == 2
