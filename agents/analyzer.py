from typing import Dict
from tools.geo import haversine_km

CAT_ORDER = {"TS": 0, "CAT1": 1, "CAT2": 2, "CAT3": 3, "CAT4": 4, "CAT5": 5}

# Buffers beyond the advisory radius, per Saffir–Simpson category
# HIGH: extra zone where strong storms count as HIGH even just outside radius
HIGH_EXTRA_BY_CAT = {
    0: 0,   # TS
    1: 0,   # CAT1
    2: 50,  # CAT2
    3: 60,  # CAT3
    4: 70,  # CAT4
    5: 80,  # CAT5
}

# MEDIUM: caution band after HIGH
MEDIUM_BUFFER_BY_CAT = {
    0: 60,   # TS
    1: 80,   # CAT1
    2: 100,  # CAT2
    3: 120,  # CAT3
    4: 140,  # CAT4
    5: 160,  # CAT5
}

# LOW: extended, softer caution band after MEDIUM
LOW_BUFFER_BY_CAT = {
    0: 120,  # TS
    1: 160,  # CAT1
    2: 200,  # CAT2
    3: 240,  # CAT3
    4: 280,  # CAT4
    5: 320,  # CAT5
}

def _parse_cat(category: str) -> int:
    return CAT_ORDER.get((category or "TS").upper(), 0)

def assess_risk(zip_code: str, advisory: Dict, zip_centroids: Dict) -> Dict:
    """
    Returns dict: {risk, reason, distance_km}
    Levels (nearest-first):
      - HIGH: inside radius OR within HIGH_EXTRA_BY_CAT (CAT2+ gets extra buffer)
      - MEDIUM: within radius + MEDIUM_BUFFER_BY_CAT
      - LOW: within radius + LOW_BUFFER_BY_CAT
      - SAFE: otherwise
    """
    if zip_code not in zip_centroids:
        return {"risk": "ERROR", "reason": "Unknown ZIP code — cannot assess risk."}

    z = zip_centroids[zip_code]
    center = advisory.get("center", {"lat": 0.0, "lon": 0.0})
    radius = float(advisory.get("radius_km", 0))
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
