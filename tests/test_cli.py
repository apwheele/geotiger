from __future__ import annotations

import pandas as pd
import pytest
from conftest import make_range_frame
from typer import BadParameter
from typer.testing import CliRunner

from geotiger import GeoTIGERStore, InterpolationConfig, cli, prepare_ranges

runner = CliRunner()


def test_cli_table_io_supports_csv_and_parquet(tmp_path):
    frame = pd.DataFrame({"value": [1, 2]})
    csv_path = tmp_path / "table.csv"
    parquet_path = tmp_path / "table.parquet"

    cli._write_table(frame, csv_path)
    cli._write_table(frame, parquet_path)

    assert cli._read_table(csv_path).equals(frame)
    assert cli._read_table(parquet_path).equals(frame)
    with pytest.raises(BadParameter, match="Input must"):
        cli._read_table(tmp_path / "table.txt")
    with pytest.raises(BadParameter, match="Output must"):
        cli._write_table(frame, tmp_path / "table.txt")


def test_cli_download_and_prepare_commands(tmp_path, monkeypatch):
    ranges_path = tmp_path / "ranges.parquet"
    database = tmp_path / "reference.duckdb"
    monkeypatch.setattr(
        cli,
        "download_tiger_ranges",
        lambda state, county=None, year=2024: make_range_frame().iloc[[0]],
    )

    downloaded = runner.invoke(
        cli.app,
        ["download", "--state", "NC", "--county", "063", "-o", str(ranges_path)],
    )
    prepared = runner.invoke(
        cli.app,
        [
            "prepare",
            "--ranges",
            str(ranges_path),
            "--database",
            str(database),
            "--state",
            "NC",
            "--end-offset-m",
            "0",
            "--side-offset-m",
            "0",
        ],
    )

    assert downloaded.exit_code == 0, downloaded.output
    assert prepared.exit_code == 0, prepared.output
    with GeoTIGERStore(database) as store:
        assert store.count() == 12


def test_cli_geocode_and_offline_map_commands(tmp_path):
    database = tmp_path / "reference.duckdb"
    prepared = prepare_ranges(
        make_range_frame(),
        config=InterpolationConfig(end_offset_m=0, side_offset_m=0),
    )
    with GeoTIGERStore(database) as store:
        store.ingest_candidates(prepared)

    inputs = tmp_path / "inputs.csv"
    output = tmp_path / "matches.csv"
    candidates = tmp_path / "candidates.parquet"
    html = tmp_path / "matches.html"
    pd.DataFrame(
        [{"address": "100 Main St", "city": "Durham", "state": "NC", "zip": "27514"}]
    ).to_csv(inputs, index=False)

    geocoded = runner.invoke(
        cli.app,
        [
            "geocode",
            "-d",
            str(database),
            "-i",
            str(inputs),
            "-o",
            str(output),
            "--all-candidates",
            str(candidates),
        ],
    )
    mapped = runner.invoke(
        cli.app,
        ["map", "--input", str(output), "--output", str(html)],
    )

    assert geocoded.exit_code == 0, geocoded.output
    assert mapped.exit_code == 0, mapped.output
    assert pd.read_csv(output).loc[0, "match_status"] == "matched"
    assert len(pd.read_parquet(candidates)) >= 1
    assert html.exists()
