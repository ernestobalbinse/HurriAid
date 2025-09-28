# agents/planner.py
from __future__ import annotations
import json, os, math
from typing import Dict, Any, Optional, List

__all__ = ["plan_nearest_open_shelter"]

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def _load_shelters(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def plan_nearest_open_shelter(
    zip_point: Optional[Dict[str, float]],
    data_dir: str,
    driving_kmh: float = 45.0
) -> Optional[Dict[str, Any]]:
    """
    Returns the nearest OPEN shelter and ETA from zip_point.
    Expects data/shelters.json entries like:
    [
      {"name":"Miami Central High Gym","lat":25.835,"lon":-80.230,"is_open":true},
      ...
    ]
    """
    if not zip_point:
        return None

    zlat, zlon = float(zip_point["lat"]), float(zip_point["lon"])
    shelters_path = os.path.join(data_dir, "shelters.json")
    shelters = _load_shelters(shelters_path)
    open_shelters = [s for s in shelters if s.get("is_open", False)]
    if not open_shelters:
        return None

    best = None
    for s in open_shelters:
        slat, slon = float(s["lat"]), float(s["lon"])
        dist = _haversine_km(zlat, zlon, slat, slon)
        if best is None or dist < best["distance_km"]:
            best = {
                "name": s["name"],
                "lat": slat,
                "lon": slon,
                "distance_km": round(dist, 1),
            }

    eta_min = int(max(2, round(best["distance_km"] / driving_kmh * 60)))
    best["eta_min"] = eta_min
    return best
