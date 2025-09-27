from typing import Dict, List, Tuple, Optional
from tools.geo import haversine_km

DRIVING_SPEED_KMPH = 50.0  # simple demo assumption

def _eta_minutes(distance_km: float) -> int:
    return int(round((distance_km / DRIVING_SPEED_KMPH) * 60))

def nearest_open_shelter(zip_code: str, zip_centroids: Dict, shelters: List[Dict]) -> Optional[Dict]:
    # Unknown ZIP -> no plan
    if zip_code not in zip_centroids:
        return None
    
    z = zip_centroids[zip_code]
    best: Tuple[float, Dict] | None = None

    for s in shelters:
        if not s.get("is_open", False):
            continue
        d = haversine_km(z["lat"], z["lon"], s["lat"], s["lon"])
        if best is None or d < best[0]:
            best = (d, s)

    if best is None:
        return None
    
    distance_km, shelter = best
    return {
        "name": shelter["name"],
        "distance_km": distance_km,
        "eta_min": _eta_minutes(distance_km),
        "lat": shelter["lat"],
        "lon": shelter["lon"],
    }