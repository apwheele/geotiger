"""GeoTIGER: local-first US geocoding from TIGER/Line address ranges."""

from .geocoder import Geocoder, GeocoderConfig, GeocodeResult, TimingReport
from .interpolate import InterpolationConfig, prepare_ranges
from .normalize import ParsedAddress, normalize_state, normalize_text, parse_address
from .sources import download_tiger_ranges, load_ranges, save_ranges
from .store import GeoTIGERStore

__all__ = [
    "GeocodeResult",
    "Geocoder",
    "GeocoderConfig",
    "GeoTIGERStore",
    "InterpolationConfig",
    "ParsedAddress",
    "TimingReport",
    "download_tiger_ranges",
    "load_ranges",
    "normalize_text",
    "normalize_state",
    "parse_address",
    "prepare_ranges",
    "save_ranges",
]
