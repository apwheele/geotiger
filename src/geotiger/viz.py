"""Small analyst-facing Folium helpers."""

from __future__ import annotations

from html import escape
from numbers import Real

import folium
import pandas as pd


def _display_text(value) -> str:
    """Format a nullable scalar for an analyst-facing popup."""

    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, Real) and float(value).is_integer():
        return str(int(value))
    return str(value).strip()


def _first_text(row: pd.Series, *columns: str) -> str:
    for column in columns:
        if column in row.index:
            value = _display_text(row.get(column))
            if value:
                return value
    return ""


def _matched_address(row: pd.Series) -> str:
    """Build a full one-line address from matched and input components."""

    house_number = _first_text(row, "matched_house_number")
    street = _first_text(
        row,
        "matched_street_norm",
        "matched_intersection_key",
        "street_norm",
        "raw_address",
    ).replace(" || ", " & ")
    address_line = " ".join(value for value in (house_number, street) if value)
    city = _first_text(row, "matched_city", "city", "city_parsed")
    state = _first_text(row, "matched_state", "state", "state_parsed")
    zip5 = _first_text(row, "matched_zip5", "zip5", "zip", "zip5_parsed")
    state_zip = " ".join(value for value in (state, zip5) if value)
    locality = ", ".join(value for value in (city, state_zip) if value)
    return ", ".join(value for value in (address_line, locality) if value)


def matches_map(
    matches: pd.DataFrame,
    *,
    tiles: str | None = None,
    zoom_start: int = 12,
    color_by: str = "match_status",
) -> folium.Map:
    """Create a result map.

    ``tiles=None`` is the privacy-preserving default: the HTML has no tile
    service configured and can be viewed entirely offline. Pass a tile name or
    URL only when an online basemap is acceptable.
    """

    valid = matches.dropna(subset=["match_latitude", "match_longitude"]).copy()
    if len(valid):
        center = [float(valid.match_latitude.mean()), float(valid.match_longitude.mean())]
    else:
        center = [39.5, -98.35]
    fmap = folium.Map(location=center, zoom_start=zoom_start, tiles=tiles, control_scale=True)
    colors = {"matched": "green", "review": "orange", "unmatched": "red"}
    for _, row in valid.iterrows():
        status = str(row.get(color_by, "matched"))
        popup_values = (
            ("Address", _matched_address(row)),
            ("Status", _first_text(row, "match_status")),
            ("Score", _first_text(row, "score")),
            ("Method", _first_text(row, "match_method")),
        )
        popup = "<br>".join(
            f"<b>{escape(label)}</b>: {escape(value)}"
            for label, value in popup_values
            if value
        )
        folium.CircleMarker(
            location=[float(row.match_latitude), float(row.match_longitude)],
            radius=5,
            color=colors.get(status, "blue"),
            fill=True,
            fill_opacity=0.75,
            popup=folium.Popup(popup, max_width=350),
        ).add_to(fmap)
    return fmap


def matches_static_map(
    matches: pd.DataFrame,
    *,
    ax=None,
    figsize: tuple[float, float] = (8, 8),
    color_by: str = "match_status",
    point_size: float = 28,
):
    """Plot matched coordinates as a static, fully offline Matplotlib map.

    The result is an ``Axes`` object so analysts can add local boundaries,
    roads, or export it with ``ax.figure.savefig(...)``. No basemap or network
    request is used.
    """

    import matplotlib.pyplot as plt

    valid = matches.dropna(subset=["match_latitude", "match_longitude"]).copy()
    if ax is None:
        _, ax = plt.subplots(figsize=figsize)
    colors = {"matched": "#2ca02c", "review": "#ff7f0e", "unmatched": "#d62728"}
    if len(valid):
        for status, group in valid.groupby(color_by, dropna=False):
            label = str(status) if pd.notna(status) else "unknown"
            ax.scatter(
                group["match_longitude"],
                group["match_latitude"],
                s=point_size,
                alpha=0.8,
                color=colors.get(label, "#1f77b4"),
                label=label,
            )
        ax.legend(title=color_by)
        ax.set_aspect("equal", adjustable="datalim")
    else:
        ax.text(0.5, 0.5, "No geocoded coordinates", ha="center", va="center")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("GeoTIGER geocoded results")
    ax.grid(True, alpha=0.25)
    return ax
