"""Local blocking, fuzzy scoring, and match aggregation."""

from __future__ import annotations

import platform
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from rapidfuzz.distance import Levenshtein

from .normalize import normalize_text, parse_record
from .store import GeoTIGERStore

DEFAULT_WEIGHTS = {
    "house_number": 0.40,
    "street": 0.35,
    "city": 0.10,
    "state": 0.05,
    "zip5": 0.10,
}


@dataclass(frozen=True)
class GeocoderConfig:
    """Matching defaults modeled after conservative desktop geocoders."""

    auto_match_threshold: float = 90.0
    review_threshold: float = 75.0
    min_margin: float = 0.0
    house_number_tolerance: int = 25
    exact_house_number_first: bool = True
    street_blocking: bool = True
    exact_street_first: bool = True
    street_fallback: bool = True
    strict_locality: bool = True
    use_lookup_table: bool = True
    use_history_cache: bool = True
    weights: Mapping[str, float] = field(default_factory=lambda: DEFAULT_WEIGHTS.copy())


@dataclass(frozen=True)
class TimingReport:
    input_count: int
    candidate_count: int
    lookup_hit_count: int
    history_cache_hit_count: int
    matched_count: int
    review_count: int
    unmatched_count: int
    parse_seconds: float
    lookup_seconds: float
    history_cache_seconds: float
    candidate_query_seconds: float
    scoring_seconds: float
    aggregation_seconds: float
    total_seconds: float
    threads: int

    @property
    def throughput_per_second(self) -> float:
        return self.input_count / self.total_seconds if self.total_seconds else 0.0

    def to_dict(self) -> dict[str, Any]:
        result = {
            "input_count": self.input_count,
            "candidate_count": self.candidate_count,
            "lookup_hit_count": self.lookup_hit_count,
            "history_cache_hit_count": self.history_cache_hit_count,
            "matched_count": self.matched_count,
            "review_count": self.review_count,
            "unmatched_count": self.unmatched_count,
            "parse_seconds": round(self.parse_seconds, 6),
            "lookup_seconds": round(self.lookup_seconds, 6),
            "history_cache_seconds": round(self.history_cache_seconds, 6),
            "candidate_query_seconds": round(self.candidate_query_seconds, 6),
            "scoring_seconds": round(self.scoring_seconds, 6),
            "aggregation_seconds": round(self.aggregation_seconds, 6),
            "total_seconds": round(self.total_seconds, 6),
            "throughput_per_second": round(self.throughput_per_second, 3),
            "duckdb_threads": self.threads,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        }
        return result


@dataclass
class GeocodeResult:
    """All outputs from one geocoding run."""

    matches: pd.DataFrame
    candidates: pd.DataFrame
    parsed_inputs: pd.DataFrame
    timings: TimingReport


def _text_similarity(left: Any, right: Any) -> float | None:
    left = str(left or "")
    right = str(right or "")
    if not left or not right:
        return None
    return float(Levenshtein.normalized_similarity(left, right) * 100.0)


def _text_similarity_batch(left: pd.Series, right: pd.Series) -> pd.Series:
    """Score a text column while handling exact matches without fuzzy calls."""

    left = left.fillna("").astype(str)
    right = right.fillna("").astype(str)
    available = left.ne("") & right.ne("")
    exact = available & left.eq(right)
    scores = pd.Series(float("nan"), index=left.index, dtype=float)
    scores.loc[exact] = 100.0
    fuzzy_index = scores.index[available & ~exact]
    if len(fuzzy_index):
        scores.loc[fuzzy_index] = [
            _text_similarity(left.loc[index], right.loc[index]) for index in fuzzy_index
        ]
    return scores


def _parse_cache_text(value: Any) -> str:
    """Convert a scalar input field into a stable per-run cache key part."""

    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)


def _cache_key_series(parsed: pd.DataFrame) -> pd.Series:
    """Build historical-cache keys without a row-wise Python apply."""

    parts = []
    for column in ("house_number", "street_norm", "city_norm", "state_norm", "zip5"):
        values = parsed[column].fillna("").astype(str)
        values = values.replace({"nan": "", "None": "", "<NA>": ""})
        parts.append(values)
    result = parts[0]
    for part in parts[1:]:
        result = result.str.cat(part, sep="\x1f")
    return result


def _number_similarity(left: Any, right: Any) -> float | None:
    if left is None or right is None or pd.isna(left) or pd.isna(right):
        return None
    left, right = int(left), int(right)
    difference = abs(left - right)
    scale = max(25, left)
    return max(0.0, 100.0 * (1.0 - difference / scale))


def _score_candidates(
    candidates: pd.DataFrame,
    parsed_inputs: pd.DataFrame,
    weights: Mapping[str, float],
) -> pd.DataFrame:
    """Vectorized batch scoring with RapidFuzz calls over plain Python lists.

    Avoiding ``DataFrame.iterrows`` is material for a 10k-row run: the blocked
    table commonly contains hundreds of thousands of potential candidates.
    """

    if "candidate_score_override" in candidates.columns:
        overrides = pd.to_numeric(candidates["candidate_score_override"], errors="coerce")
        shortcut_mask = overrides.notna()
        if shortcut_mask.any():
            shortcut = candidates.loc[shortcut_mask].copy()
            shortcut["score"] = overrides.loc[shortcut_mask]
            for name in ("house_number", "street", "city", "state", "zip5"):
                shortcut[f"score_{name}"] = overrides.loc[shortcut_mask]
            if shortcut_mask.all():
                return shortcut
            normal = _score_candidates(candidates.loc[~shortcut_mask], parsed_inputs, weights)
            return pd.concat([normal, shortcut], ignore_index=True)

    parsed = parsed_inputs.set_index("input_id")
    joined = candidates.join(
        parsed[["house_number", "street_norm", "city_norm", "state_norm", "zip5"]].rename(
            columns={
                "house_number": "input_house_number",
                "street_norm": "input_street_norm",
                "city_norm": "input_city_norm",
                "state_norm": "input_state_norm",
                "zip5": "input_zip5",
            }
        ),
        on="input_id",
    )
    joined["score_house_number"] = (
        100.0
        - 100.0
        * (
            (joined["candidate_house_number"] - joined["input_house_number"]).abs()
            / joined["input_house_number"].clip(lower=25)
        )
    ).clip(lower=0.0)
    for name, left_column, right_column in (
        ("street", "input_street_norm", "candidate_street_norm"),
        ("city", "input_city_norm", "candidate_city_norm"),
        ("state", "input_state_norm", "candidate_state_norm"),
        ("zip5", "input_zip5", "candidate_zip5"),
    ):
        joined[f"score_{name}"] = _text_similarity_batch(
            joined[left_column], joined[right_column]
        )
    component_columns = ["house_number", "street", "city", "state", "zip5"]
    numerator = pd.Series(0.0, index=joined.index)
    denominator = pd.Series(0.0, index=joined.index)
    for name in component_columns:
        values = joined[f"score_{name}"]
        weight = float(weights.get(name, 0.0))
        available = values.notna()
        numerator = numerator.add(values.fillna(0.0) * weight)
        denominator = denominator.add(available.astype(float) * weight)
    joined["score"] = (numerator / denominator.where(denominator.ne(0), 1.0)).round(3)
    joined["score_house_number"] = joined["score_house_number"].fillna(0.0).round(3)
    for name in ("street", "city", "state", "zip5"):
        joined[f"score_{name}"] = joined[f"score_{name}"].fillna(0.0).round(3)
    return joined.drop(
        columns=[
            "input_house_number",
            "input_street_norm",
            "input_city_norm",
            "input_state_norm",
            "input_zip5",
        ]
    )


class Geocoder:
    """Geocode records against a prepared :class:`GeoTIGERStore`."""

    def __init__(self, store: GeoTIGERStore, *, config: GeocoderConfig | None = None):
        self.store = store
        self.config = config or GeocoderConfig()

    def geocode(
        self,
        records: pd.DataFrame,
        *,
        address_column: str = "address",
        city_column: str | None = "city",
        state_column: str | None = "state",
        zip_column: str | None = "zip",
    ) -> GeocodeResult:
        """Parse, block, score, and aggregate a batch of input records."""

        started = time.perf_counter()
        frame = records.copy().reset_index(drop=False).rename(columns={"index": "source_index"})
        if address_column not in frame.columns:
            raise ValueError(f"Input is missing address column {address_column!r}")
        frame["input_id"] = range(len(frame))

        parse_started = time.perf_counter()
        parsed_rows: list[dict[str, Any]] = []
        parsed_cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in frame.to_dict(orient="records"):
            input_id = int(row["input_id"])
            cache_key = (
                _parse_cache_text(row.get(address_column, "")),
                _parse_cache_text(row.get(city_column, "")) if city_column else "",
                _parse_cache_text(row.get(state_column, "")) if state_column else "",
                _parse_cache_text(row.get(zip_column, "")) if zip_column else "",
            )
            parsed_values = parsed_cache.get(cache_key)
            if parsed_values is None:
                parsed_values = parse_record(
                    row,
                    address_column=address_column,
                    city_column=city_column,
                    state_column=state_column,
                    zip_column=zip_column,
                ).to_dict()
                parsed_cache[cache_key] = parsed_values
            parsed_rows.append({"input_id": input_id, **parsed_values})
        parsed_frame = pd.DataFrame(
            parsed_rows,
            columns=[
                "input_id",
                "raw_address",
                "house_number",
                "pre_directional",
                "street_name",
                "street_suffix",
                "post_directional",
                "unit",
                "city",
                "state",
                "zip5",
                "street_norm",
                "city_norm",
                "state_norm",
                "street_block",
                "is_intersection",
                "intersection_street_norm",
                "intersection_key",
                "intersection_street_block",
            ],
        )
        parse_seconds = time.perf_counter() - parse_started

        parsed_frame["lookup_norm"] = parsed_frame["raw_address"].map(normalize_text)
        parsed_frame["cache_key"] = _cache_key_series(parsed_frame)
        cache_started = time.perf_counter()
        cache_candidates = pd.DataFrame()
        if self.config.use_history_cache:
            cache_candidates = self.store.history_cache_query(
                parsed_frame[["input_id", "cache_key"]]
            )
        history_cache_seconds = time.perf_counter() - cache_started
        cache_ids = set(cache_candidates["input_id"].tolist()) if len(cache_candidates) else set()

        lookup_started = time.perf_counter()
        lookup_candidates = pd.DataFrame()
        lookup_remaining = parsed_frame.loc[~parsed_frame["input_id"].isin(cache_ids)]
        if self.config.use_lookup_table and len(lookup_remaining):
            lookup_candidates = self.store.lookup_query(
                lookup_remaining[
                    ["input_id", "lookup_norm", "state_norm", "city_norm", "zip5"]
                ],
                strict_locality=self.config.strict_locality,
            )
        lookup_seconds = time.perf_counter() - lookup_started
        lookup_ids = (
            set(lookup_candidates["input_id"].tolist()) if len(lookup_candidates) else set()
        )

        query_started = time.perf_counter()
        candidate_remaining = parsed_frame.loc[
            ~parsed_frame["input_id"].isin(cache_ids | lookup_ids)
        ]
        if len(candidate_remaining):
            query_inputs = candidate_remaining[
                [
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
            ]
            candidate_query = self.store.candidate_query(
                query_inputs,
                house_number_tolerance=self.config.house_number_tolerance,
                street_blocking=self.config.street_blocking,
                strict_locality=self.config.strict_locality,
                exact_street_first=self.config.exact_street_first,
                exact_house_number_first=self.config.exact_house_number_first,
                street_fallback=self.config.street_fallback,
            )
        else:
            candidate_query = pd.DataFrame()
        candidate_query_seconds = time.perf_counter() - query_started
        candidate_frames = [
            frame
            for frame in (cache_candidates, lookup_candidates, candidate_query)
            if len(frame)
        ]
        candidates = (
            pd.concat(candidate_frames, ignore_index=True)
            if candidate_frames
            else pd.DataFrame()
        )

        score_started = time.perf_counter()
        if len(candidates):
            candidates = _score_candidates(candidates, parsed_frame, self.config.weights)
        else:
            for name in (
                "score_house_number",
                "score_street",
                "score_city",
                "score_state",
                "score_zip5",
                "score",
            ):
                candidates[name] = pd.Series(dtype=float)
        scoring_seconds = time.perf_counter() - score_started

        aggregation_started = time.perf_counter()
        candidates = self._add_candidate_ranks(candidates)
        matches = self._aggregate_matches(frame, parsed_frame, candidates)
        aggregation_seconds = time.perf_counter() - aggregation_started
        total_seconds = time.perf_counter() - started
        status_counts = matches["match_status"].value_counts().to_dict() if len(matches) else {}
        timings = TimingReport(
            input_count=len(frame),
            candidate_count=len(candidates),
            lookup_hit_count=len(lookup_ids),
            history_cache_hit_count=len(cache_ids),
            matched_count=int(status_counts.get("matched", 0)),
            review_count=int(status_counts.get("review", 0)),
            unmatched_count=int(status_counts.get("unmatched", 0)),
            parse_seconds=parse_seconds,
            lookup_seconds=lookup_seconds,
            history_cache_seconds=history_cache_seconds,
            candidate_query_seconds=candidate_query_seconds,
            scoring_seconds=scoring_seconds,
            aggregation_seconds=aggregation_seconds,
            total_seconds=total_seconds,
            threads=self.store.threads,
        )
        return GeocodeResult(
            matches=matches,
            candidates=candidates,
            parsed_inputs=parsed_frame,
            timings=timings,
        )

    def cache_result(self, result: GeocodeResult, *, only_auto: bool = True) -> int:
        """Persist a geocode result for exact reuse on future runs."""

        return self.store.cache_matches(result, only_auto=only_auto)

    def _add_candidate_ranks(self, candidates: pd.DataFrame) -> pd.DataFrame:
        if not len(candidates):
            candidates["candidate_rank"] = pd.Series(dtype="Int64")
            candidates["candidate_count"] = pd.Series(dtype="Int64")
            candidates["score_margin"] = pd.Series(dtype=float)
            return candidates
        candidates = candidates.copy()
        source_priority_values = candidates.get(
            "candidate_source_priority", pd.Series(index=candidates.index, dtype=float)
        )
        candidates["_source_priority"] = pd.to_numeric(
            source_priority_values, errors="coerce"
        ).fillna(10_000)
        candidates["_source_type_sort"] = candidates.get(
            "candidate_source_type", pd.Series("", index=candidates.index)
        ).fillna("").astype(str)
        candidates["_source_record_sort"] = candidates.get(
            "candidate_source_record_id", pd.Series("", index=candidates.index)
        ).fillna("").astype(str)
        candidates = candidates.sort_values(
            [
                "input_id",
                "score",
                "_source_priority",
                "_source_type_sort",
                "_source_record_sort",
                "candidate_address_id",
            ],
            ascending=[True, False, True, True, True, True],
            kind="mergesort",
        ).reset_index(drop=True).drop(
            columns=["_source_priority", "_source_type_sort", "_source_record_sort"]
        )
        groups = candidates.groupby("input_id", sort=False)
        candidates["candidate_rank"] = groups.cumcount() + 1
        counts = groups["candidate_address_id"].transform("size")
        candidates["candidate_count"] = counts.astype(int)
        second_scores = candidates.loc[candidates["candidate_rank"].eq(2)].set_index(
            "input_id"
        )["score"]
        second = candidates["input_id"].map(second_scores).fillna(0.0)
        candidates["score_margin"] = candidates["score"] - second
        return candidates

    def _aggregate_matches(
        self,
        original: pd.DataFrame,
        parsed: pd.DataFrame,
        candidates: pd.DataFrame,
    ) -> pd.DataFrame:
        best = (
            candidates[candidates["candidate_rank"] == 1].copy()
            if len(candidates)
            else pd.DataFrame()
        )
        if len(best):
            best["match_status"] = "unmatched"
            auto = best["score"].ge(self.config.auto_match_threshold) & best[
                "score_margin"
            ].ge(self.config.min_margin)
            review = best["score"].ge(self.config.review_threshold)
            best.loc[review, "match_status"] = "review"
            best.loc[auto, "match_status"] = "matched"
            best["auto_assigned"] = best["match_status"].eq("matched")
            best = best.rename(
                columns={
                    "candidate_address_id": "matched_address_id",
                    "candidate_house_number": "matched_house_number",
                    "candidate_side": "matched_side",
                    "candidate_street_norm": "matched_street_norm",
                    "candidate_city": "matched_city",
                    "candidate_state": "matched_state",
                    "candidate_zip5": "matched_zip5",
                    "candidate_latitude": "match_latitude",
                    "candidate_longitude": "match_longitude",
                    "candidate_geometry_wkt": "match_geometry_wkt",
                    "candidate_source": "matched_source",
                    "candidate_source_type": "matched_source_type",
                    "candidate_source_priority": "matched_source_priority",
                    "candidate_source_record_id": "matched_source_record_id",
                    "candidate_is_intersection": "matched_is_intersection",
                    "candidate_intersection_key": "matched_intersection_key",
                    "candidate_intersection_street_norm": "matched_intersection_street_norm",
                    "candidate_lookup_id": "matched_lookup_id",
                    "candidate_cache_key": "matched_cache_key",
                    "candidate_match_method": "match_method",
                }
            )
            keep = [
                "input_id", "matched_address_id", "matched_house_number", "matched_side",
                "matched_street_norm",
                "matched_city", "matched_state", "matched_zip5", "match_latitude",
                "match_longitude",
                "match_geometry_wkt", "matched_source", "matched_source_type",
                "matched_source_priority", "matched_source_record_id", "score", "score_margin",
                "candidate_count",
                "matched_is_intersection", "matched_intersection_key",
                "matched_intersection_street_norm",
                "matched_lookup_id", "matched_cache_key", "match_method",
                "match_status", "auto_assigned", "score_house_number", "score_street", "score_city",
                "score_state", "score_zip5",
            ]
            best = best[[column for column in keep if column in best.columns]]
        else:
            best = pd.DataFrame({"input_id": pd.Series(dtype=int)})
        result = original.merge(best, on="input_id", how="left").merge(
            parsed,
            on="input_id",
            how="left",
            suffixes=("", "_parsed"),
        )
        defaults: dict[str, Any] = {
            "matched_address_id": None,
            "matched_house_number": None,
            "matched_side": None,
            "matched_street_norm": None,
            "matched_city": None,
            "matched_state": None,
            "matched_zip5": None,
            "match_latitude": None,
            "match_longitude": None,
            "match_geometry_wkt": None,
            "matched_source": None,
            "matched_source_type": None,
            "matched_source_priority": None,
            "matched_source_record_id": None,
            "matched_is_intersection": False,
            "matched_intersection_key": None,
            "matched_intersection_street_norm": None,
            "matched_lookup_id": None,
            "matched_cache_key": None,
            "match_method": "candidate",
            "score_house_number": 0.0,
            "score_street": 0.0,
            "score_city": 0.0,
            "score_state": 0.0,
            "score_zip5": 0.0,
            "candidate_count": 0,
            "score": 0.0,
            "score_margin": 0.0,
            "match_status": "unmatched",
            "auto_assigned": False,
        }
        for column, default in defaults.items():
            if column not in result.columns:
                result[column] = default
        result["candidate_count"] = result["candidate_count"].fillna(0).astype(int)
        result["score"] = result["score"].fillna(0.0)
        result["score_margin"] = result["score_margin"].fillna(0.0)
        result["match_status"] = result["match_status"].fillna("unmatched")
        result["auto_assigned"] = result["auto_assigned"].fillna(False).astype(bool)
        return result

    def _status(self, row: pd.Series) -> str:
        score = float(row.score)
        margin = float(row.score_margin)
        if score >= self.config.auto_match_threshold and margin >= self.config.min_margin:
            return "matched"
        if score >= self.config.review_threshold:
            return "review"
        return "unmatched"
