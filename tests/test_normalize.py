from geotiger.normalize import (
    normalize_state,
    normalize_street_name,
    normalize_zip,
    parse_address,
    route_component_keys,
)


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


def test_parse_address_identifies_two_street_intersections():
    parsed = parse_address("950 W Club Boulevard/N Duke Street", city="Durham", state="NC")

    assert parsed.is_intersection
    assert parsed.house_number == 950
    assert parsed.street_norm == "N DUKE ST || W CLUB BLVD"
    assert parsed.intersection_street_norm == "N DUKE ST"
    assert parsed.intersection_key == "N DUKE ST || W CLUB BLVD"


def test_zip_normalization_handles_numeric_and_zip_plus_four():
    assert normalize_zip("27514-1234") == "27514"
    assert normalize_zip(27514) == "27514"
    assert normalize_zip(1234) == "01234"


def test_state_normalization_accepts_names_and_census_fips():
    assert normalize_state("North Carolina") == "NC"
    assert normalize_state("37") == "NC"


def test_street_name_normalization_handles_ordinals_and_lexical_abbreviations():
    assert normalize_street_name("Ninth") == "9TH"
    assert normalize_street_name("9nth") == "9TH"
    assert normalize_street_name("Mount Moriah") == "MT MORIAH"
    assert normalize_street_name("Saint Paul") == "ST PAUL"


def test_directional_words_are_street_names_when_no_other_name_remains():
    south = parse_address("850 SOUTH ST", city="Durham", state="NC")
    north = parse_address("100 NORTH STREET")
    east = parse_address("EAST ST / MAIN ST")

    assert south.house_number == 850
    assert south.street_name == "SOUTH"
    assert south.street_suffix == "ST"
    assert south.pre_directional == ""
    assert south.post_directional == ""
    assert south.street_norm == "SOUTH ST"
    assert north.street_norm == "NORTH ST"
    assert east.is_intersection
    assert east.street_norm == "EAST ST || MAIN ST"


def test_common_usps_suffix_abbreviations_are_split_from_the_name():
    court = parse_address("100 Freedom Ct")
    crescent = parse_address("250 Foxridge Crescent")
    creek = parse_address("250 Tamworth Crk")

    assert court.street_name == "FREEDOM"
    assert court.street_suffix == "CT"
    assert court.street_norm == "FREEDOM CT"
    assert crescent.street_name == "FOXRIDGE"
    assert crescent.street_suffix == "CRES"
    assert crescent.street_norm == "FOXRIDGE CRES"
    assert creek.street_name == "TAMWORTH"
    assert creek.street_suffix == "CRK"


def test_route_and_phonetic_keys_preserve_auditable_parsed_components():
    highway = parse_address("4750 NC 55 Highway", state="NC")
    tiger = parse_address("4750 State Hwy 55", state="NC")
    ivey = parse_address("3200 Ivey Wood Lane")
    ivy = parse_address("3200 Ivy Wood Lane")

    assert highway.street_name == "NC 55"
    assert highway.street_suffix == "HWY"
    assert highway.street_name_key == tiger.street_name_key == "NC 55"
    assert (
        parse_address("100 State Highway 55", state="NY").street_name_key
        == parse_address("100 NY 55 Highway", state="NY").street_name_key
        == "NY 55"
    )
    assert ivey.street_name != ivy.street_name
    assert ivey.street_name_phonetic == ivy.street_name_phonetic


def test_numbered_route_keys_ignore_trailing_directionals():
    east = parse_address("2450 US 70 HWY E", state="NC")
    tiger = parse_address("2450 US Hwy 70", state="NC")
    hyphen = parse_address("100 US 15-501 N", state="NC")

    assert east.post_directional == "E"
    assert east.street_name_key == tiger.street_name_key == "US 70"
    assert hyphen.street_name_key == "US 15 501"
    assert hyphen.post_directional == "N"
    assert route_component_keys("US 15 501") == ["US 15 501", "US 15", "US 501"]
    assert route_component_keys("US 70") == ["US 70"]
    assert route_component_keys("MAIN") == ["MAIN"]
    # Ordinary directional street names are not treated as routes.
    assert parse_address("100 East St").street_name_key == "EAST"
    assert parse_address("100 E Main St").street_norm == "E MAIN ST"
