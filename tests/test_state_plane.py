from pyproj import CRS

from geotiger.state_plane import DEFAULT_FALLBACK_CRS, state_plane_crs


def test_state_plane_uses_fixed_zone_and_custom_fallback():
    assert state_plane_crs("North Carolina") == "EPSG:2264"
    assert state_plane_crs(None) == DEFAULT_FALLBACK_CRS
    assert state_plane_crs("not-a-state", fallback="EPSG:3857") == "EPSG:3857"


def test_state_plane_selects_dynamic_zone_from_representative_point():
    result = state_plane_crs("CA", longitude=-118.24, latitude=34.05)

    assert result != DEFAULT_FALLBACK_CRS
    assert CRS.from_user_input(result).is_projected
