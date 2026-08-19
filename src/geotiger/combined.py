"""A single local geocoder over address-point, parcel, and TIGER references."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from .geocoder import Geocoder, GeocoderConfig, GeocodeResult
from .prepare import prepare_combined
from .schema import DEFAULT_SOURCE_PREFERENCE, source_priority
from .store import GeoTIGERStore


class CombinedGeocoder:
    """Prepare and geocode several local reference types through one store.

    Individual address points are preferred over parcel points, and parcel
    points over TIGER interpolation by default when candidate scores tie. The
    preference is persisted in the store and never requires a second geocoder
    or an online lookup.
    """

    def __init__(
        self,
        store: GeoTIGERStore,
        *,
        config: GeocoderConfig | None = None,
        source_preference: Sequence[str] = DEFAULT_SOURCE_PREFERENCE,
    ):
        self.store = store
        self.config = config or GeocoderConfig()
        self.source_preference = tuple(str(name) for name in source_preference)
        self._geocoder = Geocoder(store, config=self.config)

    @classmethod
    def from_tables(
        cls,
        store: GeoTIGERStore,
        *,
        addresses: pd.DataFrame | None = None,
        parcels: pd.DataFrame | None = None,
        ranges: pd.DataFrame | None = None,
        source_preference: Sequence[str] = DEFAULT_SOURCE_PREFERENCE,
        config: GeocoderConfig | None = None,
        address_options: Mapping[str, Any] | None = None,
        parcel_options: Mapping[str, Any] | None = None,
        range_options: Mapping[str, Any] | None = None,
        replace: bool = True,
    ) -> CombinedGeocoder:
        """Create a combined geocoder and ingest one canonical reference table."""

        combined = cls(
            store,
            config=config,
            source_preference=source_preference,
        )
        combined.prepare_and_ingest(
            addresses=addresses,
            parcels=parcels,
            ranges=ranges,
            address_options=address_options,
            parcel_options=parcel_options,
            range_options=range_options,
            replace=replace,
        )
        return combined

    def prepare(
        self,
        *,
        addresses: pd.DataFrame | None = None,
        parcels: pd.DataFrame | None = None,
        ranges: pd.DataFrame | None = None,
        address_options: Mapping[str, Any] | None = None,
        parcel_options: Mapping[str, Any] | None = None,
        range_options: Mapping[str, Any] | None = None,
    ) -> pd.DataFrame:
        """Return one canonical prepared dataframe without writing it."""

        return prepare_combined(
            addresses=addresses,
            parcels=parcels,
            ranges=ranges,
            preference=self.source_preference,
            address_options=address_options,
            parcel_options=parcel_options,
            range_options=range_options,
        )

    def ingest(self, prepared: pd.DataFrame, *, replace: bool = True) -> int:
        """Ingest one canonical prepared dataframe into the local store."""

        frame = prepared.copy()
        if "source_type" in frame.columns:
            frame["source_priority"] = frame["source_type"].map(
                lambda value: source_priority(value, tuple(self.source_preference))
            )
        inserted = self.store.ingest_candidates(frame, replace=replace)
        self.store.set_metadata(
            source_preference=", ".join(self.source_preference),
            prepared_candidates=inserted,
        )
        return inserted

    def prepare_and_ingest(
        self,
        *,
        addresses: pd.DataFrame | None = None,
        parcels: pd.DataFrame | None = None,
        ranges: pd.DataFrame | None = None,
        address_options: Mapping[str, Any] | None = None,
        parcel_options: Mapping[str, Any] | None = None,
        range_options: Mapping[str, Any] | None = None,
        replace: bool = True,
    ) -> pd.DataFrame:
        """Prepare all supplied local sources and ingest the single result."""

        prepared = self.prepare(
            addresses=addresses,
            parcels=parcels,
            ranges=ranges,
            address_options=address_options,
            parcel_options=parcel_options,
            range_options=range_options,
        )
        self.ingest(prepared, replace=replace)
        return prepared

    def geocode(self, records: pd.DataFrame, **kwargs: Any) -> GeocodeResult:
        """Geocode input records against the combined local reference table."""

        return self._geocoder.geocode(records, **kwargs)

    def cache_result(self, result: GeocodeResult, *, only_auto: bool = True) -> int:
        """Persist successful matches for exact reuse on future local runs."""

        return self._geocoder.cache_result(result, only_auto=only_auto)

    def add_lookup_table(
        self,
        mappings: pd.DataFrame,
        *,
        alias_column: str = "alias",
        target_address_id_column: str | None = None,
        target_address_column: str = "actual_address",
        target_city_column: str | None = "city",
        target_state_column: str | None = "state",
        target_zip_column: str | None = "zip",
        lookup_city_column: str | None = "city",
        lookup_state_column: str | None = "state",
        lookup_zip_column: str | None = "zip",
        lookup_id_column: str | None = None,
        confidence_column: str | None = None,
        source: str = "lookup",
        only_auto: bool = True,
        replace: bool = False,
    ) -> int:
        """Add aliases such as McDonalds First St to local address IDs.

        Supply target_address_id_column when the mapping table already contains
        prepared IDs. Otherwise target_address_column is resolved locally once
        and the successful target IDs are stored.
        """

        frame = mappings.copy().reset_index(drop=True)
        by_upper = {str(column).upper(): column for column in frame.columns}

        def actual(requested: str | None, label: str, *, required: bool = True) -> str | None:
            if requested is None:
                return None
            column = by_upper.get(str(requested).upper())
            if column is None and required:
                raise ValueError(f"Could not find {label} column {requested!r}")
            return column

        alias_col = actual(alias_column, "lookup alias")
        target_id_col = actual(target_address_id_column, "target address ID", required=False)
        if target_id_col is not None:
            return self.store.ingest_lookup(
                frame,
                alias_column=alias_col,
                address_id_column=target_id_col,
                city_column=lookup_city_column,
                state_column=lookup_state_column,
                zip_column=lookup_zip_column,
                lookup_id_column=lookup_id_column,
                confidence_column=confidence_column,
                source=source,
                replace=replace,
            )

        target_col = actual(target_address_column, "target address")
        target_city_col = actual(target_city_column, "target city", required=False)
        target_state_col = actual(target_state_column, "target state", required=False)
        target_zip_col = actual(target_zip_column, "target ZIP", required=False)
        target = pd.DataFrame(
            {
                "address": frame[target_col],
                "city": frame[target_city_col] if target_city_col else "",
                "state": frame[target_state_col] if target_state_col else "",
                "zip": frame[target_zip_col] if target_zip_col else "",
            }
        )
        resolution = self.geocode(target)
        valid = resolution.matches["matched_address_id"].notna()
        if only_auto:
            valid &= resolution.matches["auto_assigned"].fillna(False).astype(bool)
        else:
            valid &= resolution.matches["match_status"].isin(["matched", "review"])
        resolved = frame.loc[valid].copy()
        if not len(resolved):
            return 0
        resolved["address_id"] = resolution.matches.loc[valid, "matched_address_id"].tolist()
        return self.store.ingest_lookup(
            resolved,
            alias_column=alias_col,
            address_id_column="address_id",
            city_column=lookup_city_column,
            state_column=lookup_state_column,
            zip_column=lookup_zip_column,
            lookup_id_column=lookup_id_column,
            confidence_column=confidence_column,
            source=source,
            replace=replace,
        )
