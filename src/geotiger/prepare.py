"""Prepare local parcel and address-point tables for the common reference schema."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from pyproj import CRS
from shapely import wkt
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from .interpolate import prepare_ranges
from .normalize import normalize_state, normalize_text, normalize_zip, parse_address
from .schema import (
    ADDRESS_COLUMNS,
    DEFAULT_SOURCE_PREFERENCE,
    normalize_source_type,
)
from .schema import (
    source_priority as source_priority_for,
)

_ADDRESS_COLUMNS = (
    "ADDRESS",
    "ADDRESS_LINE",
    "ADDRESS1",
    "FULL_ADDRESS",
    "FULLADDR",
    "SITUS_ADDRESS",
    "SITUSADDR",
    "SITE_ADDRESS",
    "SITEADDR",
    "LOCATION",
)
_HOUSE_NUMBER_COLUMNS = (
    "HOUSE_NUMBER",
    "ADDRESS_NUMBER",
    "ADDRNUM",
    "STREET_NUMBER",
    "HOUSE_NUM",
    "SITUS_NUMBER",
    "SITUS_NUM",
    "HNUM",
)
_STREET_COLUMNS = (
    "STREET",
    "STREET_NAME",
    "ROAD",
    "ROAD_NAME",
    "ST_NAME",
    "SITUS_STREET",
    "STREETNAME",
)
_SUFFIX_COLUMNS = (
    "STREET_SUFFIX",
    "STREET_TYPE",
    "SUFFIX",
    "ST_TYPE",
    "SITUS_SUFFIX",
)
_PRE_DIRECTIONAL_COLUMNS = ("PRE_DIRECTIONAL", "PREFIX", "STREET_PREFIX", "PREDIR")
_POST_DIRECTIONAL_COLUMNS = ("POST_DIRECTIONAL", "DIRECTION", "POSTDIR")
_CITY_COLUMNS = ("CITY", "CITY_NAME", "MUNICIPALITY", "PLACENAME", "PLACE")
_STATE_COLUMNS = ("STATE", "STATE_ABBR", "STUSPS", "STATEFP", "STATE_FIPS")
_ZIP_COLUMNS = ("ZIP", "ZIP5", "ZIP_CODE", "ZIPCODE", "POSTAL_CODE", "ZIPCODE5")
_ID_COLUMNS = ("ADDRESS_ID", "ADDR_ID", "OBJECTID", "OBJECT_ID", "ID", "UUID")
_PARCEL_ID_COLUMNS = (
    "PARCEL_ID",
    "PARCELID",
    "PARCEL_NUMBER",
    "PARCELNO",
    "PIN",
    "APN",
    "REID",
    "PID",
    "OBJECTID",
    "OBJECT_ID",
    "ID",
)
_LONGITUDE_COLUMNS = ("LONGITUDE", "LON", "X")
_LATITUDE_COLUMNS = ("LATITUDE", "LAT", "Y")


def _column(
    frame: pd.DataFrame,
    requested: str | None,
    aliases: Sequence[str],
    *,
    label: str,
) -> str | None:
    by_upper = {str(column).upper(): column for column in frame.columns}
    if requested is not None:
        result = by_upper.get(str(requested).upper())
        if result is None:
            raise ValueError(f"Could not find {label} column {requested!r}")
        return result
    for alias in aliases:
        if alias in by_upper:
            return by_upper[alias]
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


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return bool(str(value).strip()) and str(value).strip().upper() not in {"NAN", "NONE", "<NA>"}


def _geometry(value: Any) -> BaseGeometry | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = wkt.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, BaseGeometry) or value.is_empty:
        return None
    # representative_point is inside a parcel polygon and also gives a useful
    # fallback for geometry collections or address features supplied as lines.
    return value if value.geom_type == "Point" else value.representative_point()


def _points_wgs84(
    frame: pd.DataFrame,
    *,
    geometry_column: str | None,
    longitude_column: str | None,
    latitude_column: str | None,
    input_crs: str | CRS | None,
    output_crs: str | CRS,
) -> gpd.GeoSeries:
    if geometry_column is None and isinstance(frame, gpd.GeoDataFrame):
        geometry_column = frame.geometry.name
    geometry_column = _column(frame, geometry_column, ("GEOMETRY",), label="geometry")
    source_crs: str | CRS | None = input_crs
    if geometry_column is not None:
        values = [_geometry(value) for value in frame[geometry_column].tolist()]
        source_crs = getattr(frame, "crs", None) or source_crs or "EPSG:4326"
    else:
        longitude_column = _column(
            frame, longitude_column, _LONGITUDE_COLUMNS, label="longitude"
        )
        latitude_column = _column(frame, latitude_column, _LATITUDE_COLUMNS, label="latitude")
        if longitude_column is None or latitude_column is None:
            raise ValueError(
                "Address and parcel reference tables need a geometry column or both "
                "longitude and latitude columns"
            )
        values = []
        for longitude, latitude in zip(frame[longitude_column], frame[latitude_column]):
            try:
                values.append(Point(float(longitude), float(latitude)))
            except (TypeError, ValueError):
                values.append(None)
        source_crs = source_crs or "EPSG:4326"

    missing = [str(index) for index, value in zip(frame.index, values) if value is None]
    if missing:
        sample = ", ".join(missing[:5])
        suffix = "..." if len(missing) > 5 else ""
        raise ValueError(
            f"Could not make a point for {len(missing)} reference rows: {sample}{suffix}"
        )
    points = gpd.GeoSeries(values, index=frame.index, crs=source_crs)
    return points.to_crs(output_crs)


def _component_address(
    row: pd.Series,
    *,
    address_column: str | None,
    house_number_column: str | None,
    pre_directional_column: str | None,
    street_name_column: str | None,
    street_suffix_column: str | None,
    post_directional_column: str | None,
) -> str:
    address = _value(row, address_column)
    if _has_value(address):
        return str(address)
    parts = [
        _value(row, house_number_column),
        _value(row, pre_directional_column),
        _value(row, street_name_column),
        _value(row, street_suffix_column),
        _value(row, post_directional_column),
    ]
    return " ".join(str(part).strip() for part in parts if _has_value(part))


def _prepare_point_table(
    records: pd.DataFrame,
    *,
    source: str,
    source_type: str,
    priority: int | None,
    id_column: str | None,
    address_column: str | None,
    house_number_column: str | None,
    pre_directional_column: str | None,
    street_name_column: str | None,
    street_suffix_column: str | None,
    post_directional_column: str | None,
    city_column: str | None,
    state_column: str | None,
    zip_column: str | None,
    city: str | None,
    state: str | None,
    zip_code: str | None,
    geometry_column: str | None,
    longitude_column: str | None,
    latitude_column: str | None,
    input_crs: str | CRS | None,
    output_crs: str | CRS,
) -> pd.DataFrame:
    frame = records.copy()
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("records must be a pandas or GeoPandas table")
    source_type = normalize_source_type(source_type)
    source_priority = (
        source_priority_for(source_type) if priority is None else int(priority)
    )
    address_column = _column(frame, address_column, _ADDRESS_COLUMNS, label="address")
    house_number_column = _column(
        frame, house_number_column, _HOUSE_NUMBER_COLUMNS, label="house number"
    )
    pre_directional_column = _column(
        frame, pre_directional_column, _PRE_DIRECTIONAL_COLUMNS, label="pre-directional"
    )
    street_name_column = _column(frame, street_name_column, _STREET_COLUMNS, label="street name")
    street_suffix_column = _column(
        frame, street_suffix_column, _SUFFIX_COLUMNS, label="street suffix"
    )
    post_directional_column = _column(
        frame, post_directional_column, _POST_DIRECTIONAL_COLUMNS, label="post-directional"
    )
    city_column = _column(frame, city_column, _CITY_COLUMNS, label="city")
    state_column = _column(frame, state_column, _STATE_COLUMNS, label="state")
    zip_column = _column(frame, zip_column, _ZIP_COLUMNS, label="ZIP")
    id_column = _column(frame, id_column, _ID_COLUMNS, label="address ID")
    points = _points_wgs84(
        frame,
        geometry_column=geometry_column,
        longitude_column=longitude_column,
        latitude_column=latitude_column,
        input_crs=input_crs,
        output_crs=output_crs,
    )
    output: list[dict[str, Any]] = []
    for row_number, (index, row) in enumerate(frame.iterrows()):
        raw_address = _component_address(
            row,
            address_column=address_column,
            house_number_column=house_number_column,
            pre_directional_column=pre_directional_column,
            street_name_column=street_name_column,
            street_suffix_column=street_suffix_column,
            post_directional_column=post_directional_column,
        )
        parsed = parse_address(
            raw_address,
            city=city if city is not None else _value(row, city_column),
            state=state if state is not None else _value(row, state_column),
            zip_code=zip_code if zip_code is not None else _value(row, zip_column),
        )
        if parsed.house_number is None or not parsed.street_name:
            continue
        record_id = _value(row, id_column, index)
        if not _has_value(record_id):
            record_id = row_number
        record_id = str(record_id)
        point = points.loc[index]
        address_id = f"{source}:{record_id}:{row_number}"
        output.append(
            {
                "address_id": address_id,
                "range_id": record_id,
                "house_number": parsed.house_number,
                "parity": "even" if parsed.house_number % 2 == 0 else "odd",
                "side": "P",
                "pre_directional": parsed.pre_directional,
                "street_name": parsed.street_name,
                "street_suffix": parsed.street_suffix,
                "post_directional": parsed.post_directional,
                "street_norm": parsed.street_norm,
                "street_block": parsed.street_block,
                "city": normalize_text(parsed.city),
                "city_norm": parsed.city_norm,
                "state": normalize_state(parsed.state),
                "state_norm": parsed.state_norm,
                "zip5": normalize_zip(parsed.zip5),
                "latitude": float(point.y),
                "longitude": float(point.x),
                "geometry_wkt": point.wkt,
                "interpolation_crs": None,
                "source": source,
                "source_type": source_type,
                "source_priority": source_priority,
                "source_record_id": record_id,
            }
        )
    if not output:
        raise ValueError("No valid address rows were found in the reference table")
    return pd.DataFrame(output, columns=ADDRESS_COLUMNS)


def prepare_addresses(
    records: pd.DataFrame,
    *,
    address_column: str | None = None,
    house_number_column: str | None = None,
    pre_directional_column: str | None = None,
    street_name_column: str | None = None,
    street_suffix_column: str | None = None,
    post_directional_column: str | None = None,
    city_column: str | None = None,
    state_column: str | None = None,
    zip_column: str | None = None,
    city: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
    geometry_column: str | None = None,
    longitude_column: str | None = None,
    latitude_column: str | None = None,
    input_crs: str | CRS | None = None,
    output_crs: str | CRS = "EPSG:4326",
    source: str = "individual",
    source_type: str = "individual",
    priority: int | None = None,
    id_column: str | None = None,
) -> pd.DataFrame:
    """Prepare individual address points into the common reference schema.

    The table may contain a full address column or separate house-number and
    street components. Coordinates can be a point geometry/WKT or longitude
    and latitude columns. Polygon and line geometries are reduced to an
    interior representative point, although point geometries are preferred.
    """

    return _prepare_point_table(
        records,
        source=source,
        source_type=source_type,
        priority=priority,
        id_column=id_column,
        address_column=address_column,
        house_number_column=house_number_column,
        pre_directional_column=pre_directional_column,
        street_name_column=street_name_column,
        street_suffix_column=street_suffix_column,
        post_directional_column=post_directional_column,
        city_column=city_column,
        state_column=state_column,
        zip_column=zip_column,
        city=city,
        state=state,
        zip_code=zip_code,
        geometry_column=geometry_column,
        longitude_column=longitude_column,
        latitude_column=latitude_column,
        input_crs=input_crs,
        output_crs=output_crs,
    )


def prepare_parcels(
    records: pd.DataFrame,
    *,
    address_column: str | None = None,
    house_number_column: str | None = None,
    pre_directional_column: str | None = None,
    street_name_column: str | None = None,
    street_suffix_column: str | None = None,
    post_directional_column: str | None = None,
    city_column: str | None = None,
    state_column: str | None = None,
    zip_column: str | None = None,
    city: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
    geometry_column: str | None = None,
    longitude_column: str | None = None,
    latitude_column: str | None = None,
    input_crs: str | CRS | None = None,
    output_crs: str | CRS = "EPSG:4326",
    source: str = "parcel",
    source_type: str = "parcel",
    priority: int | None = None,
    parcel_id_column: str | None = None,
) -> pd.DataFrame:
    """Prepare parcel polygons or parcel address points into one row per parcel.

    Polygon features use ``representative_point`` so the resulting point stays
    inside the parcel. A parcel identifier is retained as ``source_record_id``
    for audit joins after geocoding.
    """

    id_column = parcel_id_column or None
    frame = records
    if id_column is None:
        id_column = _column(frame, None, _PARCEL_ID_COLUMNS, label="parcel ID")
    return _prepare_point_table(
        frame,
        source=source,
        source_type=source_type,
        priority=priority,
        id_column=id_column,
        address_column=address_column,
        house_number_column=house_number_column,
        pre_directional_column=pre_directional_column,
        street_name_column=street_name_column,
        street_suffix_column=street_suffix_column,
        post_directional_column=post_directional_column,
        city_column=city_column,
        state_column=state_column,
        zip_column=zip_column,
        city=city,
        state=state,
        zip_code=zip_code,
        geometry_column=geometry_column,
        longitude_column=longitude_column,
        latitude_column=latitude_column,
        input_crs=input_crs,
        output_crs=output_crs,
    )


def _is_prepared_table(frame: pd.DataFrame) -> bool:
    return {
        "address_id",
        "house_number",
        "street_norm",
        "street_block",
        "latitude",
        "longitude",
    }.issubset({str(column).lower() for column in frame.columns})


def _reuse_prepared_table(
    frame: pd.DataFrame,
    *,
    source_type: str,
    preference: tuple[str, ...],
) -> pd.DataFrame:
    """Re-label a table already produced by one of the preparation helpers."""

    result = frame.copy()
    source_type = normalize_source_type(source_type)
    if "source" not in result.columns:
        result["source"] = source_type
    result["source_type"] = source_type
    result["source_priority"] = source_priority_for(source_type, preference)
    if "source_record_id" not in result.columns:
        result["source_record_id"] = result["address_id"].astype(str)
    if "range_id" not in result.columns:
        result["range_id"] = result["source_record_id"].astype(str)
    for column in ADDRESS_COLUMNS:
        if column not in result.columns:
            result[column] = None
    return result[ADDRESS_COLUMNS]


def prepare_combined(
    *,
    addresses: pd.DataFrame | None = None,
    parcels: pd.DataFrame | None = None,
    ranges: gpd.GeoDataFrame | pd.DataFrame | None = None,
    preference: Sequence[str] = DEFAULT_SOURCE_PREFERENCE,
    address_options: Mapping[str, Any] | None = None,
    parcel_options: Mapping[str, Any] | None = None,
    range_options: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Build one canonical prepared dataset from local reference tables.

    ``addresses`` are individual address points, ``parcels`` are parcel
    polygons/points with situs addresses, and ``ranges`` are local TIGER/Line
    ranges. All supplied sources are concatenated into one table. The
    preference order is persisted as ``source_priority`` and is used by
    :class:`~geotiger.CombinedGeocoder` to break equal-score matches.
    """

    preference = tuple(normalize_source_type(name) for name in preference)
    prepared: list[pd.DataFrame] = []
    if addresses is not None:
        if _is_prepared_table(addresses):
            prepared.append(
                _reuse_prepared_table(
                    addresses,
                    source_type="individual",
                    preference=preference,
                )
            )
        else:
            options = dict(address_options or {})
            options.update(
                source_type="individual",
                priority=source_priority_for("individual", preference),
            )
            prepared.append(prepare_addresses(addresses, **options))
    if parcels is not None:
        if _is_prepared_table(parcels):
            prepared.append(
                _reuse_prepared_table(
                    parcels,
                    source_type="parcel",
                    preference=preference,
                )
            )
        else:
            options = dict(parcel_options or {})
            options.update(
                source_type="parcel",
                priority=source_priority_for("parcel", preference),
            )
            prepared.append(prepare_parcels(parcels, **options))
    if ranges is not None:
        if _is_prepared_table(ranges):
            prepared.append(
                _reuse_prepared_table(
                    ranges,
                    source_type="tiger",
                    preference=preference,
                )
            )
        else:
            options = dict(range_options or {})
            options.update(
                source_type="tiger",
                source_priority=source_priority_for("tiger", preference),
            )
            prepared.append(prepare_ranges(ranges, **options))
    if not prepared:
        raise ValueError("Supply at least one of addresses, parcels, or ranges")
    combined = pd.concat(prepared, ignore_index=True)
    return combined[ADDRESS_COLUMNS].sort_values(
        ["source_priority", "source_type", "address_id"], kind="mergesort"
    ).reset_index(drop=True)


def save_prepared(prepared: pd.DataFrame, path: str | Path) -> Path:
    """Save a canonical prepared dataset as Parquet or CSV."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() in {".parquet", ".geoparquet"}:
        prepared.to_parquet(output, index=False)
    elif output.suffix.lower() == ".csv":
        prepared.to_csv(output, index=False)
    else:
        raise ValueError("Prepared output must end in .parquet or .csv")
    return output
