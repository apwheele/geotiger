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
    "interpolation_crs",
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
                interpolation_crs VARCHAR,
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
        self.connection.execute(
            "ALTER TABLE addresses ADD COLUMN IF NOT EXISTS interpolation_crs VARCHAR"
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
        exact_street_first: bool = True,
        exact_house_number_first: bool = True,
        exact_street: bool = False,
        street_fallback: bool = True,
    ) -> pd.DataFrame:
        """Return blocked candidates for parsed input records.

        DuckDB executes this join with the store's configured thread count. No
        input data is sent outside the process.
        """

        self.create()
        if exact_street_first and not exact_street:
            eligible = inputs.loc[inputs["street_norm"].fillna("").ne("")]
            exact = self.candidate_query(
                eligible,
                house_number_tolerance=house_number_tolerance,
                street_blocking=street_blocking,
                strict_locality=strict_locality,
                exact_street_first=False,
                exact_house_number_first=exact_house_number_first,
                exact_street=True,
            )
            found = set(exact["input_id"].tolist()) if len(exact) else set()
            remaining = inputs.loc[~inputs["input_id"].isin(found)]
            if not len(remaining) or not street_fallback:
                return exact
            fallback = self.candidate_query(
                remaining,
                house_number_tolerance=house_number_tolerance,
                street_blocking=street_blocking,
                strict_locality=strict_locality,
                exact_street_first=False,
                exact_house_number_first=exact_house_number_first,
                exact_street=False,
                street_fallback=street_fallback,
            )
            return pd.concat([exact, fallback], ignore_index=True)
        if exact_house_number_first and house_number_tolerance > 0:
            exact = self.candidate_query(
                inputs,
                house_number_tolerance=0,
                street_blocking=street_blocking,
                strict_locality=strict_locality,
                exact_street_first=False,
                exact_house_number_first=False,
                exact_street=exact_street,
            )
            found = set(exact["input_id"].tolist()) if len(exact) else set()
            remaining = inputs.loc[~inputs["input_id"].isin(found)]
            if not len(remaining):
                return exact
            fallback = self.candidate_query(
                remaining,
                house_number_tolerance=house_number_tolerance,
                street_blocking=street_blocking,
                strict_locality=strict_locality,
                exact_street_first=False,
                exact_house_number_first=False,
                exact_street=exact_street,
            )
            return pd.concat([exact, fallback], ignore_index=True)
        required = [
            "input_id",
            "house_number",
            "street_norm",
            "street_block",
            "state_norm",
            "city_norm",
            "zip5",
        ]
        missing = [column for column in required if column not in inputs.columns]
        if missing:
            raise ValueError(f"Parsed input is missing columns: {', '.join(missing)}")
        frame = inputs[required].copy()
        frame["house_number"] = frame["house_number"].astype("Int64")
        for column in ("street_block", "state_norm", "city_norm", "zip5"):
            frame[column] = frame[column].fillna("").astype(str)
        view = f"_geotiger_inputs_{uuid.uuid4().hex}"
        self.connection.register(view, frame)
        conditions = []
        for column in ("state_norm",):
            if frame[column].ne("").all():
                conditions.append(f"a.{column} = i.{column}")
            else:
                conditions.append(f"(i.{column} = '' OR a.{column} = i.{column})")
        if exact_street:
            conditions.append("a.street_norm = i.street_norm")
        elif street_blocking:
            if frame["street_block"].ne("").all():
                conditions.append("a.street_block = i.street_block")
            else:
                conditions.append("(i.street_block = '' OR a.street_block = i.street_block)")
        if strict_locality:
            for column in ("city_norm", "zip5"):
                if frame[column].ne("").all():
                    conditions.append(f"a.{column} = i.{column}")
                else:
                    conditions.append(f"(i.{column} = '' OR a.{column} = i.{column})")
        if frame["house_number"].notna().all():
            conditions.append(
                f"abs(a.house_number - i.house_number) <= {int(house_number_tolerance)}"
            )
        else:
            conditions.append(
                f"(i.house_number IS NULL OR abs(a.house_number - i.house_number) <= "
                f"{int(house_number_tolerance)})"
            )
        join_where = " AND ".join(conditions) or "TRUE"
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
              ON {join_where}
        """
        try:
            return self.connection.execute(query).df()
        finally:
            self.connection.unregister(view)
