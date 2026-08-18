"""DuckDB persistence and blocking queries for the prepared reference table."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

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
    "city",
    "city_norm",
    "state",
    "state_norm",
    "zip5",
    "latitude",
    "longitude",
    "geometry_wkt",
    "source",
]


class GeoTIGERStore:
    """A reusable local DuckDB store for expanded address candidates."""

    def __init__(self, path: str | os.PathLike[str] = ":memory:", *, threads: int | None = None):
        self.path = str(path)
        self.threads = max(1, threads or (os.cpu_count() or 1))
        self._connection: duckdb.DuckDBPyConnection | None = None

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        if self._connection is None:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._connection = duckdb.connect(self.path)
            self._connection.execute(f"PRAGMA threads={self.threads}")
        return self._connection

    def create(self) -> GeoTIGERStore:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS addresses (
                address_id VARCHAR,
                range_id VARCHAR,
                house_number INTEGER,
                parity VARCHAR,
                side VARCHAR,
                pre_directional VARCHAR,
                street_name VARCHAR,
                street_suffix VARCHAR,
                post_directional VARCHAR,
                street_norm VARCHAR,
                street_block VARCHAR,
                city VARCHAR,
                city_norm VARCHAR,
                state VARCHAR,
                state_norm VARCHAR,
                zip5 VARCHAR,
                latitude DOUBLE,
                longitude DOUBLE,
                geometry_wkt VARCHAR,
                source VARCHAR
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key VARCHAR PRIMARY KEY,
                value VARCHAR
            )
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS addresses_state_zip ON addresses(state_norm, zip5)",
            "CREATE INDEX IF NOT EXISTS addresses_state_city ON addresses(state_norm, city_norm)",
            "CREATE INDEX IF NOT EXISTS addresses_house_number ON addresses(house_number)",
            "CREATE INDEX IF NOT EXISTS addresses_street_block ON addresses(street_block)",
        ):
            try:
                self.connection.execute(statement)
            except duckdb.Error:
                # Index support varies slightly between DuckDB releases. The
                # blocking query remains correct without these advisory indexes.
                pass
        return self

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> GeoTIGERStore:
        return self.create()

    def __exit__(self, *_: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def set_metadata(self, **values: Any) -> None:
        self.create()
        for key, value in values.items():
            self.connection.execute(
                "INSERT OR REPLACE INTO metadata VALUES (?, ?)", [str(key), str(value)]
            )

    def metadata(self) -> dict[str, str]:
        self.create()
        return dict(self.connection.execute("SELECT key, value FROM metadata").fetchall())

    def count(self) -> int:
        self.create()
        return int(self.connection.execute("SELECT count(*) FROM addresses").fetchone()[0])

    def ingest_candidates(
        self,
        candidates: pd.DataFrame,
        *,
        replace: bool = False,
        source: str | None = None,
    ) -> int:
        """Persist expanded candidates, returning the number of inserted rows."""

        self.create()
        frame = candidates.copy()
        if source is not None:
            frame["source"] = source
        for column in ADDRESS_COLUMNS:
            if column not in frame.columns:
                frame[column] = None
        frame = frame[ADDRESS_COLUMNS]
        if replace:
            self.connection.execute("DELETE FROM addresses")
        view = f"_geotiger_load_{uuid.uuid4().hex}"
        self.connection.register(view, frame)
        try:
            self.connection.execute(
                f"INSERT INTO addresses SELECT {', '.join(ADDRESS_COLUMNS)} FROM {view}"
            )
        finally:
            self.connection.unregister(view)
        return len(frame)

    def candidate_query(
        self,
        inputs: pd.DataFrame,
        *,
        house_number_tolerance: int = 25,
        street_blocking: bool = True,
        strict_locality: bool = True,
    ) -> pd.DataFrame:
        """Return blocked candidates for parsed input records.

        DuckDB executes this join with the store's configured thread count. No
        input data is sent outside the process.
        """

        self.create()
        required = ["input_id", "house_number", "street_block", "state_norm", "city_norm", "zip5"]
        missing = [column for column in required if column not in inputs.columns]
        if missing:
            raise ValueError(f"Parsed input is missing columns: {', '.join(missing)}")
        frame = inputs[required].copy()
        frame["house_number"] = frame["house_number"].astype("Int64")
        for column in ("street_block", "state_norm", "city_norm", "zip5"):
            frame[column] = frame[column].fillna("").astype(str)
        view = f"_geotiger_inputs_{uuid.uuid4().hex}"
        self.connection.register(view, frame)
        local_where = (
            "(i.state_norm = '' OR a.state_norm = i.state_norm) "
            "AND (i.city_norm = '' OR a.city_norm = i.city_norm) "
            "AND (i.zip5 = '' OR a.zip5 = i.zip5)"
        )
        if not strict_locality:
            local_where = "TRUE"
        street_where = (
            "TRUE"
            if not street_blocking
            else "(i.street_block = '' OR a.street_block = i.street_block)"
        )
        query = f"""
            SELECT
                i.input_id,
                a.address_id AS candidate_address_id,
                a.range_id,
                a.house_number AS candidate_house_number,
                a.parity AS candidate_parity,
                a.side AS candidate_side,
                a.pre_directional AS candidate_pre_directional,
                a.street_name AS candidate_street_name,
                a.street_suffix AS candidate_street_suffix,
                a.post_directional AS candidate_post_directional,
                a.street_norm AS candidate_street_norm,
                a.city AS candidate_city,
                a.city_norm AS candidate_city_norm,
                a.state AS candidate_state,
                a.state_norm AS candidate_state_norm,
                a.zip5 AS candidate_zip5,
                a.latitude AS candidate_latitude,
                a.longitude AS candidate_longitude,
                a.geometry_wkt AS candidate_geometry_wkt,
                a.source AS candidate_source
            FROM {view} i
            INNER JOIN addresses a
              ON {local_where}
             AND {street_where}
             AND (i.house_number IS NULL OR
                  abs(a.house_number - i.house_number) <= {int(house_number_tolerance)})
        """
        try:
            return self.connection.execute(query).df()
        finally:
            self.connection.unregister(view)
