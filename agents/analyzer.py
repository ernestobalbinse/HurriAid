# agents/analyzer.py
from typing import Dict
from tools.geo import haversine_km

CAT_ORDER = {"TS": 0, "CAT1": 1, "CAT2": 2, "CAT3": 3, "CAT4": 4, "CAT5": 5}

def _parse_cat(category: str) -> int:
    return CAT_ORDER.get(category.upper(), 0)

def assess_risk(zip_code: str, advisory: Dict, zip_centroids: Dict) -> Dict:
    # If ZIP is unknown, return ERROR (do not assume a risk)
    if zip_code not in zip_centroids:
        return {"risk": "ERROR", "reason": "Unknown ZIP code — cannot assess risk."}

    z = zip_centroids[zip_code]
    center = advisory["center"]
    dist_km = haversine_km(z["lat"], z["lon"], center["lat"], center["lon"])
    radius = float(advisory.get("radius_km", 0))
    cat = _parse_cat(advisory.get("category", "TS"))

    # Heuristics from the brief
    if dist_km <= radius:
        risk = "HIGH"
        reason = f"Inside advisory radius (dist={dist_km:.1f} km ≤ {radius:.1f} km)."
    elif cat >= 2 and dist_km <= radius + 50:
        risk = "HIGH"
        reason = f"Within 50 km buffer at CAT2+ (dist={dist_km:.1f} km)."
    elif dist_km <= radius + 120:
        risk = "MEDIUM"
        reason = f"Within 120 km buffer (dist={dist_km:.1f} km)."
    else:
        risk = "LOW"
        reason = f"Outside buffers (dist={dist_km:.1f} km)."

    return {"risk": risk, "distance_km": dist_km, "reason": reason}
