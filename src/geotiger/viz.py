"""Small analyst-facing Folium helpers."""

from __future__ import annotations

import folium
import pandas as pd


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
        popup = "<br>".join(
            f"<b>{label}</b>: {row.get(column, '')}"
            for label, column in (
                ("Status", "match_status"),
                ("Score", "score"),
                ("Address", "matched_street_norm"),
                ("ZIP", "matched_zip5"),
            )
            if column in row.index
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
