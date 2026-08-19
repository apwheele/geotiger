from conftest import make_range_frame
from shapely.geometry import LineString

from geotiger import InterpolationConfig, prepare_ranges


def test_prepare_ranges_expands_parity_and_both_sides():
    prepared = prepare_ranges(
        make_range_frame().iloc[[0]],
        config=InterpolationConfig(end_offset_m=0, side_offset_m=5),
        source="fixture",
    )
    assert len(prepared) == 12
    assert set(prepared["side"]) == {"L", "R"}
    assert set(prepared.loc[prepared.side == "L", "house_number"]) == {100, 102, 104, 106, 108, 110}
    assert set(prepared.loc[prepared.side == "R", "house_number"]) == {101, 103, 105, 107, 109, 111}
    assert prepared["latitude"].notna().all()
    assert prepared["longitude"].notna().all()


def test_prepare_ranges_uses_offset_inside_clipped_segment():
    frame = make_range_frame().iloc[[0]].copy()
    prepared = prepare_ranges(
        frame,
        config=InterpolationConfig(end_offset_m=50, side_offset_m=0),
        source="clipped",
    )
    # Even an endpoint address is not placed directly on the segment endpoint.
    endpoint_lon = -78.95
    assert (prepared["longitude"] > endpoint_lon).all()


def test_prepare_ranges_accepts_current_pygris_house_number_columns():
    frame = make_range_frame().iloc[[0]].rename(
        columns={
            "LFROMADD": "LFROMHN",
            "LTOADD": "LTOHN",
            "RFROMADD": "RFROMHN",
            "RTOADD": "RTOHN",
        }
    )
    prepared = prepare_ranges(frame, config=InterpolationConfig(end_offset_m=0, side_offset_m=0))
    assert len(prepared) == 12
    assert prepared["interpolation_crs"].eq("EPSG:2264").all()


def test_prepare_ranges_adds_intersection_points_by_default():
    frame = make_range_frame().copy()
    frame.loc[1, "FULLNAME"] = "First Avenue"
    frame.loc[1, "geometry"] = LineString([(-78.945, 35.995), (-78.945, 36.005)])

    prepared = prepare_ranges(
        frame,
        config=InterpolationConfig(end_offset_m=0, side_offset_m=0),
    )

    intersections = prepared.loc[prepared["is_intersection"]]
    assert len(intersections) == 1
    assert intersections.iloc[0]["intersection_key"] == "FIRST AVE || MAIN ST"
    assert intersections.iloc[0]["side"] == "I"
