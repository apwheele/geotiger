"""State-plane CRS selection for local interpolation."""

from __future__ import annotations

from functools import lru_cache

from pyproj import CRS
from pyproj.database import query_crs_info

from .normalize import normalize_state

DEFAULT_FALLBACK_CRS = "EPSG:5070"

# Fixed-zone state-plane systems most useful for the Southeast demo and common
# US analyst workflows. Multi-zone states are selected from the EPSG database
# below when coordinates are available.
STATE_PLANE_CRS = {
    "NC": "EPSG:2264",  # NAD83 / North Carolina (ftUS)
    "SC": "EPSG:2273",  # NAD83 / South Carolina (ft)
    "TN": "EPSG:2274",  # NAD83 / Tennessee (ftUS)
    "MD": "EPSG:2248",  # NAD83 / Maryland (ftUS)
    "DE": "EPSG:2243",  # NAD83 / Delaware (ftUS)
    "NJ": "EPSG:3424",  # NAD83 / New Jersey (ftUS)
    "PA": "EPSG:2272",  # NAD83 / Pennsylvania South (ftUS)
}

_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NM": "New Mexico",
    "NY": "New York", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "RI": "Rhode Island", "SD": "South Dakota", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


@lru_cache(maxsize=1)
def _nad83_state_plane_candidates() -> tuple[tuple[str, int, str], ...]:
    results = []
    for info in query_crs_info(auth_name="EPSG", pj_types="PROJECTED_CRS"):
        if "NAD83" in info.name and " / " in info.name:
            results.append((info.name, int(info.code), info.auth_name))
    return tuple(results)


def state_plane_crs(
    state: str | None,
    *,
    longitude: float | None = None,
    latitude: float | None = None,
    fallback: str = DEFAULT_FALLBACK_CRS,
) -> str:
    """Return a reasonable NAD83 state-plane CRS for a USPS state code.

    Fixed-zone systems use an explicit EPSG code. For states with multiple
    zones, the EPSG database's area of use is used when a representative point
    is available; otherwise the first matching NAD83 state-plane CRS is used.
    ``EPSG:5070`` remains the national fallback when no state can be inferred.
    """

    code = normalize_state(state)
    if code in STATE_PLANE_CRS:
        return STATE_PLANE_CRS[code]
    state_name = _STATE_NAMES.get(code)
    if not state_name:
        return fallback
    candidates = []
    for name, epsg_code, auth_name in _nad83_state_plane_candidates():
        if not name.startswith(f"NAD83 / {state_name}"):
            continue
        if "(ftUS)" not in name and "(ft)" not in name:
            continue
        crs = CRS.from_authority(auth_name, epsg_code)
        area = crs.area_of_use
        if longitude is not None and latitude is not None:
            if area.west <= longitude <= area.east and area.south <= latitude <= area.north:
                return f"EPSG:{epsg_code}"
        candidates.append(epsg_code)
    return f"EPSG:{candidates[0]}" if candidates else fallback
