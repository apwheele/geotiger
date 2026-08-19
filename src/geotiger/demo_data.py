"""Download and cache the public Durham crime table used by the demo."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

DURHAM_CRIME_URL = (
    "https://webgis2.durhamnc.gov/server/rest/services/"
    "PublicServices/Tables/MapServer/4"
)


def _arcgis_json(url: str, params: dict[str, object]) -> dict:
    query = urlencode(params)
    request = Request(
        f"{url}/query?{query}",
        headers={"User-Agent": "GeoTIGER demo data downloader"},
    )
    with urlopen(request, timeout=120) as response:  # noqa: S310 - explicit public demo URL
        payload = json.loads(response.read().decode("utf-8"))
    if "error" in payload:
        raise RuntimeError(payload["error"])
    return payload


def load_durham_crime(
    cache_path: str | Path,
    *,
    url: str = DURHAM_CRIME_URL,
    chunk_size: int = 2_000,
    max_records: int | None = None,
) -> pd.DataFrame:
    """Load the Durham public crime table, downloading it once if needed.

    The cache is a gzip-compressed CSV, which is easy to inspect, portable, and
    substantially smaller than the ArcGIS JSON response. A sidecar JSON file
    records the source URL and row count.
    """

    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        return pd.read_csv(cache_path, low_memory=False)
    count_payload = _arcgis_json(
        url,
        {"where": "1=1", "returnCountOnly": "true", "f": "json"},
    )
    total = int(count_payload.get("count", 0))
    requested = min(total, max_records) if max_records is not None else total
    records: list[dict] = []
    for offset in range(0, requested, chunk_size):
        payload = _arcgis_json(
            url,
            {
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "false",
                "resultOffset": offset,
                "resultRecordCount": min(chunk_size, requested - offset),
                "orderByFields": "OBJECTID",
                "f": "json",
            },
        )
        features = payload.get("features", [])
        records.extend(feature.get("attributes", {}) for feature in features)
        if len(features) < chunk_size:
            break
    frame = pd.DataFrame(records)
    frame.to_csv(cache_path, index=False, compression="gzip")
    cache_path.with_suffix(cache_path.suffix + ".json").write_text(
        json.dumps(
            {
                "source_url": url,
                "source_count": total,
                "cached_count": len(frame),
                "cache_format": "gzip-compressed CSV",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return frame


def midpoint_address(value: object) -> str:
    """Turn a public ``*00`` block address into the block midpoint ``*50``."""

    text = "" if value is None else str(value).strip()
    if not text or text.upper() in {"NAN", "<NA>", "NONE"}:
        return ""
    digits = []
    for character in text:
        if character.isdigit():
            digits.append(character)
        else:
            break
    if not digits:
        return text
    number = int("".join(digits))
    if number >= 100 and number % 100 == 0:
        replacement = str(number + 50)
        return replacement + text[len(digits) :]
    return text


def make_durham_inputs(
    crime: pd.DataFrame,
    *,
    address_column: str = "ADDRESS2",
) -> pd.DataFrame:
    """Create GeoTIGER input columns from the public Durham table."""

    if address_column not in crime.columns:
        raise ValueError(f"Durham crime data is missing {address_column!r}")
    result = crime.copy()
    result["address"] = result[address_column].map(midpoint_address)
    result["city"] = "DURHAM"
    result["state"] = "NC"
    return result

