"""Address parsing and deterministic normalization.

Component tagging uses :mod:`usaddress`. This module then canonicalizes those
tagged fields (directionals, USPS suffixes, state, ZIP, route identity) for
matching. Intersection strings are split first because usaddress treats the
whole string as one address.
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
_NUMERIC_ORDINAL_RE = re.compile(r"^(\d+)(?:ST|ND|RD|TH|NTH)$")
_STATE_ROUTE_RE = re.compile(
    r"^([A-Z]{2}|[A-Z] [A-Z]|STATE(?: HWY| HIGHWAY| RTE| ROUTE)?)\s*"
    r"(\d+[A-Z]?)\s*(?:HWY|HIGHWAY|RTE|ROUTE)?$"
)
_US_ROUTE_RE = re.compile(
    r"^(?:US|U S)\s*(?:HWY|HIGHWAY|RTE|ROUTE)?\s*"
    r"(\d+(?:\s+\d+)*)\s*(?:HWY|HIGHWAY|RTE|ROUTE)?$"
)
_INTERSTATE_RE = re.compile(r"^(?:I|INTERSTATE)\s*(\d+[A-Z]?(?:\s+\d+[A-Z]?)*)\s*(?:HWY|HIGHWAY|RTE|ROUTE)?$")
_ROUTE_NUMBER_RE = re.compile(r"\d+[A-Z]?")
_ROUTE_KEY_PREFIX_RE = re.compile(r"^(US|I|[A-Z]{2}) (.+)$")
# Prefixes usaddress sometimes labels as PreDirectional on numbered routes.
_ROUTE_PREFIX_TOKENS = {"US", "U S", "I", "INTERSTATE", "STATE"}

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
    "ALY": "ALY",
    "AVENUE": "AVE",
    "AV": "AVE",
    "AVE": "AVE",
    "BOULEVARD": "BLVD",
    "BLVD": "BLVD",
    "CIRCLE": "CIR",
    "CIR": "CIR",
    "COURT": "CT",
    "CT": "CT",
    "CRESCENT": "CRES",
    "CRES": "CRES",
    "CREEK": "CRK",
    "CRK": "CRK",
    "CROSSING": "XING",
    "XING": "XING",
    "DRIVE": "DR",
    "DR": "DR",
    "EXPRESSWAY": "EXPY",
    "EXPY": "EXPY",
    "FREEWAY": "FWY",
    "FWY": "FWY",
    "HEIGHTS": "HTS",
    "HTS": "HTS",
    "HIGHWAY": "HWY",
    "HWY": "HWY",
    "JUNCTION": "JCT",
    "JCT": "JCT",
    "LANE": "LN",
    "LN": "LN",
    "LOOP": "LOOP",
    "MOUNTAIN": "MTN",
    "MTN": "MTN",
    "PARKWAY": "PKWY",
    "PKWY": "PKWY",
    "PASS": "PASS",
    "PATH": "PATH",
    "PIKE": "PIKE",
    "PLACE": "PL",
    "PL": "PL",
    "PLAZA": "PLZ",
    "PLZ": "PLZ",
    "ROAD": "RD",
    "RD": "RD",
    "ROUTE": "RTE",
    "RTE": "RTE",
    "ROW": "ROW",
    "RUN": "RUN",
    "SQUARE": "SQ",
    "SQ": "SQ",
    "STATION": "STA",
    "STA": "STA",
    "STREET": "ST",
    "ST": "ST",
    "TERRACE": "TER",
    "TER": "TER",
    "TRAIL": "TRL",
    "TRL": "TRL",
    "TURNPIKE": "TPKE",
    "TPKE": "TPKE",
    "WALK": "WALK",
    "WAY": "WAY",
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

_ROUTE_PREFIX_TOKENS.update(
    {code for code in _STATE_ALIASES.values() if len(code) == 2 and code.isalpha()}
)

_ORDINAL_WORDS = {
    "FIRST": "1ST",
    "SECOND": "2ND",
    "THIRD": "3RD",
    "FOURTH": "4TH",
    "FIFTH": "5TH",
    "SIXTH": "6TH",
    "SEVENTH": "7TH",
    "EIGHTH": "8TH",
    "NINTH": "9TH",
    "TENTH": "10TH",
    "ELEVENTH": "11TH",
    "TWELFTH": "12TH",
    "THIRTEENTH": "13TH",
    "FOURTEENTH": "14TH",
    "FIFTEENTH": "15TH",
    "SIXTEENTH": "16TH",
    "SEVENTEENTH": "17TH",
    "EIGHTEENTH": "18TH",
    "NINETEENTH": "19TH",
    "TWENTIETH": "20TH",
}

# These are lexical abbreviations, not fuzzy corrections. They are only
# applied to the parsed street-name component, where ``ST`` means Saint rather
# than Street (the latter has already been separated into ``street_suffix``).
_STREET_NAME_ALIASES = {
    "MOUNT": "MT",
    "SAINT": "ST",
    "FORT": "FT",
}

_SOUNDEX_CODES = {
    **dict.fromkeys("BFPV", "1"),
    **dict.fromkeys("CGJKQSXZ", "2"),
    **dict.fromkeys("DT", "3"),
    "L": "4",
    **dict.fromkeys("MN", "5"),
    "R": "6",
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


def _ordinal(number: int) -> str:
    """Return a canonical numeric ordinal such as ``9TH`` or ``21ST``."""

    remainder = number % 100
    if 10 < remainder < 14:
        suffix = "TH"
    else:
        suffix = {1: "ST", 2: "ND", 3: "RD"}.get(number % 10, "TH")
    return f"{number}{suffix}"


def normalize_street_name(value: Any) -> str:
    """Canonicalize common, deterministic US street-name variants.

    The function intentionally does not correct arbitrary spelling. It unifies
    lexical abbreviations and ordinal forms that have a single conventional
    representation. Approximate spelling is handled later by a phonetic block
    and remains visible to the scorer.
    """

    tokens: list[str] = []
    for token in normalize_text(value).split():
        ordinal = _ORDINAL_WORDS.get(token)
        if ordinal:
            tokens.append(ordinal)
            continue
        numeric = _NUMERIC_ORDINAL_RE.match(token)
        if numeric:
            tokens.append(_ordinal(int(numeric.group(1))))
            continue
        tokens.append(_STREET_NAME_ALIASES.get(token, token))
    return " ".join(tokens)


def _without_trailing_directional(text: str) -> str:
    tokens = text.split()
    if len(tokens) >= 2 and tokens[-1] in _DIRECTIONALS:
        return " ".join(tokens[:-1])
    return text


def street_name_key(
    street_name: Any,
    street_suffix: Any = "",
    state: Any = "",
) -> str:
    """Return a suffix-independent identity key for a parsed street name.

    Numbered state, US, and Interstate routes receive a common key so common
    source forms such as ``NC 55 HWY`` and ``STATE HWY 55`` can enter scoring.
    Trailing directionals on those routes (``US 70 HWY E``) are ignored in the
    key. A candidate is not accepted on this key alone: house number, locality,
    component scores, thresholds, and score margins still apply.
    """

    name = normalize_text(street_name)
    suffix = normalize_suffix(street_suffix)
    route_text = normalize_text(" ".join(value for value in (name, suffix) if value))
    for candidate in (route_text, _without_trailing_directional(route_text)):
        if not candidate:
            continue
        for pattern, prefix in (
            (_INTERSTATE_RE, "I"),
            (_US_ROUTE_RE, "US"),
        ):
            match = pattern.match(candidate)
            if match:
                route_number = _SPACE_RE.sub(" ", match.group(1)).strip()
                return f"{prefix} {route_number}"
        state_route = _STATE_ROUTE_RE.match(candidate)
        if state_route:
            route_label = state_route.group(1)
            route_number = state_route.group(2)
            state_code = normalize_state(state)
            if route_label.startswith("STATE"):
                route_label = state_code or "STATE"
            else:
                route_label = route_label.replace(" ", "")
                if state_code and route_label != state_code:
                    continue
            return f"{route_label} {route_number}"
    return normalize_street_name(name)


def route_component_keys(key: str) -> list[str]:
    """Return blocking keys for concurrent numbered routes such as ``US 15 501``.

    Single-number routes and ordinary street names return only themselves, so
    ``Main`` and ``US 70`` do not broaden. Concurrent US/state/Interstate keys
    also include each individual number so ``US 15 501`` can retrieve TIGER
    ``US 15`` or ``US 501``.
    """

    key = normalize_text(key)
    if not key:
        return []
    match = _ROUTE_KEY_PREFIX_RE.match(key)
    if not match:
        return [key]
    prefix, rest = match.group(1), match.group(2)
    numbers = _ROUTE_NUMBER_RE.findall(rest)
    if len(numbers) < 2:
        return [key]
    keys = [key]
    for number in numbers:
        component = f"{prefix} {number}"
        if component not in keys:
            keys.append(component)
    return keys


def _soundex_token(token: str) -> str:
    """Return a compact Soundex code used only for candidate blocking."""

    if not token:
        return ""
    if token.isdigit() or any(character.isdigit() for character in token):
        return token
    first = token[0]
    previous = _SOUNDEX_CODES.get(first, "")
    digits: list[str] = []
    for character in token[1:]:
        code = _SOUNDEX_CODES.get(character, "")
        if code and code != previous:
            digits.append(code)
        previous = code
    return (first + "".join(digits) + "000")[:4]


def street_name_phonetic_key(
    street_name: Any,
    street_suffix: Any = "",
    state: Any = "",
) -> str:
    """Return a phonetic block key for small street-name spelling variants."""

    key = street_name_key(street_name, street_suffix, state)
    return " ".join(_soundex_token(token) for token in key.split())


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


def intersection_component_keys(match_key: str) -> list[str]:
    """Expand a canonical intersection key with concurrent-route variants."""

    match_key = str(match_key or "").strip()
    if not match_key:
        return []
    if " || " not in match_key:
        return route_component_keys(match_key)
    left, right = match_key.split(" || ", 1)
    keys: list[str] = []
    for first in route_component_keys(left) or [left]:
        for second in route_component_keys(right) or [right]:
            key = intersection_key(first, second)
            if key and key not in keys:
                keys.append(key)
    return keys


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
    intersection_street_name_key: str = ""
    intersection_street_name_phonetic: str = ""

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
    def street_name_key(self) -> str:
        """Return the canonical, suffix-independent street identity."""

        return street_name_key(self.street_name, self.street_suffix, self.state_norm)

    @property
    def street_name_phonetic(self) -> str:
        """Return the phonetic candidate-block key for the street name."""

        return street_name_phonetic_key(
            self.street_name,
            self.street_suffix,
            self.state_norm,
        )

    @property
    def intersection_match_key(self) -> str:
        """Return an order-independent canonical key for an intersection."""

        if not self.is_intersection:
            return ""
        return intersection_key(self.street_name_key, self.intersection_street_name_key)

    @property
    def intersection_phonetic_key(self) -> str:
        """Return an order-independent phonetic intersection key."""

        if not self.is_intersection:
            return ""
        return intersection_key(
            self.street_name_phonetic,
            self.intersection_street_name_phonetic,
        )

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
            street_name_key=self.street_name_key,
            street_name_phonetic=self.street_name_phonetic,
            intersection_street_block=self.intersection_street_block,
            intersection_match_key=self.intersection_match_key,
            intersection_phonetic_key=self.intersection_phonetic_key,
        )
        return result


def _first(parsed: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        value = parsed.get(key)
        if value:
            return str(value)
    return ""


def _tag_address(raw: str) -> dict[str, str]:
    """Return usaddress component labels for one address string."""

    try:
        parsed, _ = usaddress.tag(raw)
        return {str(key): str(value) for key, value in parsed.items()}
    except usaddress.RepeatedLabelError:
        # Duplicate labels are uncommon but should not make a batch fail. The
        # token parser still provides useful first-occurrence fields.
        parsed: dict[str, str] = {}
        for token, label in usaddress.parse(raw):
            parsed.setdefault(label, token)
        return parsed


def _parse_single_address(
    address: Any,
    *,
    city: Any = "",
    state: Any = "",
    zip_code: Any = "",
) -> ParsedAddress:
    """Parse one non-intersection US address with usaddress, then canonicalize."""

    raw = "" if address is None else str(address)
    if raw.strip().upper() in {"NAN", "<NA>", "NONE"}:
        raw = ""
    dummy_house = False
    tagged = raw
    # usaddress is much more reliable with a house number present. Street
    # fragments such as ``Mount Moriah Rd`` otherwise mis-tag ``Mount``.
    if tagged and _NUMBER_RE.match(tagged) is None:
        tagged = f"1 {tagged}"
        dummy_house = True
    parsed = _tag_address(tagged) if raw else {}

    house_raw = _first(parsed, "AddressNumber")
    pre_raw = _first(parsed, "StreetNamePreDirectional")
    post_raw = _first(parsed, "StreetNamePostDirectional")
    pre = normalize_directional(pre_raw)
    # PreType holds route prefixes such as ``NC`` or ``State Hwy``; keep it
    # in the auditable street name so later route keys can unify forms.
    street = normalize_text(
        " ".join(
            part
            for part in (_first(parsed, "StreetNamePreType"), _first(parsed, "StreetName"))
            if part
        )
    )
    suffix = normalize_suffix(_first(parsed, "StreetNamePostType"))
    post = normalize_directional(post_raw)
    # usaddress sometimes labels a route prefix as a pre-directional
    # (``US 15-501`` -> PreDirectional=US, StreetName=15-501).
    prefix_token = normalize_text(pre_raw)
    if (
        prefix_token in _ROUTE_PREFIX_TOKENS
        and street
        and street.split()[0][:1].isdigit()
    ):
        street = normalize_text(f"{prefix_token} {street}")
        pre = ""
        pre_raw = ""
    # Trailing N/S/E/W on a numbered route belongs in post-directional, not
    # the street-name key (``US 70 HWY E``). Ordinary names such as East St
    # are left alone because they are not numbered routes.
    tokens = street.split()
    if len(tokens) >= 2 and tokens[-1] in _DIRECTIONALS:
        remainder = " ".join(tokens[:-1])
        if re.match(r"^(US|I|[A-Z]{2}) \d", street_name_key(remainder, suffix, state)):
            if not post:
                post = normalize_directional(tokens[-1])
            street = remainder
    # usaddress sometimes labels a directional street name as a postfix
    # (``100 SOUTH ST``) even though ``1 SOUTH ST`` tags it as StreetName.
    if not street:
        if post_raw:
            street = normalize_text(post_raw)
            post = ""
        elif pre_raw:
            street = normalize_text(pre_raw)
            pre = ""
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
        house_number=None if dummy_house else extract_house_number(house_raw),
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


def _looks_like_intersection(raw: str) -> bool:
    """Cheaply screen strings before running the full intersection regex."""

    upper = f" {raw.upper()} "
    return (
        any(separator in raw for separator in ("/", "&", "@", "+"))
        or " AT " in upper
        or " AND " in upper
        or " INTERSECTION OF " in upper
        or " CORNER OF " in upper
    )


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
    parts = _intersection_parts(raw) if raw and _looks_like_intersection(raw) else None
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
                intersection_street_name_key=right_parsed.street_name_key,
                intersection_street_name_phonetic=right_parsed.street_name_phonetic,
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
