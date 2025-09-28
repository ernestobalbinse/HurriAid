# watcher.py
from typing import Dict, Optional
import math
from functools import lru_cache

import pgeocode
from tools.geo import haversine_km

# --- Config ---
FL_ONLY = True  # set False if you want any US ZIP allowed

# --- Category & buffers (unchanged) ---
CAT_ORDER = {"TS": 0, "CAT1": 1, "CAT2": 2, "CAT3": 3, "CAT4": 4, "CAT5": 5}

HIGH_EXTRA_BY_CAT = {
    0: 0,   # TS
    1: 0,   # CAT1
    2: 50,  # CAT2
    3: 60,  # CAT3
    4: 70,  # CAT4
    5: 80,  # CAT5
}

MEDIUM_BUFFER_BY_CAT = {
    0: 60,   # TS
    1: 80,   # CAT1
    2: 100,  # CAT2
    3: 120,  # CAT3
    4: 140,  # CAT4
    5: 160,  # CAT5
}

LOW_BUFFER_BY_CAT = {
    0: 120,  # TS
    1: 160,  # CAT1
    2: 200,  # CAT2
    3: 240,  # CAT3
    4: 280,  # CAT4
    5: 320,  # CAT5
}

# --- Helpers ---
_nom = pgeocode.Nominatim("us")

def _parse_cat(category: str) -> int:
    return CAT_ORDER.get((category or "TS").upper(), 0)

def _norm_zip(z: str) -> str:
    z = str(z or "").strip()
    # strip ZIP+4 if present
    if "-" in z:
        z = z.split("-", 1)[0]
    return z.zfill(5) if z.isdigit() else z

def _isnumber(x) -> bool:
    try:
        return isinstance(x, (int, float)) and not math.isnan(float(x))
    except Exception:
        return False

@lru_cache(maxsize=5000)
def _lookup_zip_with_pgeocode(z: str) -> Dict:
    rec = _nom.query_postal_code(z)
    lat = getattr(rec, "latitude", float("nan"))
    lon = getattr(rec, "longitude", float("nan"))
    state_code = getattr(rec, "state_code", "") or ""
    if not (_isnumber(lat) and _isnumber(lon)):
        raise ValueError(f"Unknown ZIP: {z}")
    if FL_ONLY and state_code != "FL":
        raise ValueError(f"ZIP {z} is not in Florida (state_code={state_code or 'NA'}).")
    return {"lat": float(lat), "lon": float(lon), "state": state_code, "zip": z}

def _get_centroid(zip_code: str, zip_centroids: Optional[Dict]) -> Dict:
    """Try provided dict first; if missing, fall back to pgeocode."""
    z = _norm_zip(zip_code)
    # 1) Provided dict (if any)
    if isinstance(zip_centroids, dict) and z in zip_centroids:
        pt = zip_centroids[z]
        lat, lon = pt.get("lat"), pt.get("lon")
        if _isnumber(lat) and _isnumber(lon):
            return {"lat": float(lat), "lon": float(lon), "zip": z, "state": pt.get("state") or "FL"}
        # if dict entry is malformed, fall through to pgeocode
    # 2) pgeocode fallback
    return _lookup_zip_with_pgeocode(z)

# --- Public: assess_risk (keeps your original logic) ---
def assess_risk(zip_code: str, advisory: Dict, zip_centroids: Optional[Dict]) -> Dict:
    """
    Returns dict: {risk, reason, distance_km}
    Levels (nearest-first):
      - HIGH: inside radius OR within HIGH_EXTRA_BY_CAT (CAT2+ gets extra buffer)
      - MEDIUM: within radius + MEDIUM_BUFFER_BY_CAT
      - LOW: within radius + LOW_BUFFER_BY_CAT
      - SAFE: otherwise
    Uses provided zip_centroids when available; otherwise falls back to pgeocode.
    """
    try:
        z = _get_centroid(zip_code, zip_centroids)
    except Exception as e:
        return {"risk": "ERROR", "reason": str(e)}

    center = advisory.get("center", {"lat": 0.0, "lon": 0.0})
    radius = float(advisory.get("radius_km", 0) or 0.0)
    cat = _parse_cat(advisory.get("category"))
    dist_km = haversine_km(z["lat"], z["lon"], center["lat"], center["lon"])

    # HIGH zones
    if dist_km <= radius:
        return {
            "risk": "HIGH",
            "distance_km": dist_km,
            "reason": f"Inside advisory radius (dist={dist_km:.1f} km ≤ {radius:.1f} km)."
        }
    high_extra = HIGH_EXTRA_BY_CAT.get(cat, 0)
    if high_extra > 0 and dist_km <= radius + high_extra:
        return {
            "risk": "HIGH",
            "distance_km": dist_km,
            "reason": f"Within HIGH buffer {high_extra} km for {advisory.get('category','TS')}."
        }

    # MEDIUM band
    med_buf = MEDIUM_BUFFER_BY_CAT.get(cat, 80)
    if dist_km <= radius + med_buf:
        return {
            "risk": "MEDIUM",
            "distance_km": dist_km,
            "reason": f"Within MEDIUM buffer {med_buf} km for {advisory.get('category','TS')}."
        }

    # LOW band
    low_buf = LOW_BUFFER_BY_CAT.get(cat, 160)
    if dist_km <= radius + low_buf:
        return {
            "risk": "LOW",
            "distance_km": dist_km,
            "reason": f"Within LOW buffer {low_buf} km for {advisory.get('category','TS')}."
        }

    # Beyond all buffers = SAFE
    return {
        "risk": "SAFE",
        "distance_km": dist_km,
        "reason": f"Outside all buffers (dist={dist_km:.1f} km)."
    }

# --- Optional helper for UI map pin (works even without prebuilt dict) ---
def get_zip_point(zip_code: str, zip_centroids: Optional[Dict] = None) -> Optional[Dict]:
    try:
        pt = _get_centroid(zip_code, zip_centroids)
        return {"lat": pt["lat"], "lon": pt["lon"]}
    except Exception:
        return None
