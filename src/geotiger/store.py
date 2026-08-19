"""DuckDB persistence and blocking queries for the prepared reference table."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from .normalize import (
    intersection_key,
    normalize_state,
    normalize_text,
    normalize_zip,
    parse_address,
    intersection_component_keys,
    route_component_keys,
    street_name_key,
    street_name_phonetic_key,
)
from .schema import (
    ADDRESS_COLUMNS,
    address_cache_key,
    normalize_source_type,
    source_priority,
)


def _deduplicate_candidate_inputs(
    frame: pd.DataFrame,
    key_columns: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Query one representative per repeated block while preserving outputs."""

    if not len(frame) or not frame.duplicated(key_columns).any():
        return frame, None
    representatives = frame.drop_duplicates(key_columns, keep="first").copy()
    mapping = frame[key_columns + ["input_id"]].rename(
        columns={"input_id": "original_input_id"}
    ).merge(
        representatives[key_columns + ["input_id"]].rename(
            columns={"input_id": "representative_input_id"}
        ),
        on=key_columns,
        how="left",
        validate="many_to_one",
    )
    return representatives, mapping[["representative_input_id", "original_input_id"]]


def _expand_candidate_inputs(
    candidates: pd.DataFrame,
    mapping: pd.DataFrame | None,
) -> pd.DataFrame:
    """Expand internally deduplicated candidate rows to original input IDs."""

    if mapping is None or not len(candidates):
        return candidates
    expanded = candidates.rename(columns={"input_id": "representative_input_id"}).merge(
        mapping,
        on="representative_input_id",
        how="inner",
    )
    expanded["input_id"] = expanded["original_input_id"]
    return expanded.drop(columns=["representative_input_id", "original_input_id"])


class GeoTIGERStore:
    """A reusable local DuckDB store for expanded address candidates."""

    def __init__(self, path: str | os.PathLike[str] = ":memory:", *, threads: int | None = None):
        self.path = str(path)
        self.threads = max(1, threads or (os.cpu_count() or 1))
        self._connection: duckdb.DuckDBPyConnection | None = None
        self._intersection_table_initialized = False
        self._street_keys_initialized = False

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
                street_name_key VARCHAR,
                street_name_phonetic VARCHAR,
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
                intersection_street_block VARCHAR,
                intersection_match_key VARCHAR,
                intersection_phonetic_key VARCHAR
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
            ("street_name_key", "VARCHAR"),
            ("street_name_phonetic", "VARCHAR"),
            ("is_intersection", "BOOLEAN"),
            ("intersection_key", "VARCHAR"),
            ("intersection_street_norm", "VARCHAR"),
            ("intersection_street_block", "VARCHAR"),
            ("intersection_match_key", "VARCHAR"),
            ("intersection_phonetic_key", "VARCHAR"),
        ):
            self.connection.execute(
                f"ALTER TABLE addresses ADD COLUMN IF NOT EXISTS {column} {definition}"
            )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS address_intersections AS "
            "SELECT * FROM addresses WHERE FALSE"
        )
        for column, definition in (
            ("street_name_key", "VARCHAR"),
            ("street_name_phonetic", "VARCHAR"),
            ("intersection_match_key", "VARCHAR"),
            ("intersection_phonetic_key", "VARCHAR"),
        ):
            self.connection.execute(
                f"ALTER TABLE address_intersections "
                f"ADD COLUMN IF NOT EXISTS {column} {definition}"
            )
        if not self._intersection_table_initialized:
            if self.connection.execute(
                "SELECT count(*) FROM address_intersections"
            ).fetchone()[0] == 0:
                self.connection.execute(
                    "INSERT INTO address_intersections "
                    f"({', '.join(ADDRESS_COLUMNS)}) SELECT "
                    f"{', '.join(ADDRESS_COLUMNS)} FROM addresses "
                    "WHERE is_intersection IS TRUE"
                )
            self._intersection_table_initialized = True
        self._backfill_street_keys()
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
            "CREATE INDEX IF NOT EXISTS addresses_street_name_key ON "
            "addresses(street_name_key)",
            "CREATE INDEX IF NOT EXISTS addresses_street_name_phonetic ON "
            "addresses(street_name_phonetic)",
            "CREATE INDEX IF NOT EXISTS addresses_intersection_key ON "
            "addresses(intersection_key)",
            "CREATE INDEX IF NOT EXISTS addresses_intersection_match_key ON "
            "addresses(intersection_match_key)",
            "CREATE INDEX IF NOT EXISTS address_intersections_key ON "
            "address_intersections(intersection_key)",
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

    def _backfill_street_keys(self) -> None:
        """Populate match keys when opening databases made by older releases."""

        if self._street_keys_initialized:
            return
        for table in ("addresses", "address_intersections"):
            missing = self.connection.execute(
                f"""
                SELECT DISTINCT
                    coalesce(pre_directional, '') AS pre_directional,
                    coalesce(street_name, '') AS street_name,
                    coalesce(street_suffix, '') AS street_suffix,
                    coalesce(post_directional, '') AS post_directional,
                    coalesce(state_norm, '') AS state_norm,
                    coalesce(intersection_street_norm, '') AS intersection_street_norm,
                    coalesce(is_intersection, FALSE) AS is_intersection
                FROM {table}
                WHERE street_name_key IS NULL
                   OR street_name_phonetic IS NULL
                   OR intersection_match_key IS NULL
                   OR intersection_phonetic_key IS NULL
                """
            ).df()
            if not len(missing):
                continue
            key_rows: list[dict[str, Any]] = []
            for row in missing.itertuples(index=False):
                name_key = street_name_key(
                    row.street_name,
                    row.street_suffix,
                    row.state_norm,
                )
                phonetic = street_name_phonetic_key(
                    row.street_name,
                    row.street_suffix,
                    row.state_norm,
                )
                match_key = ""
                intersection_phonetic = ""
                if row.is_intersection and row.intersection_street_norm:
                    second = parse_address(f"1 {row.intersection_street_norm}")
                    match_key = intersection_key(name_key, second.street_name_key)
                    intersection_phonetic = intersection_key(
                        phonetic,
                        second.street_name_phonetic,
                    )
                key_rows.append(
                    {
                        "pre_directional": row.pre_directional,
                        "street_name": row.street_name,
                        "street_suffix": row.street_suffix,
                        "post_directional": row.post_directional,
                        "state_norm": row.state_norm,
                        "intersection_street_norm": row.intersection_street_norm,
                        "is_intersection": bool(row.is_intersection),
                        "street_name_key_value": name_key,
                        "street_name_phonetic_value": phonetic,
                        "intersection_match_key_value": match_key,
                        "intersection_phonetic_key_value": intersection_phonetic,
                    }
                )
            keys = pd.DataFrame(key_rows)
            view = f"_geotiger_street_keys_{uuid.uuid4().hex}"
            self.connection.register(view, keys)
            try:
                self.connection.execute(
                    f"""
                    UPDATE {table} AS a
                    SET street_name_key = k.street_name_key_value,
                        street_name_phonetic = k.street_name_phonetic_value,
                        intersection_match_key = k.intersection_match_key_value,
                        intersection_phonetic_key = k.intersection_phonetic_key_value
                    FROM {view} AS k
                    WHERE coalesce(a.pre_directional, '') = k.pre_directional
                      AND coalesce(a.street_name, '') = k.street_name
                      AND coalesce(a.street_suffix, '') = k.street_suffix
                      AND coalesce(a.post_directional, '') = k.post_directional
                      AND coalesce(a.state_norm, '') = k.state_norm
                      AND coalesce(a.intersection_street_norm, '') =
                          k.intersection_street_norm
                      AND coalesce(a.is_intersection, FALSE) = k.is_intersection
                    """
                )
            finally:
                self.connection.unregister(view)
        self._street_keys_initialized = True

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
                "SELECT count(*) FROM address_intersections"
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
                a.street_name_key AS candidate_street_name_key,
                a.street_name_phonetic AS candidate_street_name_phonetic,
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
                a.intersection_match_key AS candidate_intersection_match_key,
                a.intersection_phonetic_key AS candidate_intersection_phonetic_key,
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
                a.street_name_key AS candidate_street_name_key,
                a.street_name_phonetic AS candidate_street_name_phonetic,
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
                a.intersection_match_key AS candidate_intersection_match_key,
                a.intersection_phonetic_key AS candidate_intersection_phonetic_key,
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
                "addresses_street_name_key",
                "addresses_street_name_phonetic",
                "addresses_intersection_key",
                "addresses_intersection_match_key",
                "address_intersections_key",
            ):
                try:
                    self.connection.execute(f"DROP INDEX IF EXISTS {index_name}")
                except duckdb.Error:
                    pass
            self.connection.execute("DELETE FROM addresses")
            self.connection.execute("DELETE FROM address_intersections")
        view = f"_geotiger_load_{uuid.uuid4().hex}"
        self.connection.register(view, frame)
        try:
            self.connection.execute(
                f"INSERT INTO addresses ({', '.join(ADDRESS_COLUMNS)}) "
                f"SELECT {', '.join(ADDRESS_COLUMNS)} FROM {view}"
            )
            self.connection.execute(
                "INSERT INTO address_intersections "
                f"({', '.join(ADDRESS_COLUMNS)}) SELECT "
                f"{', '.join(ADDRESS_COLUMNS)} FROM {view} "
                "WHERE is_intersection IS TRUE"
            )
        finally:
            self.connection.unregister(view)
        self._street_keys_initialized = False
        self._backfill_street_keys()
        if replace:
            self.create()
        return len(frame)

    def candidate_query(
        self,
        inputs: pd.DataFrame,
        *,
        house_number_tolerance: int = 100,
        street_blocking: bool = True,
        strict_locality: bool = True,
        exact_street_first: bool = True,
        exact_house_number_first: bool = True,
        exact_street: bool = False,
        compact_street: bool = False,
        name_key_street: bool = False,
        phonetic_street: bool = False,
        street_fallback: bool = True,
        street_variant_fallback: bool = True,
        intersection_variant: bool = False,
        intersection_phonetic: bool = False,
        _intersection_only: bool = False,
    ) -> pd.DataFrame:
        """Return blocked candidates for parsed input records.

        DuckDB executes this join with the store's configured thread count. No
        input data is sent outside the process.
        """

        self.create()
        if not _intersection_only and len(inputs) and "is_intersection" in inputs.columns:
            intersection_mask = inputs["is_intersection"].fillna(False).astype(bool)
            if intersection_mask.any():
                intersection_candidates = self.candidate_query(
                    inputs.loc[intersection_mask],
                    house_number_tolerance=house_number_tolerance,
                    street_blocking=street_blocking,
                    strict_locality=strict_locality,
                    exact_street_first=True,
                    exact_house_number_first=False,
                    exact_street=False,
                    compact_street=False,
                    street_fallback=False,
                    street_variant_fallback=street_variant_fallback,
                    _intersection_only=True,
                )
                ordinary_inputs = inputs.loc[~intersection_mask]
                if not len(ordinary_inputs):
                    return intersection_candidates
                ordinary_candidates = self.candidate_query(
                    ordinary_inputs,
                    house_number_tolerance=house_number_tolerance,
                    street_blocking=street_blocking,
                    strict_locality=strict_locality,
                    exact_street_first=exact_street_first,
                    exact_house_number_first=exact_house_number_first,
                    exact_street=exact_street,
                    compact_street=compact_street,
                    street_fallback=street_fallback,
                    street_variant_fallback=street_variant_fallback,
                )
                candidate_frames = [
                    candidate_frame
                    for candidate_frame in (intersection_candidates, ordinary_candidates)
                    if len(candidate_frame)
                ]
                return (
                    pd.concat(candidate_frames, ignore_index=True)
                    if candidate_frames
                    else pd.DataFrame()
                )
        if _intersection_only:
            exact_house_number_first = False
        matching_mode = any(
            (
                exact_street,
                name_key_street,
                phonetic_street,
                intersection_variant,
                intersection_phonetic,
            )
        )
        if exact_street_first and not matching_mode:
            eligibility_column = "intersection_key" if _intersection_only else "street_norm"
            eligible = inputs.loc[inputs[eligibility_column].fillna("").ne("")]
            exact = self.candidate_query(
                eligible,
                house_number_tolerance=house_number_tolerance,
                street_blocking=street_blocking,
                strict_locality=strict_locality,
                exact_street_first=False,
                exact_house_number_first=exact_house_number_first,
                exact_street=True,
                compact_street=False,
                street_variant_fallback=street_variant_fallback,
                _intersection_only=_intersection_only,
            )
            found = set(exact["input_id"].tolist()) if len(exact) else set()
            remaining = inputs.loc[~inputs["input_id"].isin(found)]
            compact = pd.DataFrame()
            if len(remaining) and not _intersection_only:
                compact = self.candidate_query(
                    remaining,
                    house_number_tolerance=house_number_tolerance,
                    street_blocking=street_blocking,
                    strict_locality=strict_locality,
                    exact_street_first=False,
                    exact_house_number_first=exact_house_number_first,
                    exact_street=True,
                    compact_street=True,
                    street_fallback=False,
                    street_variant_fallback=street_variant_fallback,
                )
                compact_found = set(compact["input_id"].tolist()) if len(compact) else set()
                remaining = remaining.loc[~remaining["input_id"].isin(compact_found)]
            variant = pd.DataFrame()
            if len(remaining) and street_variant_fallback:
                variant_keys = [
                    "house_number",
                    "state_norm",
                    "city_norm",
                    "zip5",
                    "is_intersection",
                    (
                        "intersection_match_key"
                        if _intersection_only
                        else "street_name_key"
                    ),
                ]
                variant_inputs, variant_mapping = _deduplicate_candidate_inputs(
                    remaining,
                    variant_keys,
                )
                variant = self.candidate_query(
                    variant_inputs,
                    house_number_tolerance=house_number_tolerance,
                    street_blocking=street_blocking,
                    strict_locality=strict_locality,
                    exact_street_first=False,
                    exact_house_number_first=exact_house_number_first,
                    name_key_street=not _intersection_only,
                    intersection_variant=_intersection_only,
                    street_fallback=False,
                    street_variant_fallback=street_variant_fallback,
                    _intersection_only=_intersection_only,
                )
                variant = _expand_candidate_inputs(variant, variant_mapping)
                variant_found = set(variant["input_id"].tolist()) if len(variant) else set()
                remaining = remaining.loc[~remaining["input_id"].isin(variant_found)]
            phonetic = pd.DataFrame()
            if len(remaining) and street_variant_fallback:
                phonetic_keys = [
                    "house_number",
                    "state_norm",
                    "city_norm",
                    "zip5",
                    "is_intersection",
                    (
                        "intersection_phonetic_key"
                        if _intersection_only
                        else "street_name_phonetic"
                    ),
                ]
                phonetic_inputs, phonetic_mapping = _deduplicate_candidate_inputs(
                    remaining,
                    phonetic_keys,
                )
                phonetic = self.candidate_query(
                    phonetic_inputs,
                    house_number_tolerance=house_number_tolerance,
                    street_blocking=street_blocking,
                    strict_locality=strict_locality,
                    exact_street_first=False,
                    exact_house_number_first=exact_house_number_first,
                    phonetic_street=not _intersection_only,
                    intersection_phonetic=_intersection_only,
                    street_fallback=False,
                    street_variant_fallback=street_variant_fallback,
                    _intersection_only=_intersection_only,
                )
                phonetic = _expand_candidate_inputs(phonetic, phonetic_mapping)
                phonetic_found = (
                    set(phonetic["input_id"].tolist()) if len(phonetic) else set()
                )
                remaining = remaining.loc[~remaining["input_id"].isin(phonetic_found)]
            exact_frames = [
                candidate_frame
                for candidate_frame in (exact, compact, variant, phonetic)
                if len(candidate_frame)
            ]
            if not len(remaining) or not street_fallback or _intersection_only:
                return (
                    pd.concat(exact_frames, ignore_index=True)
                    if exact_frames
                    else pd.DataFrame()
                )
            fallback = self.candidate_query(
                remaining,
                house_number_tolerance=house_number_tolerance,
                street_blocking=street_blocking,
                strict_locality=strict_locality,
                exact_street_first=False,
                exact_house_number_first=exact_house_number_first,
                exact_street=False,
                street_fallback=street_fallback,
                street_variant_fallback=street_variant_fallback,
            )
            return pd.concat([*exact_frames, fallback], ignore_index=True)
        if exact_house_number_first and house_number_tolerance > 0:
            exact = self.candidate_query(
                inputs,
                house_number_tolerance=0,
                street_blocking=street_blocking,
                strict_locality=strict_locality,
                exact_street_first=False,
                exact_house_number_first=False,
                exact_street=exact_street,
                compact_street=compact_street,
                name_key_street=name_key_street,
                phonetic_street=phonetic_street,
                intersection_variant=intersection_variant,
                intersection_phonetic=intersection_phonetic,
                street_variant_fallback=street_variant_fallback,
                _intersection_only=_intersection_only,
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
                compact_street=compact_street,
                name_key_street=name_key_street,
                phonetic_street=phonetic_street,
                intersection_variant=intersection_variant,
                intersection_phonetic=intersection_phonetic,
                street_variant_fallback=street_variant_fallback,
                _intersection_only=_intersection_only,
            )
            return pd.concat([exact, fallback], ignore_index=True)
        required = [
            "input_id",
            "house_number",
            "street_norm",
            "street_block",
            "street_name_key",
            "street_name_phonetic",
            "state_norm",
            "city_norm",
            "zip5",
            "is_intersection",
            "intersection_key",
            "intersection_match_key",
            "intersection_phonetic_key",
        ]
        missing = [column for column in required if column not in inputs.columns]
        if missing:
            raise ValueError(f"Parsed input is missing columns: {', '.join(missing)}")
        frame = inputs[required].copy()
        frame["house_number"] = frame["house_number"].astype("Int64")
        frame["is_intersection"] = frame["is_intersection"].fillna(False).astype(bool)
        for column in (
            "street_block",
            "street_name_key",
            "street_name_phonetic",
            "state_norm",
            "city_norm",
            "zip5",
            "intersection_key",
            "intersection_match_key",
            "intersection_phonetic_key",
        ):
            frame[column] = frame[column].fillna("").astype(str)
        frame["street_name_key_alts"] = [
            route_component_keys(value) for value in frame["street_name_key"]
        ]
        frame["intersection_match_key_alts"] = [
            intersection_component_keys(value)
            for value in frame["intersection_match_key"]
        ]
        view = f"_geotiger_inputs_{uuid.uuid4().hex}"
        self.connection.register(view, frame)
        conditions = []
        if _intersection_only:
            conditions.append("a.is_intersection IS TRUE")
            if intersection_variant:
                conditions.append(
                    "(a.intersection_match_key = i.intersection_match_key OR "
                    "list_contains(i.intersection_match_key_alts, a.intersection_match_key))"
                )
            elif intersection_phonetic:
                conditions.append(
                    "a.intersection_phonetic_key = i.intersection_phonetic_key"
                )
            else:
                conditions.append("a.intersection_key = i.intersection_key")
        else:
            conditions.append("a.is_intersection IS NOT TRUE")
        for column in ("state_norm",):
            if frame[column].ne("").all():
                conditions.append(f"a.{column} = i.{column}")
            else:
                conditions.append(f"(i.{column} = '' OR a.{column} = i.{column})")
        if not _intersection_only:
            if name_key_street:
                conditions.append(
                    "(a.street_name_key = i.street_name_key OR "
                    "list_contains(i.street_name_key_alts, a.street_name_key))"
                )
            elif phonetic_street:
                conditions.append("a.street_name_phonetic = i.street_name_phonetic")
            elif exact_street:
                if compact_street:
                    # Treat spacing-only variants such as ``SNOWCREST TRL``
                    # and ``SNOW CREST TRL`` as the same normalized street.
                    # This branch only receives rows unresolved by the fast
                    # exact-street pass. Do not also require street_block:
                    # suffix splits change the name token used for that key.
                    conditions.append(
                        "replace(a.street_norm, ' ', '') = replace(i.street_norm, ' ', '')"
                    )
                else:
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
        qualify = ""
        if not _intersection_only:
            conditions.append(
                f"(i.house_number IS NULL OR "
                f"abs(a.house_number - i.house_number) <= {int(house_number_tolerance)})"
            )
            qualify = """
            QUALIFY i.house_number IS NOT NULL
                 OR ROW_NUMBER() OVER (
                        PARTITION BY i.input_id
                        ORDER BY a.house_number, a.address_id
                    ) = 1
            """
        join_where = " AND ".join(conditions) or "TRUE"
        reference_table = "address_intersections" if _intersection_only else "addresses"
        if intersection_phonetic:
            match_method = "intersection_phonetic"
        elif intersection_variant:
            match_method = "intersection_canonical"
        elif _intersection_only:
            match_method = "intersection_exact"
        elif phonetic_street:
            match_method = "street_phonetic"
        elif name_key_street:
            match_method = "street_canonical"
        elif compact_street:
            match_method = "street_spacing"
        elif exact_street:
            match_method = "street_exact"
        else:
            match_method = "street_block"
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
                a.street_name_key AS candidate_street_name_key,
                a.street_name_phonetic AS candidate_street_name_phonetic,
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
                a.intersection_match_key AS candidate_intersection_match_key,
                a.intersection_phonetic_key AS candidate_intersection_phonetic_key,
                NULL::VARCHAR AS candidate_lookup_id,
                NULL::VARCHAR AS candidate_cache_key,
                NULL::DOUBLE AS candidate_score_override,
                '{match_method}'::VARCHAR AS candidate_match_method
            FROM {view} i
            INNER JOIN {reference_table} a
              ON {join_where}
            {qualify}
        """
        try:
            return self.connection.execute(query).df()
        finally:
            self.connection.unregister(view)
