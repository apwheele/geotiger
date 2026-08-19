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
