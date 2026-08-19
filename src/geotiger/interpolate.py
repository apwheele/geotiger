"""Expand TIGER/Line address ranges into point-address candidates."""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import pandas as pd
from pyproj import CRS, Transformer
from shapely import wkt
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge

from .normalize import (
    normalize_state,
    normalize_text,
    normalize_zip,
    parse_address,
    street_block_key,
)
from .state_plane import state_plane_crs


@dataclass(frozen=True)
class InterpolationConfig:
    """Controls the geometry-to-address interpolation.

    ``end_offset_m`` is measured from each end of the *currently supplied*
    segment. This is important when the source edge has been clipped: the
    address fraction is still based on the source range, but the usable point
    is moved away from the clipped/dangling endpoint. ``side_offset_m`` moves
    the point to the left or right of the directed TIGER line.
    """

    projected_crs: str | None = None
    output_crs: str = "EPSG:4326"
    end_offset_m: float = 5.0
    side_offset_m: float = 5.0
    max_addresses_per_range: int = 10_000


_CANDIDATES = {
    "full_name": ["FULLNAME", "FULL_NAME", "STREET", "STREET_NAME", "ROAD", "NAME"],
    "left_from": ["LFROMADD", "LFROMHN", "L_FROM_ADD", "LEFT_FROM", "LEFTFROM", "L_FROM"],
    "left_to": ["LTOADD", "LTOHN", "L_TO_ADD", "LEFT_TO", "LEFTTO", "L_TO"],
    "right_from": ["RFROMADD", "RFROMHN", "R_FROM_ADD", "RIGHT_FROM", "RIGHTFROM", "R_FROM"],
    "right_to": ["RTOADD", "RTOHN", "R_TO_ADD", "RIGHT_TO", "RIGHTTO", "R_TO"],
    "left_zip": ["ZIPL", "ZIP_LEFT", "LEFT_ZIP", "ZIPL5"],
    "right_zip": ["ZIPR", "ZIP_RIGHT", "RIGHT_ZIP", "ZIPR5"],
    "left_city": ["CITYL", "CITY_LEFT", "LEFT_CITY", "PLACEL", "PLACE_LEFT"],
    "right_city": ["CITYR", "CITY_RIGHT", "RIGHT_CITY", "PLACER", "PLACE_RIGHT"],
    "state": ["STUSPS", "STATE", "STATE_ABBR", "STATE_NAME"],
}


def _column(frame: pd.DataFrame, key: str, required: bool = False) -> str | None:
    upper = {str(col).upper(): col for col in frame.columns}
    for name in _CANDIDATES[key]:
        if name in upper:
            return upper[name]
    if required:
        raise ValueError(f"Could not find required TIGER range column for {key!r}")
    return None


def _value(row: pd.Series, column: str | None, default: Any = "") -> Any:
    if column is None:
        return default
    value = row[column]
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    return value


def _number(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _line_geometry(value: Any) -> LineString | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = wkt.loads(value)
    if isinstance(value, LineString):
        return value
    if isinstance(value, MultiLineString):
        merged = linemerge(value)
        if isinstance(merged, LineString):
            return merged
        return max(merged.geoms, key=lambda item: item.length, default=None)
    return None


def _range_numbers(start: int | None, end: int | None, maximum: int) -> list[int]:
    if start is None or end is None:
        return []
    if start == end:
        return [start]
    step = 2 if abs(end - start) >= 2 else 1
    step *= 1 if end > start else -1
    numbers = list(range(start, end + step, step))
    if len(numbers) > maximum:
        raise ValueError(
            f"Address range {start}-{end} expands to {len(numbers):,} rows; "
            f"increase max_addresses_per_range from {maximum:,} if intentional."
        )
    return numbers


def _offset_point(line: LineString, distance: float, side: str, offset: float) -> Point:
    """Offset a point using the local tangent, robust to clipped segments."""

    length = line.length
    point = line.interpolate(distance)
    epsilon = min(max(length / 10_000, 0.01), 2.0)
    before = line.interpolate(max(0.0, distance - epsilon))
    after = line.interpolate(min(length, distance + epsilon))
    dx = after.x - before.x
    dy = after.y - before.y
    norm = math.hypot(dx, dy)
    if norm == 0:
        return point
    left_x, left_y = -dy / norm, dx / norm
    direction = 1.0 if side == "L" else -1.0
    return Point(point.x + direction * offset * left_x, point.y + direction * offset * left_y)


def _street_parts(full_name: Any) -> tuple[str, str, str, str]:
    parsed = parse_address(f"1 {full_name}")
    return parsed.pre_directional, parsed.street_name, parsed.street_suffix, parsed.post_directional


def _state_from_row(row: pd.Series, state_column: str | None, configured: str | None) -> str:
    value = _value(row, state_column, configured or "")
    return normalize_state(value)


def prepare_ranges(
    ranges: gpd.GeoDataFrame | pd.DataFrame,
    *,
    config: InterpolationConfig | None = None,
    state: str | None = None,
    source: str = "unknown",
) -> pd.DataFrame:
    """Expand TIGER/Line ranges into one row per address number and side.

    Parameters
    ----------
    ranges:
        A GeoDataFrame or dataframe with a ``geometry`` column. Standard TIGER
        names such as ``LFROMADD`` and ``ZIPL`` are detected automatically.
    config:
        Projection, endpoint, side-offset, and expansion limits.
    state:
        Optional state code used when the source does not contain an USPS state
        column (recommended for ``STATEFP``-only downloads).
    """

    config = config or InterpolationConfig()
    frame = ranges.copy()
    if "geometry" not in frame.columns:
        raise ValueError("ranges must include a geometry column")
    if not isinstance(frame, gpd.GeoDataFrame):
        frame = gpd.GeoDataFrame(frame, geometry="geometry", crs=getattr(ranges, "crs", None))
    if frame.crs is None:
        warnings.warn(
            "Input CRS is missing; assuming EPSG:4269 for TIGER/Line geometry.",
            stacklevel=2,
        )
        frame = frame.set_crs("EPSG:4269")
    state_col = _column(frame, "state")
    inferred_state = normalize_state(state)
    if not inferred_state and state_col:
        values = frame[state_col].dropna().map(normalize_state)
        inferred_state = values.iloc[0] if len(values) else ""
    projected_crs = config.projected_crs or state_plane_crs(inferred_state)
    projected_crs_obj = CRS.from_user_input(projected_crs)
    distance_units_per_meter = 1.0 / projected_crs_obj.axis_info[0].unit_conversion_factor
    projected = frame.to_crs(projected_crs)
    transformer = Transformer.from_crs(
        projected_crs,
        config.output_crs,
        always_xy=True,
    )
    output_rows: list[dict[str, Any]] = []
    full_col = _column(frame, "full_name", required=True)
    lf_col = _column(frame, "left_from", required=True)
    lt_col = _column(frame, "left_to", required=True)
    rf_col = _column(frame, "right_from", required=True)
    rt_col = _column(frame, "right_to", required=True)
    lzip_col, rzip_col = _column(frame, "left_zip"), _column(frame, "right_zip")
    lcity_col, rcity_col = _column(frame, "left_city"), _column(frame, "right_city")

    for row_number, (original, projected_row) in enumerate(
        zip(frame.itertuples(index=False), projected.itertuples(index=False))
    ):
        original_row = pd.Series(original, index=frame.columns)
        projected_series = pd.Series(projected_row, index=projected.columns)
        line = _line_geometry(projected_series["geometry"])
        if line is None or line.length == 0:
            continue
        full_name = str(_value(original_row, full_col, ""))
        pre, street_name, suffix, post = _street_parts(full_name)
        if not street_name:
            continue
        state_value = _state_from_row(original_row, state_col, state)
        range_id = _value(original_row, _column(frame, "full_name"), row_number)
        # Prefer a stable source row id when present.
        for id_col in ("TLID", "LINEARID", "RANGE_ID", "ID"):
            matches = {str(col).upper(): col for col in frame.columns}
            if id_col in matches:
                range_id = _value(original_row, matches[id_col], row_number)
                break
        end_offset = max(float(config.end_offset_m), 0.0) * distance_units_per_meter
        side_offset = max(float(config.side_offset_m), 0.0) * distance_units_per_meter
        offset = min(end_offset, line.length * 0.49)
        usable_length = max(line.length - 2 * offset, 0.0)

        for side, start_col, end_col, zip_col, city_col in (
            ("L", lf_col, lt_col, lzip_col, lcity_col),
            ("R", rf_col, rt_col, rzip_col, rcity_col),
        ):
            start = _number(_value(original_row, start_col))
            end = _number(_value(original_row, end_col))
            numbers = _range_numbers(start, end, config.max_addresses_per_range)
            if not numbers:
                continue
            zip5 = normalize_zip(_value(original_row, zip_col))
            city = normalize_text(_value(original_row, city_col))
            for house_number in numbers:
                fraction = 0.0 if start == end else (house_number - start) / (end - start)
                fraction = max(0.0, min(1.0, fraction))
                distance = offset + fraction * usable_length
                point_projected = _offset_point(line, distance, side, side_offset)
                longitude, latitude = transformer.transform(point_projected.x, point_projected.y)
                output_rows.append(
                    {
                        "address_id": f"{source}:{range_id}:{side}:{house_number}",
                        "range_id": str(range_id),
                        "house_number": house_number,
                        "parity": "even" if house_number % 2 == 0 else "odd",
                        "side": side,
                        "pre_directional": pre,
                        "street_name": street_name,
                        "street_suffix": suffix,
                        "post_directional": post,
                        "street_norm": normalize_text(
                            " ".join(p for p in (pre, street_name, suffix, post) if p)
                        ),
                        "street_block": street_block_key(street_name),
                        "city": city,
                        "city_norm": city,
                        "state": state_value,
                        "state_norm": state_value,
                        "zip5": zip5,
                        "latitude": float(latitude),
                        "longitude": float(longitude),
                        "geometry_wkt": Point(longitude, latitude).wkt,
                        "interpolation_crs": str(projected_crs),
                        "source": source,
                    }
                )
    return pd.DataFrame(output_rows)


def ranges_from_file(path: str, *, layer: str | None = None) -> gpd.GeoDataFrame:
    """Read a GeoJSON, Shapefile, GeoPackage, or GeoParquet range file."""

    return gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
