"""Shared schema and source-preference helpers."""

from __future__ import annotations

from typing import Any

ADDRESS_COLUMNS = [
    "address_id",
    "range_id",
    "house_number",
    "parity",
    "side",
    "pre_directional",
    "street_name",
    "street_suffix",
    "post_directional",
    "street_norm",
    "street_block",
    "street_name_key",
    "street_name_phonetic",
    "city",
    "city_norm",
    "state",
    "state_norm",
    "zip5",
    "latitude",
    "longitude",
    "geometry_wkt",
    "interpolation_crs",
    "source",
    "source_type",
    "source_priority",
    "source_record_id",
    "is_intersection",
    "intersection_key",
    "intersection_street_norm",
    "intersection_street_block",
    "intersection_match_key",
    "intersection_phonetic_key",
]

# Lower numbers are preferred when match scores are otherwise equal. The
# names describe the kind of reference row, not the arbitrary source label.
DEFAULT_SOURCE_PREFERENCE = ("individual", "parcel", "tiger")
SOURCE_PRIORITIES = {
    "individual": 0,
    "address": 0,
    "address_point": 0,
    "parcel": 10,
    "tiger": 20,
}


def normalize_source_type(value: Any) -> str:
    """Normalize a reference source kind used for deterministic tie-breaking."""

    if value is None:
        value = "custom"
    value = str(value).strip()
    if not value or value.upper() in {"NAN", "NONE", "<NA>"}:
        value = "custom"
    value = value.lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "addresses": "individual",
        "individual_addresses": "individual",
        "address_points": "individual",
        "parcel_data": "parcel",
        "parcels": "parcel",
        "tiger_line": "tiger",
        "tiger_ranges": "tiger",
    }
    return aliases.get(value, value or "custom")


def source_priority(source_type: Any, preference: tuple[str, ...] | None = None) -> int:
    """Return a lower-is-better priority for a reference source kind."""

    normalized = normalize_source_type(source_type)
    if preference is not None:
        preferred = {
            normalize_source_type(name): position * 10
            for position, name in enumerate(preference)
        }
        if normalized in preferred:
            return preferred[normalized]
    return SOURCE_PRIORITIES.get(normalized, 100)


def address_cache_key(row: Any) -> str:
    """Build a stable key from the normalized fields used for matching."""

    values = []
    for column in ("house_number", "street_norm", "city_norm", "state_norm", "zip5"):
        value = row.get(column, "") if hasattr(row, "get") else ""
        if value is None:
            value = ""
        text = str(value).strip()
        if text.upper() in {"NAN", "NONE", "<NA>"}:
            text = ""
        values.append(text)
    return "\x1f".join(values)
