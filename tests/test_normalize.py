from geotiger.normalize import normalize_state, normalize_zip, parse_address


def test_parse_address_normalizes_components():
    parsed = parse_address("100 N. Main Street Apt 4B, Durham, NC 27514-1234")
    assert parsed.house_number == 100
    assert parsed.pre_directional == "N"
    assert parsed.street_name == "MAIN"
    assert parsed.street_suffix == "ST"
    assert parsed.city == "DURHAM"
    assert parsed.state == "NC"
    assert parsed.zip5 == "27514"
    assert parsed.street_norm == "N MAIN ST"


def test_zip_normalization_handles_numeric_and_zip_plus_four():
    assert normalize_zip("27514-1234") == "27514"
    assert normalize_zip(27514) == "27514"
    assert normalize_zip(1234) == "01234"


def test_state_normalization_accepts_names_and_census_fips():
    assert normalize_state("North Carolina") == "NC"
    assert normalize_state("37") == "NC"
