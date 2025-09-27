# tools/zip_resolver.py
from __future__ import annotations
import pgeocode
import math

_nom = pgeocode.Nominatim("us")

class ZipNotFound(Exception): ...
class ZipNotInFlorida(Exception): ...

def resolve_fl_zip(zip_code: str) -> dict:
    """Return {'lat': float, 'lon': float} for any valid Florida ZIP.
    Raises ZipNotFound or ZipNotInFlorida on failure.
    """
    z = str(zip_code).strip()[:5]
    if not z.isdigit() or len(z) != 5:
        raise ZipNotFound(f"Invalid ZIP format: {zip_code}")

    rec = _nom.query_postal_code(z)
    if rec is None or math.isnan(rec.latitude) or math.isnan(rec.longitude):
        raise ZipNotFound(f"Unknown ZIP: {zip_code}")

    # pgeocode uses two-letter state_code (e.g., 'FL')
    if (rec.state_code or "").upper() != "FL":
        raise ZipNotInFlorida(f"{zip_code} is not in Florida (state={rec.state_code})")

    return {"lat": float(rec.latitude), "lon": float(rec.longitude)}
