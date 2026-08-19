"""GeoTIGER: local-first US geocoding from TIGER/Line address ranges."""

from .combined import CombinedGeocoder
from .demo_data import DURHAM_CRIME_URL, load_durham_crime, make_durham_inputs, midpoint_address
from .geocoder import Geocoder, GeocoderConfig, GeocodeResult, TimingReport
from .interpolate import InterpolationConfig, prepare_ranges
from .normalize import (
    ParsedAddress,
    normalize_state,
    normalize_text,
    parse_address,
    street_block_key,
)
from .prepare import prepare_addresses, prepare_combined, prepare_parcels, save_prepared
from .sources import download_tiger_ranges, load_ranges, save_ranges
from .state_plane import DEFAULT_FALLBACK_CRS, state_plane_crs
from .store import GeoTIGERStore

__all__ = [
    "GeocodeResult",
    "Geocoder",
    "GeocoderConfig",
    "CombinedGeocoder",
    "GeoTIGERStore",
    "InterpolationConfig",
    "ParsedAddress",
    "TimingReport",
    "download_tiger_ranges",
    "DEFAULT_FALLBACK_CRS",
    "DURHAM_CRIME_URL",
    "load_ranges",
    "load_durham_crime",
    "make_durham_inputs",
    "midpoint_address",
    "normalize_text",
    "normalize_state",
    "parse_address",
    "prepare_ranges",
    "prepare_addresses",
    "prepare_parcels",
    "prepare_combined",
    "save_prepared",
    "save_ranges",
    "state_plane_crs",
    "street_block_key",
]
