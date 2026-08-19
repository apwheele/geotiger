from geotiger.normalize import (
    normalize_state,
    normalize_street_name,
    normalize_zip,
    parse_address,
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
