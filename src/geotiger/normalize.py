"""Address parsing and deterministic normalization.

The parser is deliberately kept separate from matching. Parsing is the only
step that may use the probabilistic CRF model in :mod:`usaddress`; all later
steps operate on canonical strings and numeric fields that can be audited.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

import usaddress

_SPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9 ]+")
_NUMBER_RE = re.compile(r"^\s*(\d+)")
_LEADING_NUMBER_RE = re.compile(r"^\s*(\d+)\s+(.*)$")
_INTERSECTION_SEPARATOR_RE = re.compile(r"\s*(?:/|&|@|\+)\s*|\s+\b(?:AT|AND)\b\s+", re.IGNORECASE)
_INTERSECTION_PREFIX_RE = re.compile(r"^\s*(?:INTERSECTION|CORNER)\s+OF\s+", re.IGNORECASE)

_DIRECTIONALS = {
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
    "NORTHEAST": "NE",
    "NORTH EAST": "NE",
    "NORTHWEST": "NW",
    "NORTH WEST": "NW",
    "SOUTHEAST": "SE",
    "SOUTH EAST": "SE",
    "SOUTHWEST": "SW",
    "SOUTH WEST": "SW",
    "N": "N",
    "S": "S",
    "E": "E",
    "W": "W",
    "NE": "NE",
    "NW": "NW",
    "SE": "SE",
    "SW": "SW",
}

_SUFFIXES = {
    "ALLEY": "ALY",
    "AVENUE": "AVE",
    "AV": "AVE",
    "BOULEVARD": "BLVD",
    "CIRCLE": "CIR",
    "COURT": "CT",
    "DRIVE": "DR",
    "EXPRESSWAY": "EXPY",
    "HIGHWAY": "HWY",
    "JUNCTION": "JCT",
    "LANE": "LN",
    "MOUNTAIN": "MTN",
    "PARKWAY": "PKWY",
    "PLACE": "PL",
    "PLAZA": "PLZ",
    "ROAD": "RD",
    "ROUTE": "RTE",
    "SQUARE": "SQ",
    "STREET": "ST",
    "TERRACE": "TER",
    "TRAIL": "TRL",
    "TURNPIKE": "TPKE",
    "WAY": "WAY",
    "ST": "ST",
    "RD": "RD",
    "AVE": "AVE",
    "BLVD": "BLVD",
    "DR": "DR",
    "LN": "LN",
    "HWY": "HWY",
    "PKWY": "PKWY",
}

_STATE_ALIASES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY", "DISTRICT OF COLUMBIA": "DC",
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
    "09": "CT", "10": "DE", "12": "FL", "13": "GA", "15": "HI", "16": "ID",
    "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA",
    "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH", "34": "NJ",
    "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK",
    "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN",
    "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV",
    "55": "WI", "56": "WY", "11": "DC",
}


def normalize_text(value: Any) -> str:
    """Return an uppercase, punctuation-free comparison string."""

    if value is None:
        return ""
    try:
        if value != value:  # NaN and pandas.NA-like values
            return ""
    except (TypeError, ValueError):
        pass
    if str(value).strip().upper() in {"NAN", "<NA>", "NONE"}:
        return ""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    text = _NON_ALNUM_RE.sub(" ", text.upper())
    return _SPACE_RE.sub(" ", text).strip()


def normalize_directional(value: Any) -> str:
    value = normalize_text(value)
    return _DIRECTIONALS.get(value, value)


def normalize_suffix(value: Any) -> str:
    value = normalize_text(value)
    return _SUFFIXES.get(value, value)


def normalize_state(value: Any) -> str:
    """Normalize USPS state names, abbreviations, and Census state FIPS."""

    value = normalize_text(value)
    return _STATE_ALIASES.get(value, value)


def normalize_zip(value: Any) -> str:
    """Keep a ZIP as a five-digit string, retaining only the ZIP5 portion."""

    value = normalize_text(value)
    digits = re.sub(r"[^0-9]", "", value)
    return digits[:5].zfill(5) if digits else ""


def street_block_key(value: Any) -> str:
    """Make a compact street blocking signature tolerant of middle typos."""

    compact = normalize_text(value).replace(" ", "")
    return compact if len(compact) <= 6 else compact[:3] + compact[-3:]


def intersection_key(left: Any, right: Any) -> str:
    """Return an order-independent key for a two-street intersection."""

    streets = sorted(value for value in (normalize_text(left), normalize_text(right)) if value)
    return " || ".join(streets)


def extract_house_number(value: Any) -> int | None:
    """Extract the numeric portion of an address number such as ``12-14``."""

    match = _NUMBER_RE.match(str(value or ""))
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class ParsedAddress:
    """Canonical fields used by the geocoder and persisted in its output."""

    raw_address: str = ""
    house_number: int | None = None
    pre_directional: str = ""
    street_name: str = ""
    street_suffix: str = ""
    post_directional: str = ""
    unit: str = ""
    city: str = ""
    state: str = ""
    zip5: str = ""
    is_intersection: bool = False
    intersection_street_norm: str = ""
    intersection_key: str = ""

    @property
    def street_norm(self) -> str:
        parts = [
            self.pre_directional,
            self.street_name,
            self.street_suffix,
            self.post_directional,
        ]
        street = normalize_text(" ".join(part for part in parts if part))
        return self.intersection_key if self.is_intersection else street

    @property
    def city_norm(self) -> str:
        return normalize_text(self.city)

    @property
    def state_norm(self) -> str:
        return normalize_state(self.state)

    @property
    def street_block(self) -> str:
        # Block on the first character of the street name, not a directional
        # prefix ("N Main" must share a block with TIGER's "Main").
        return street_block_key(self.street_name)

    @property
    def intersection_street_block(self) -> str:
        """Return the compact block key for the second intersection street."""

        return street_block_key(self.intersection_street_norm)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.update(
            street_norm=self.street_norm,
            city_norm=self.city_norm,
            state_norm=self.state_norm,
            street_block=self.street_block,
            intersection_street_block=self.intersection_street_block,
        )
        return result


def _first(parsed: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        value = parsed.get(key)
        if value:
            return str(value)
    return ""


def _simple_parse(
    raw: str,
    *,
    city: Any,
    state: Any,
    zip_code: Any,
) -> ParsedAddress | None:
    """Fast path for the common ``123 N Main St`` form.

    Full usaddress parsing remains the fallback for commas, units, rural
    routes, and other complex forms. The fast path matters for large batches
    of already standardized crime/RMS exports.
    """

    if not raw or "," in raw:
        return None
    normalized = normalize_text(raw)
    tokens = normalized.split()
    if len(tokens) < 2:
        return None
    number = int(tokens.pop(0)) if tokens[0].isdigit() else None
    pre = ""
    post = ""
    if tokens and tokens[0] in _DIRECTIONALS:
        pre = normalize_directional(tokens.pop(0))
    if tokens and tokens[-1] in _DIRECTIONALS:
        post = normalize_directional(tokens.pop())
    suffix = ""
    if tokens and tokens[-1] in _SUFFIXES:
        suffix = normalize_suffix(tokens.pop())
    if not tokens:
        return None
    return ParsedAddress(
        raw_address=raw,
        house_number=number,
        pre_directional=pre,
        street_name=normalize_text(" ".join(tokens)),
        street_suffix=suffix,
        post_directional=post,
        city=normalize_text(city),
        state=normalize_state(state),
        zip5=normalize_zip(zip_code),
    )


def _parse_single_address(
    address: Any,
    *,
    city: Any = "",
    state: Any = "",
    zip_code: Any = "",
) -> ParsedAddress:
    """Parse one non-intersection US address."""

    raw = "" if address is None else str(address)
    if raw.strip().upper() in {"NAN", "<NA>", "NONE"}:
        raw = ""
    simple = _simple_parse(raw, city=city, state=state, zip_code=zip_code)
    if simple is not None:
        return simple
    parsed: dict[str, str]
    try:
        parsed, _ = usaddress.tag(raw)
    except usaddress.RepeatedLabelError:
        # Duplicate labels are uncommon but should not make a batch fail. The
        # token parser still provides useful first-occurrence fields.
        parsed = {}
        for token, label in usaddress.parse(raw):
            parsed.setdefault(label, token)

    house_raw = _first(parsed, "AddressNumber")
    pre = normalize_directional(_first(parsed, "StreetNamePreDirectional"))
    street = normalize_text(_first(parsed, "StreetName"))
    suffix = normalize_suffix(_first(parsed, "StreetNamePostType"))
    post = normalize_directional(_first(parsed, "StreetNamePostDirectional"))
    unit = normalize_text(
        _first(
            parsed,
            "OccupancyType",
            "OccupancyIdentifier",
            "SubaddressType",
            "SubaddressIdentifier",
        )
    )
    parsed_city = _first(parsed, "PlaceName")
    parsed_state = _first(parsed, "StateName")
    parsed_zip = _first(parsed, "ZipCode")
    return ParsedAddress(
        raw_address=raw,
        house_number=extract_house_number(house_raw),
        pre_directional=pre,
        street_name=street,
        street_suffix=suffix,
        post_directional=post,
        unit=unit,
        city=normalize_text(city) or normalize_text(parsed_city),
        state=normalize_state(state) or normalize_state(parsed_state),
        zip5=normalize_zip(zip_code) or normalize_zip(parsed_zip),
    )


def _intersection_parts(raw: str) -> tuple[int | None, str, str] | None:
    """Split a two-street intersection while preserving an optional number."""

    text = _INTERSECTION_PREFIX_RE.sub("", raw.split(",", 1)[0])
    house_number: int | None = None
    leading = _LEADING_NUMBER_RE.match(text)
    if leading:
        house_number = int(leading.group(1))
        text = leading.group(2)
    parts = [part.strip() for part in _INTERSECTION_SEPARATOR_RE.split(text) if part.strip()]
    if len(parts) != 2:
        return None
    return house_number, parts[0], parts[1]


def parse_address(
    address: Any,
    *,
    city: Any = "",
    state: Any = "",
    zip_code: Any = "",
) -> ParsedAddress:
    """Parse a free-form US address or a two-street intersection.

    Intersections may use ``/``, ``&``, ``@``, ``+``, ``AND``, or ``AT`` as the
    separator. A leading number is retained as an optional block/incident
    number, but it is not required for intersection matching.
    """

    raw = "" if address is None else str(address)
    if raw.strip().upper() in {"NAN", "<NA>", "NONE"}:
        raw = ""
    parts = _intersection_parts(raw) if raw else None
    if parts is not None:
        house_number, left, right = parts
        locality = _parse_single_address(
            "1 Main St" + raw[raw.find(",") :], city="", state="", zip_code=""
        ) if "," in raw else ParsedAddress()
        resolved_city = normalize_text(city) or locality.city
        resolved_state = normalize_state(state) or locality.state
        resolved_zip = normalize_zip(zip_code) or locality.zip5
        left_parsed = _parse_single_address(
            left,
            city=resolved_city,
            state=resolved_state,
            zip_code=resolved_zip,
        )
        right_parsed = _parse_single_address(
            right,
            city=resolved_city,
            state=resolved_state,
            zip_code=resolved_zip,
        )
        if left_parsed.street_norm and right_parsed.street_norm:
            return ParsedAddress(
                raw_address=raw,
                house_number=house_number,
                pre_directional=left_parsed.pre_directional,
                street_name=left_parsed.street_name,
                street_suffix=left_parsed.street_suffix,
                post_directional=left_parsed.post_directional,
                city=resolved_city or left_parsed.city,
                state=resolved_state or left_parsed.state,
                zip5=resolved_zip or left_parsed.zip5,
                is_intersection=True,
                intersection_street_norm=right_parsed.street_norm,
                intersection_key=intersection_key(
                    left_parsed.street_norm,
                    right_parsed.street_norm,
                ),
            )
    return _parse_single_address(raw, city=city, state=state, zip_code=zip_code)


def parse_record(
    row: Mapping[str, Any],
    *,
    address_column: str = "address",
    city_column: str | None = "city",
    state_column: str | None = "state",
    zip_column: str | None = "zip",
) -> ParsedAddress:
    """Parse one dataframe-like record using the configured column names."""

    return parse_address(
        row.get(address_column, ""),
        city=row.get(city_column, "") if city_column else "",
        state=row.get(state_column, "") if state_column else "",
        zip_code=row.get(zip_column, "") if zip_column else "",
    )
