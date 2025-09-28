# tools/geo.py
from __future__ import annotations
from math import radians, sin, cos, asin, sqrt, pi
from typing import List

# A tiny geometry helper module used by the UI.
# Goal: fast, readable helpers—no fallbacks, no magic. Just plain math.


KM_PER_DEG_LAT = 111.32  # ~km per degree of latitude (good enough for small radii)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Great-circle distance between two WGS84 points, in kilometers.

    Why this exists: we just need a quick, dependable distance for the map and ETA.
    This is the standard haversine formula—accurate enough for our use case.
    """
    R = 6371.0  # mean Earth radius in km
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


def circle_polygon(lat: float, lon: float, radius_km: float, num_points: int = 72) -> List[List[float]]:
    """
    Build a small-circle polygon around (lat, lon) with an approximate radius in km.

    What it returns:
      A list of [lon, lat] vertices suitable for deck.gl/pydeck PolygonLayer.
      The polygon is "closed" (first point repeated at the end).

    How it works:
      For small radii, we can treat degrees-per-kilometer as roughly constant at the
      given latitude. We convert a circle in km into deltas in degrees and walk
      around 360°.

    Notes:
      - We clamp the longitude scaling near the poles to avoid division by zero.
      - If radius_km <= 0, we return a degenerate 2-point "polygon".
    """
    if radius_km <= 0:
        return [[lon, lat], [lon, lat]]

    cos_lat = abs(cos(radians(lat)))
    deg_lat_per_km = 1.0 / KM_PER_DEG_LAT
    deg_lon_per_km = 1.0 / (KM_PER_DEG_LAT * max(cos_lat, 1e-9))

    pts: List[List[float]] = []
    for i in range(num_points):
        theta = 2.0 * pi * (i / num_points)
        dlat = radius_km * sin(theta) * deg_lat_per_km
        dlon = radius_km * cos(theta) * deg_lon_per_km
        pts.append([lon + dlon, lat + dlat])

    # Close the polygon
    pts.append(pts[0])
    return pts
