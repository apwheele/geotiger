"""DuckDB persistence and blocking queries for the prepared reference table."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from .normalize import normalize_state, normalize_text, normalize_zip
from .schema import (
    ADDRESS_COLUMNS,
    address_cache_key,
    normalize_source_type,
    source_priority,
)


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
                source VARCHAR,
                source_type VARCHAR,
                source_priority INTEGER,
                source_record_id VARCHAR,
                is_intersection BOOLEAN,
                intersection_key VARCHAR,
                intersection_street_norm VARCHAR,
                intersection_street_block VARCHAR
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
            """
            CREATE TABLE IF NOT EXISTS address_lookup (
                lookup_id VARCHAR,
                lookup_text VARCHAR,
                lookup_norm VARCHAR,
                city_norm VARCHAR,
                state_norm VARCHAR,
                zip5 VARCHAR,
                address_id VARCHAR,
                source VARCHAR,
                confidence DOUBLE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS history_cache (
                cache_key VARCHAR PRIMARY KEY,
                raw_address VARCHAR,
                house_number INTEGER,
                street_norm VARCHAR,
                city_norm VARCHAR,
                state_norm VARCHAR,
                zip5 VARCHAR,
                address_id VARCHAR,
                source VARCHAR,
                match_status VARCHAR,
                score DOUBLE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(
            "ALTER TABLE addresses ADD COLUMN IF NOT EXISTS interpolation_crs VARCHAR"
        )
        self.connection.execute(
            "ALTER TABLE addresses ADD COLUMN IF NOT EXISTS source_type VARCHAR"
        )
        self.connection.execute(
            "ALTER TABLE addresses ADD COLUMN IF NOT EXISTS source_priority INTEGER"
        )
        self.connection.execute(
            "ALTER TABLE addresses ADD COLUMN IF NOT EXISTS source_record_id VARCHAR"
        )
        for column, definition in (
            ("is_intersection", "BOOLEAN"),
            ("intersection_key", "VARCHAR"),
            ("intersection_street_norm", "VARCHAR"),
            ("intersection_street_block", "VARCHAR"),
        ):
            self.connection.execute(
                f"ALTER TABLE addresses ADD COLUMN IF NOT EXISTS {column} {definition}"
            )
        self.connection.execute(
            """
            UPDATE addresses
            SET source_type = CASE
                WHEN lower(coalesce(source, '')) LIKE '%tiger%' THEN 'tiger'
                ELSE 'custom'
            END
            WHERE source_type IS NULL
            """
        )
        self.connection.execute(
            """
            UPDATE addresses
            SET source_priority = CASE
                WHEN source_type = 'tiger' THEN 20
                ELSE 100
            END
            WHERE source_priority IS NULL
            """
        )
        self.connection.execute(
            "UPDATE addresses SET source_record_id = address_id WHERE source_record_id IS NULL"
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS addresses_state_zip ON addresses(state_norm, zip5)",
            "CREATE INDEX IF NOT EXISTS addresses_state_city ON addresses(state_norm, city_norm)",
            "CREATE INDEX IF NOT EXISTS addresses_house_number ON addresses(house_number)",
            "CREATE INDEX IF NOT EXISTS addresses_street_block ON addresses(street_block)",
            "CREATE INDEX IF NOT EXISTS addresses_intersection_key ON "
            "addresses(intersection_key)",
            "CREATE INDEX IF NOT EXISTS lookup_norm_locality ON "
            "address_lookup(lookup_norm, state_norm, city_norm, zip5)",
            "CREATE INDEX IF NOT EXISTS history_cache_key ON history_cache(cache_key)",
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

    def intersection_count(self) -> int:
        """Return the number of prepared intersection points."""

        self.create()
        return int(
            self.connection.execute(
                "SELECT count(*) FROM addresses WHERE coalesce(is_intersection, FALSE)"
            ).fetchone()[0]
        )

    def lookup_count(self) -> int:
        """Return the number of explicit local alias mappings."""

        self.create()
        return int(self.connection.execute("SELECT count(*) FROM address_lookup").fetchone()[0])

    def history_cache_count(self) -> int:
        """Return the number of historical normalized-address mappings."""

        self.create()
        return int(self.connection.execute("SELECT count(*) FROM history_cache").fetchone()[0])

    def ingest_lookup(
        self,
        mappings: pd.DataFrame,
        *,
        alias_column: str = "alias",
        address_id_column: str = "address_id",
        city_column: str | None = "city",
        state_column: str | None = "state",
        zip_column: str | None = "zip",
        lookup_id_column: str | None = None,
        confidence_column: str | None = None,
        source: str = "lookup",
        replace: bool = False,
    ) -> int:
        """Persist aliases that resolve directly to prepared address IDs."""

        self.create()
        frame = mappings.copy()
        by_upper = {str(column).upper(): column for column in frame.columns}

        def actual(
            requested: str | None,
            label: str,
            *,
            required: bool = True,
        ) -> str | None:
            if requested is None:
                return None
            column = by_upper.get(str(requested).upper())
            if column is None and required:
                raise ValueError(f"Could not find {label} column {requested!r}")
            return column

        alias_col = actual(alias_column, "lookup alias")
        target_col = actual(address_id_column, "target address ID")
        city_col = actual(city_column, "city", required=False)
        state_col = actual(state_column, "state", required=False)
        zip_col = actual(zip_column, "ZIP", required=False)
        id_col = actual(lookup_id_column, "lookup ID")
        confidence_col = actual(confidence_column, "confidence")
        rows: list[dict[str, Any]] = []
        for row_number, (_, row) in enumerate(frame.iterrows()):
            alias = normalize_text(row[alias_col])
            target = str(row[target_col]).strip() if not pd.isna(row[target_col]) else ""
            if not alias or not target:
                continue
            lookup_id = (
                str(row[id_col]).strip()
                if id_col is not None and not pd.isna(row[id_col])
                else f"{source}:{row_number}"
            )
            confidence = 100.0
            if confidence_col is not None and not pd.isna(row[confidence_col]):
                confidence = float(row[confidence_col])
            rows.append(
                {
                    "lookup_id": lookup_id,
                    "lookup_text": str(row[alias_col]),
                    "lookup_norm": alias,
                    "city_norm": normalize_text(row[city_col]) if city_col else "",
                    "state_norm": normalize_state(row[state_col]) if state_col else "",
                    "zip5": normalize_zip(row[zip_col]) if zip_col else "",
                    "address_id": target,
                    "source": source,
                    "confidence": confidence,
                }
            )
        if not rows:
            return 0
        prepared = pd.DataFrame(rows)
        view = f"_geotiger_lookup_{uuid.uuid4().hex}"
        self.connection.register(view, prepared)
        try:
            missing = int(
                self.connection.execute(
                    f"""
                    SELECT count(*)
                    FROM {view} l
                    LEFT JOIN addresses a ON a.address_id = l.address_id
                    WHERE a.address_id IS NULL
                    """
                ).fetchone()[0]
            )
            if missing:
                raise ValueError(f"{missing:,} lookup rows reference unknown address IDs")
            if replace:
                self.connection.execute("DELETE FROM address_lookup")
            self.connection.execute(
                f"""
                INSERT INTO address_lookup (
                    lookup_id, lookup_text, lookup_norm, city_norm, state_norm,
                    zip5, address_id, source, confidence
                )
                SELECT lookup_id, lookup_text, lookup_norm, city_norm, state_norm,
                       zip5, address_id, source, confidence
                FROM {view}
                """
            )
        finally:
            self.connection.unregister(view)
        return len(prepared)

    def cache_matches(
        self,
        matches: Any,
        *,
        only_auto: bool = True,
    ) -> int:
        """Upsert successful geocodes into the historical address cache."""

        self.create()
        frame = matches.matches.copy() if hasattr(matches, "matches") else matches.copy()
        required = {
            "matched_address_id",
            "raw_address",
            "house_number",
            "street_norm",
            "city_norm",
            "state_norm",
            "zip5",
        }
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise ValueError(f"Match table is missing cache fields: {', '.join(missing)}")
        valid = frame["matched_address_id"].notna() & frame["matched_address_id"].astype(str).ne("")
        if only_auto:
            if "auto_assigned" in frame.columns:
                valid &= frame["auto_assigned"].fillna(False).astype(bool)
            else:
                valid &= frame["match_status"].eq("matched")
        else:
            valid &= frame["match_status"].isin(["matched", "review"])
        frame = frame.loc[valid].copy()
        if not len(frame):
            return 0
        rows: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            key = address_cache_key(row)
            if not key.replace("\x1f", ""):
                continue
            rows.append(
                {
                    "cache_key": key,
                    "raw_address": str(row["raw_address"]),
                    "house_number": row["house_number"],
                    "street_norm": row["street_norm"],
                    "city_norm": row["city_norm"],
                    "state_norm": row["state_norm"],
                    "zip5": row["zip5"],
                    "address_id": str(row["matched_address_id"]),
                    "source": str(row.get("matched_source") or "history"),
                    "match_status": str(row.get("match_status") or "matched"),
                    "score": float(row.get("score") or 0.0),
                }
            )
        if not rows:
            return 0
        prepared = pd.DataFrame(rows).drop_duplicates("cache_key", keep="last")
        view = f"_geotiger_history_{uuid.uuid4().hex}"
        self.connection.register(view, prepared)
        try:
            self.connection.execute(
                f"""
                INSERT OR REPLACE INTO history_cache (
                    cache_key, raw_address, house_number, street_norm, city_norm,
                    state_norm, zip5, address_id, source, match_status, score
                )
                SELECT cache_key, raw_address, house_number, street_norm, city_norm,
                       state_norm, zip5, address_id, source, match_status, score
                FROM {view}
                """
            )
        finally:
            self.connection.unregister(view)
        return len(prepared)

    def lookup_query(
        self,
        inputs: pd.DataFrame,
        *,
        strict_locality: bool = True,
    ) -> pd.DataFrame:
        """Resolve exact normalized aliases to prepared address candidates."""

        self.create()
        required = ["input_id", "lookup_norm", "state_norm", "city_norm", "zip5"]
        missing = [column for column in required if column not in inputs.columns]
        if missing:
            raise ValueError(f"Lookup input is missing columns: {', '.join(missing)}")
        if not len(inputs):
            return pd.DataFrame()
        frame = inputs[required].copy()
        for column in ("lookup_norm", "state_norm", "city_norm", "zip5"):
            frame[column] = frame[column].fillna("").astype(str)
        view = f"_geotiger_lookup_inputs_{uuid.uuid4().hex}"
        self.connection.register(view, frame)
        locality = []
        if strict_locality:
            for column in ("state_norm", "city_norm", "zip5"):
                locality.append(
                    f"(i.{column} = '' OR l.{column} = '' OR l.{column} = i.{column})"
                )
        where = " AND ".join(locality) or "TRUE"
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
                a.source AS candidate_source,
                a.source_type AS candidate_source_type,
                a.source_priority AS candidate_source_priority,
                a.source_record_id AS candidate_source_record_id,
                coalesce(a.is_intersection, FALSE) AS candidate_is_intersection,
                a.intersection_key AS candidate_intersection_key,
                a.intersection_street_norm AS candidate_intersection_street_norm,
                l.lookup_id AS candidate_lookup_id,
                NULL::VARCHAR AS candidate_cache_key,
                100.0::DOUBLE AS candidate_score_override,
                'lookup'::VARCHAR AS candidate_match_method
            FROM {view} i
            INNER JOIN address_lookup l
              ON l.lookup_norm = i.lookup_norm
             AND {where}
            INNER JOIN addresses a
              ON a.address_id = l.address_id
            QUALIFY row_number() OVER (
                PARTITION BY i.input_id
                ORDER BY l.confidence DESC NULLS LAST, l.lookup_id
            ) = 1
        """
        try:
            return self.connection.execute(query).df()
        finally:
            self.connection.unregister(view)

    def history_cache_query(self, inputs: pd.DataFrame) -> pd.DataFrame:
        """Resolve exact normalized historical mappings to candidates."""

        self.create()
        required = ["input_id", "cache_key"]
        missing = [column for column in required if column not in inputs.columns]
        if missing:
            raise ValueError(f"History-cache input is missing columns: {', '.join(missing)}")
        if not len(inputs):
            return pd.DataFrame()
        frame = inputs[required].copy()
        frame["cache_key"] = frame["cache_key"].fillna("").astype(str)
        view = f"_geotiger_history_inputs_{uuid.uuid4().hex}"
        self.connection.register(view, frame)
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
                a.source AS candidate_source,
                a.source_type AS candidate_source_type,
                a.source_priority AS candidate_source_priority,
                a.source_record_id AS candidate_source_record_id,
                coalesce(a.is_intersection, FALSE) AS candidate_is_intersection,
                a.intersection_key AS candidate_intersection_key,
                a.intersection_street_norm AS candidate_intersection_street_norm,
                NULL::VARCHAR AS candidate_lookup_id,
                h.cache_key AS candidate_cache_key,
                100.0::DOUBLE AS candidate_score_override,
                'history_cache'::VARCHAR AS candidate_match_method
            FROM {view} i
            INNER JOIN history_cache h ON h.cache_key = i.cache_key
            INNER JOIN addresses a ON a.address_id = h.address_id
        """
        try:
            return self.connection.execute(query).df()
        finally:
            self.connection.unregister(view)

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
        if "source" not in frame.columns:
            frame["source"] = "unknown"
        if "source_type" not in frame.columns:
            frame["source_type"] = frame["source"].map(normalize_source_type)
        else:
            frame["source_type"] = frame["source_type"].map(normalize_source_type)
        if "source_priority" not in frame.columns:
            frame["source_priority"] = frame["source_type"].map(source_priority)
        else:
            frame["source_priority"] = pd.to_numeric(frame["source_priority"], errors="coerce")
            frame["source_priority"] = frame["source_priority"].fillna(
                frame["source_type"].map(source_priority)
            )
        if "source_record_id" not in frame.columns:
            frame["source_record_id"] = frame["address_id"].astype(str)
        else:
            frame["source_record_id"] = frame["source_record_id"].fillna(
                frame["address_id"].astype(str)
            )
        for column in ADDRESS_COLUMNS:
            if column not in frame.columns:
                frame[column] = None
        frame = frame[ADDRESS_COLUMNS]
        if replace:
            # DuckDB versions with persistent secondary indexes can reject a
            # bulk DELETE while the indexed table is being reshaped. Drop the
            # advisory indexes for a replacement load and recreate them after
            # the new reference rows are inserted.
            for index_name in (
                "addresses_state_zip",
                "addresses_state_city",
                "addresses_house_number",
                "addresses_street_block",
                "addresses_intersection_key",
            ):
                try:
                    self.connection.execute(f"DROP INDEX IF EXISTS {index_name}")
                except duckdb.Error:
                    pass
            self.connection.execute("DELETE FROM addresses")
        view = f"_geotiger_load_{uuid.uuid4().hex}"
        self.connection.register(view, frame)
        try:
            self.connection.execute(
                f"INSERT INTO addresses SELECT {', '.join(ADDRESS_COLUMNS)} FROM {view}"
            )
        finally:
            self.connection.unregister(view)
        if replace:
            self.create()
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
            "is_intersection",
            "intersection_key",
        ]
        missing = [column for column in required if column not in inputs.columns]
        if missing:
            raise ValueError(f"Parsed input is missing columns: {', '.join(missing)}")
        frame = inputs[required].copy()
        frame["house_number"] = frame["house_number"].astype("Int64")
        frame["is_intersection"] = frame["is_intersection"].fillna(False).astype(bool)
        for column in ("street_block", "state_norm", "city_norm", "zip5", "intersection_key"):
            frame[column] = frame[column].fillna("").astype(str)
        view = f"_geotiger_inputs_{uuid.uuid4().hex}"
        self.connection.register(view, frame)
        conditions = []
        conditions.append("coalesce(a.is_intersection, FALSE) = i.is_intersection")
        conditions.append("(NOT i.is_intersection OR a.intersection_key = i.intersection_key)")
        for column in ("state_norm",):
            if frame[column].ne("").all():
                conditions.append(f"a.{column} = i.{column}")
            else:
                conditions.append(f"(i.{column} = '' OR a.{column} = i.{column})")
        if exact_street:
            conditions.append("a.street_norm = i.street_norm")
        elif street_blocking:
            if frame["street_block"].ne("").all():
                conditions.append("(i.is_intersection OR a.street_block = i.street_block)")
            else:
                conditions.append(
                    "(i.is_intersection OR i.street_block = '' OR "
                    "a.street_block = i.street_block)"
                )
        if strict_locality:
            for column in ("city_norm", "zip5"):
                if frame[column].ne("").all():
                    conditions.append(f"a.{column} = i.{column}")
                else:
                    conditions.append(f"(i.{column} = '' OR a.{column} = i.{column})")
        conditions.append(
            f"(i.is_intersection OR i.house_number IS NULL OR "
            f"abs(a.house_number - i.house_number) <= {int(house_number_tolerance)})"
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
                a.source AS candidate_source,
                a.source_type AS candidate_source_type,
                a.source_priority AS candidate_source_priority,
                a.source_record_id AS candidate_source_record_id,
                coalesce(a.is_intersection, FALSE) AS candidate_is_intersection,
                a.intersection_key AS candidate_intersection_key,
                a.intersection_street_norm AS candidate_intersection_street_norm,
                NULL::VARCHAR AS candidate_lookup_id,
                NULL::VARCHAR AS candidate_cache_key,
                NULL::DOUBLE AS candidate_score_override,
                'candidate'::VARCHAR AS candidate_match_method
            FROM {view} i
            INNER JOIN addresses a
              ON {join_where}
        """
        try:
            return self.connection.execute(query).df()
        finally:
            self.connection.unregister(view)
