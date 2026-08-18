"""Command-line entry points for common analyst workflows."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import typer

from .geocoder import Geocoder
from .interpolate import InterpolationConfig, prepare_ranges
from .sources import download_tiger_ranges, load_ranges, save_ranges
from .store import GeoTIGERStore
from .viz import matches_map

app = typer.Typer(help="Local-first US geocoding from Census TIGER address ranges.")


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".parquet", ".geoparquet"}:
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise typer.BadParameter("Input must be .csv or .parquet")


def _write_table(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".parquet", ".geoparquet"}:
        frame.to_parquet(path, index=False)
    elif path.suffix.lower() == ".csv":
        frame.to_csv(path, index=False)
    else:
        raise typer.BadParameter("Output must be .csv or .parquet")


@app.command()
def download(
    state: str = typer.Option(..., help="State abbreviation or code accepted by pygris."),
    county: str | None = typer.Option(None, help="Optional county name/FIPS."),
    year: int = typer.Option(2024, help="TIGER/Line vintage."),
    output: Path = typer.Option(..., "--output", "-o", help="Local GeoParquet/GeoPackage output."),
) -> None:
    """Download TIGER ranges; this is the explicit network step."""

    ranges = download_tiger_ranges(state, county=county, year=year)
    save_ranges(ranges, output)
    typer.echo(f"Saved {len(ranges):,} TIGER ranges to {output}")


@app.command()
def prepare(
    ranges: Path = typer.Option(..., help="Local TIGER range file."),
    database: Path = typer.Option(
        ..., "--database", "-d", help="DuckDB database to create/update."
    ),
    state: str | None = typer.Option(None, help="State override when ranges only contain STATEFP."),
    source: str = typer.Option("tiger", help="Source label persisted with each candidate."),
    end_offset_m: float = typer.Option(5.0, help="Distance to keep from segment endpoints."),
    side_offset_m: float = typer.Option(5.0, help="Distance to offset from the street centerline."),
    replace: bool = typer.Option(True, help="Replace existing address candidates."),
) -> None:
    """Expand local ranges and store them in DuckDB."""

    range_frame = load_ranges(ranges)
    prepared = prepare_ranges(
        range_frame,
        config=InterpolationConfig(end_offset_m=end_offset_m, side_offset_m=side_offset_m),
        state=state,
        source=source,
    )
    with GeoTIGERStore(database) as store:
        count = store.ingest_candidates(prepared, replace=replace)
        store.set_metadata(source=source, input_ranges=str(ranges), candidates=count)
    typer.echo(f"Stored {count:,} expanded candidates in {database}")


@app.command()
def geocode(
    database: Path = typer.Option(..., "--database", "-d"),
    input: Path = typer.Option(..., "--input", "-i"),
    output: Path = typer.Option(..., "--output", "-o"),
    all_candidates: Path | None = typer.Option(
        None, help="Optional path for every blocked candidate."
    ),
    address_column: str = typer.Option("address"),
    city_column: str | None = typer.Option("city"),
    state_column: str | None = typer.Option("state"),
    zip_column: str | None = typer.Option("zip"),
    auto_threshold: float = typer.Option(90.0),
    review_threshold: float = typer.Option(75.0),
) -> None:
    """Geocode a local CSV/Parquet table with no network calls."""

    from .geocoder import GeocoderConfig

    records = _read_table(input)
    result = Geocoder(
        GeoTIGERStore(database),
        config=GeocoderConfig(
            auto_match_threshold=auto_threshold,
            review_threshold=review_threshold,
        ),
    ).geocode(
        records,
        address_column=address_column,
        city_column=city_column,
        state_column=state_column,
        zip_column=zip_column,
    )
    _write_table(result.matches, output)
    if all_candidates:
        _write_table(result.candidates, all_candidates)
    typer.echo(json.dumps(result.timings.to_dict(), indent=2))


@app.command("map")
def map_results(
    input: Path = typer.Option(..., help="Geocoded CSV/Parquet output."),
    output: Path = typer.Option(..., help="Offline HTML map output."),
    tiles: str | None = typer.Option(
        None, help="Optional tile layer; defaults to no online basemap."
    ),
) -> None:
    """Create an analyst map of geocoded results."""

    frame = _read_table(input)
    fmap = matches_map(frame, tiles=tiles)
    output.parent.mkdir(parents=True, exist_ok=True)
    fmap.save(output)
    typer.echo(f"Saved map to {output}")
