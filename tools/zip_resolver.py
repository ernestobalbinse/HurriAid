# tools/zip_resolver.py
from __future__ import annotations
import math
from typing import Tuple, Dict

import pgeocode

# Single, clear purpose: turn a 5-digit U.S. ZIP into map-ready coordinates.
# We use pgeocode’s centroid data — it’s fast, dependable, and plenty accurate
# for distance/ETA estimates and drawing the advisory circle on the map.

_nom = pgeocode.Nominatim("us")


class ZipNotFound(Exception):
    """Raised when a ZIP code is invalid or not found in the dataset."""


def resolve_zip_latlon(zip_code: str) -> Tuple[float, float]:
    """
    Return (lat, lon) for a 5-digit U.S. ZIP code.

    What this does:
      - Validates the input looks like a ZIP (exactly 5 digits).
      - Queries pgeocode and returns the centroid in WGS84 degrees.

    When it fails:
      - Raises ZipNotFound if the ZIP is malformed or unknown.

    Example:
      >>> resolve_zip_latlon("33101")
      (25.77..., -80.19...)
    """
    z = str(zip_code).strip()[:5]
    if not z.isdigit() or len(z) != 5:
        raise ZipNotFound(f"Invalid ZIP format: {zip_code!r}")

    rec = _nom.query_postal_code(z)
    try:
        lat = float(rec.latitude)
        lon = float(rec.longitude)
    except Exception:
        raise ZipNotFound(f"Unknown ZIP: {zip_code!r}")

    if math.isnan(lat) or math.isnan(lon):
        raise ZipNotFound(f"Unknown ZIP: {zip_code!r}")

    return lat, lon


def resolve_fl_zip(zip_code: str) -> Dict[str, float]:
    """
    Florida-only convenience helper kept for compatibility with older code.

    Returns:
      {'lat': float, 'lon': float} for valid Florida ZIPs.

    Raises:
      ZipNotFound if the ZIP is invalid, unknown, or not in Florida.
    """
    z = str(zip_code).strip()[:5]
    if not z.isdigit() or len(z) != 5:
        raise ZipNotFound(f"Invalid ZIP format: {zip_code!r}")

    rec = _nom.query_postal_code(z)
    if (
        rec is None
        or not isinstance(rec.state_code, str)
        or rec.state_code.upper() != "FL"
        or math.isnan(rec.latitude)
        or math.isnan(rec.longitude)
    ):
        raise ZipNotFound(f"Not a Florida ZIP: {zip_code!r}")

    return {"lat": float(rec.latitude), "lon": float(rec.longitude)}
