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

