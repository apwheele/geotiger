import pandas as pd

from geotiger.demo_data import make_durham_inputs, midpoint_address


def test_midpoint_address_moves_public_block_to_fifty():
    assert midpoint_address("1400 SNOWCREST TRL") == "1450 SNOWCREST TRL"
    assert midpoint_address("100 MAIN ST") == "150 MAIN ST"
    assert midpoint_address("1425 MAIN ST") == "1425 MAIN ST"


def test_make_durham_inputs_adds_locality_columns():
    result = make_durham_inputs(pd.DataFrame({"ADDRESS2": ["800 W CHAPEL HILL ST"]}))
    assert result.loc[0, "address"] == "850 W CHAPEL HILL ST"
    assert result.loc[0, "city"] == "DURHAM"
    assert result.loc[0, "state"] == "NC"

