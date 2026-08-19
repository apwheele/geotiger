"""Expand TIGER/Line address ranges into point-address candidates."""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import CRS, Transformer
from shapely import wkt
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge

from .normalize import (
    intersection_key,
    normalize_state,
    normalize_text,
    normalize_zip,
    parse_address,
    street_block_key,
    street_name_key,
    street_name_phonetic_key,
)
from .schema import ADDRESS_COLUMNS, normalize_source_type
from .state_plane import state_plane_crs


@dataclass(frozen=True)
class InterpolationConfig:
    """Controls the geometry-to-address interpolation.

    ``end_offset_m`` is measured from each end of the *currently supplied*
    segment. This is important when the source edge has been clipped: the
    address fraction is still based on the source range, but the usable point
    is moved away from the clipped/dangling endpoint. ``side_offset_m`` moves
    the point to the left or right of the directed TIGER line. When
    ``include_intersections`` is true, crossing named street geometries also
    contribute explicit point rows for order-independent intersection matching.
    """

    projected_crs: str | None = None
    output_crs: str = "EPSG:4326"
    end_offset_m: float = 5.0
    side_offset_m: float = 5.0
    max_addresses_per_range: int = 10_000
    include_intersections: bool = True


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


def _offset_points(
    line: LineString,
    distances: np.ndarray,
    side: str,
    offset: float,
) -> np.ndarray:
    """Vectorized equivalent of :func:`_offset_point` for a range side.

    TIGER ranges commonly expand to tens of thousands of points. Keeping the
    interpolation and local tangent calculation in Shapely's vectorized
    ufuncs avoids one Python/Shapely/pyproj call per generated address.
    """

    points = shapely.line_interpolate_point(line, distances)
    if offset == 0:
        return points
    length = line.length
    epsilon = min(max(length / 10_000, 0.01), 2.0)
    before = shapely.line_interpolate_point(line, np.maximum(distances - epsilon, 0.0))
    after = shapely.line_interpolate_point(line, np.minimum(distances + epsilon, length))
    point_x = shapely.get_x(points)
    point_y = shapely.get_y(points)
    dx = shapely.get_x(after) - shapely.get_x(before)
    dy = shapely.get_y(after) - shapely.get_y(before)
    norm = np.hypot(dx, dy)
    direction = 1.0 if side == "L" else -1.0
    with np.errstate(divide="ignore", invalid="ignore"):
        offset_x = point_x + direction * offset * (-dy / norm)
        offset_y = point_y + direction * offset * (dx / norm)
    offset_x = np.where(norm == 0, point_x, offset_x)
    offset_y = np.where(norm == 0, point_y, offset_y)
    return shapely.points(offset_x, offset_y)


def _street_parts(full_name: Any) -> tuple[str, str, str, str]:
    parsed = parse_address(f"1 {full_name}")
    return parsed.pre_directional, parsed.street_name, parsed.street_suffix, parsed.post_directional


def _state_from_row(row: pd.Series, state_column: str | None, configured: str | None) -> str:
    value = _value(row, state_column, configured or "")
    return normalize_state(value)


def _intersection_points(value: Any) -> list[Point]:
    """Extract point parts from a line intersection, ignoring overlaps."""

    if value is None or value.is_empty:
        return []
    if value.geom_type == "Point":
        return [value]
    if value.geom_type == "MultiPoint":
        return list(value.geoms)
    if value.geom_type == "GeometryCollection":
        points: list[Point] = []
        for part in value.geoms:
            points.extend(_intersection_points(part))
        return points
    return []


def _prepare_intersection_rows(
    frame: gpd.GeoDataFrame,
    projected: gpd.GeoDataFrame,
    *,
    full_col: str,
    state_col: str | None,
    lzip_col: str | None,
    rzip_col: str | None,
    lcity_col: str | None,
    rcity_col: str | None,
    state: str | None,
    projected_crs: str,
    transformer: Transformer,
    source: str,
    source_type: str,
    source_priority: int,
) -> list[dict[str, Any]]:
    """Create one canonical point row for each pair of crossing street names."""

    metadata: list[dict[str, Any]] = []
    lines: list[LineString | None] = []
    for row_number, (original, projected_row) in enumerate(
        zip(frame.itertuples(index=False), projected.itertuples(index=False))
    ):
        original_row = pd.Series(original, index=frame.columns)
        projected_row = pd.Series(projected_row, index=projected.columns)
        line = _line_geometry(projected_row["geometry"])
        lines.append(line)
        full_name = str(_value(original_row, full_col, ""))
        parsed = parse_address(
            f"1 {full_name}",
            state=_state_from_row(original_row, state_col, state),
        )
        if line is None or line.length == 0 or not parsed.street_norm or parsed.is_intersection:
            metadata.append({"street_norm": ""})
            continue
        range_id = _value(original_row, None, row_number)
        for id_col in ("TLID", "LINEARID", "RANGE_ID", "ID"):
            actual = {str(col).upper(): col for col in frame.columns}.get(id_col)
            if actual is not None:
                range_id = _value(original_row, actual, row_number)
                break
        city = normalize_text(
            _value(original_row, lcity_col) or _value(original_row, rcity_col)
        )
        zip5 = normalize_zip(_value(original_row, lzip_col) or _value(original_row, rzip_col))
        metadata.append(
            {
                "street_norm": parsed.street_norm,
                "street_block": parsed.street_block,
                "street_name_key": parsed.street_name_key,
                "street_name_phonetic": parsed.street_name_phonetic,
                "pre_directional": parsed.pre_directional,
                "street_name": parsed.street_name,
                "street_suffix": parsed.street_suffix,
                "post_directional": parsed.post_directional,
                "city": city,
                "state": _state_from_row(original_row, state_col, state),
                "zip5": zip5,
                "range_id": str(range_id),
            }
        )

    if not len(projected):
        return []
    pairs = projected.sindex.query(projected.geometry, predicate="intersects")
    seen: set[tuple[str, str, float, float]] = set()
    output: list[dict[str, Any]] = []
    for left_index, right_index in zip(pairs[0], pairs[1]):
        left_index, right_index = int(left_index), int(right_index)
        if left_index >= right_index:
            continue
        left_meta, right_meta = metadata[left_index], metadata[right_index]
        if not left_meta.get("street_norm") or not right_meta.get("street_norm"):
            continue
        if left_meta["street_norm"] == right_meta["street_norm"]:
            continue
        line_left, line_right = lines[left_index], lines[right_index]
        if line_left is None or line_right is None:
            continue
        for point in _intersection_points(line_left.intersection(line_right)):
            key = intersection_key(left_meta["street_norm"], right_meta["street_norm"])
            match_key = intersection_key(
                left_meta["street_name_key"], right_meta["street_name_key"]
            )
            phonetic_key = intersection_key(
                left_meta["street_name_phonetic"], right_meta["street_name_phonetic"]
            )
            x_key, y_key = round(float(point.x), 2), round(float(point.y), 2)
            dedupe_key = (key, left_meta.get("state", ""), x_key, y_key)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            first, second = sorted((left_meta, right_meta), key=lambda item: item["street_norm"])
            longitude, latitude = transformer.transform(point.x, point.y)
            address_id = (
                f"{source}:intersection:{key}:{x_key}:{y_key}"
            )
            output.append(
                {
                    "address_id": address_id,
                    "range_id": f"{first['range_id']}|{second['range_id']}",
                    "house_number": None,
                    "parity": "intersection",
                    "side": "I",
                    "pre_directional": first["pre_directional"],
                    "street_name": first["street_name"],
                    "street_suffix": first["street_suffix"],
                    "post_directional": first["post_directional"],
                    "street_norm": key,
                    "street_block": first["street_block"],
                    "street_name_key": first["street_name_key"],
                    "street_name_phonetic": first["street_name_phonetic"],
                    "city": first["city"] or second["city"],
                    "city_norm": first["city"] or second["city"],
                    "state": first["state"] or second["state"],
                    "state_norm": first["state"] or second["state"],
                    "zip5": first["zip5"] or second["zip5"],
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                    "geometry_wkt": Point(float(longitude), float(latitude)).wkt,
                    "interpolation_crs": str(projected_crs),
                    "source": source,
                    "source_type": source_type,
                    "source_priority": int(source_priority),
                    "source_record_id": address_id,
                    "is_intersection": True,
                    "intersection_key": key,
                    "intersection_street_norm": second["street_norm"],
                    "intersection_street_block": second["street_block"],
                    "intersection_match_key": match_key,
                    "intersection_phonetic_key": phonetic_key,
                }
            )
    return output


def prepare_ranges(
    ranges: gpd.GeoDataFrame | pd.DataFrame,
    *,
    config: InterpolationConfig | None = None,
    state: str | None = None,
    source: str = "unknown",
    source_type: str = "tiger",
    source_priority: int = 20,
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
    source_type, source_priority:
        Provenance kind and lower-is-better tie-break priority when combining
        TIGER rows with local address or parcel references.

    Notes
    -----
    Intersection points are geometric line crossings. TIGER/Line does not
    reliably encode grade separation, so bridges and tunnels can produce
    apparent intersections; set ``include_intersections=False`` when that is
    not appropriate for the source data.
    """

    config = config or InterpolationConfig()
    source_type = normalize_source_type(source_type)
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
    output_columns: dict[str, list[Any]] = {column: [] for column in ADDRESS_COLUMNS}
    full_col = _column(frame, "full_name", required=True)
    lf_col = _column(frame, "left_from", required=True)
    lt_col = _column(frame, "left_to", required=True)
    rf_col = _column(frame, "right_from", required=True)
    rt_col = _column(frame, "right_to", required=True)
    lzip_col, rzip_col = _column(frame, "left_zip"), _column(frame, "right_zip")
    lcity_col, rcity_col = _column(frame, "left_city"), _column(frame, "right_city")

    id_columns = {str(col).upper(): col for col in frame.columns}
    stable_id_col = next(
        (id_columns[name] for name in ("TLID", "LINEARID", "RANGE_ID", "ID") if name in id_columns),
        None,
    )

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
        range_id = _value(original_row, stable_id_col, row_number)
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
            number_values = _range_numbers(start, end, config.max_addresses_per_range)
            if not number_values:
                continue
            numbers = np.asarray(number_values, dtype=np.int64)
            zip5 = normalize_zip(_value(original_row, zip_col))
            city = normalize_text(_value(original_row, city_col))
            if start == end:
                fractions = np.zeros(len(numbers), dtype=float)
            else:
                fractions = (numbers - start) / (end - start)
                fractions = np.clip(fractions, 0.0, 1.0)
            distances = offset + fractions * usable_length
            points_projected = _offset_points(line, distances, side, side_offset)
            point_x = shapely.get_x(points_projected)
            point_y = shapely.get_y(points_projected)
            longitude, latitude = transformer.transform(point_x, point_y)
            points_wgs84 = shapely.points(longitude, latitude)
            n_rows = len(numbers)
            street_norm = normalize_text(" ".join(p for p in (pre, street_name, suffix, post) if p))
            common = {
                "range_id": str(range_id),
                "parity": ["even" if number % 2 == 0 else "odd" for number in numbers],
                "side": side,
                "pre_directional": pre,
                "street_name": street_name,
                "street_suffix": suffix,
                "post_directional": post,
                "street_norm": street_norm,
                "street_block": street_block_key(street_name),
                "street_name_key": street_name_key(street_name, suffix, state_value),
                "street_name_phonetic": street_name_phonetic_key(
                    street_name,
                    suffix,
                    state_value,
                ),
                "city": city,
                "city_norm": city,
                "state": state_value,
                "state_norm": state_value,
                "zip5": zip5,
                "interpolation_crs": str(projected_crs),
                "source": source,
                "source_type": source_type,
                "source_priority": int(source_priority),
                "source_record_id": str(range_id),
                "is_intersection": False,
                "intersection_key": "",
                "intersection_street_norm": "",
                "intersection_street_block": "",
                "intersection_match_key": "",
                "intersection_phonetic_key": "",
            }
            output_columns["address_id"].extend(
                f"{source}:{range_id}:{side}:{house_number}" for house_number in numbers
            )
            output_columns["house_number"].extend(numbers.tolist())
            for column, value in common.items():
                if isinstance(value, list):
                    output_columns[column].extend(value)
                else:
                    output_columns[column].extend([value] * n_rows)
            output_columns["latitude"].extend(np.asarray(latitude, dtype=float).tolist())
            output_columns["longitude"].extend(np.asarray(longitude, dtype=float).tolist())
            output_columns["geometry_wkt"].extend(
                shapely.to_wkt(points_wgs84, rounding_precision=-1).tolist()
            )
    if config.include_intersections:
        intersection_rows = _prepare_intersection_rows(
            frame,
            projected,
            full_col=full_col,
            state_col=state_col,
            lzip_col=lzip_col,
            rzip_col=rzip_col,
            lcity_col=lcity_col,
            rcity_col=rcity_col,
            state=state,
            projected_crs=str(projected_crs),
            transformer=transformer,
            source=source,
            source_type=source_type,
            source_priority=source_priority,
        )
        for row in intersection_rows:
            for column in ADDRESS_COLUMNS:
                output_columns[column].append(row.get(column))
    return pd.DataFrame(output_columns, columns=ADDRESS_COLUMNS)


def ranges_from_file(path: str, *, layer: str | None = None) -> gpd.GeoDataFrame:
    """Read a GeoJSON, Shapefile, GeoPackage, or GeoParquet range file."""

    return gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
