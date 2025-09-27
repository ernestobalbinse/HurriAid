from typing import Dict
from tools.geo import haversine_km

CAT_ORDER = {"TS": 0, "CAT1": 1, "CAT2": 2, "CAT3": 3, "CAT4": 4, "CAT5": 5}

def _parse_cat(category: str) -> int:
	return CAT_ORDER.get((category or "TS").upper(), 0)

def assess_risk(zip_code: str, advisory: Dict, zip_centroids: Dict) -> Dict:
	"""Compute deterministic risk for a given ZIP.
	Returns a dict with keys: risk, reason, and optionally distance_km.
	- ERROR if ZIP unknown or malformed (we don't assume LOW).
	- HIGH if inside advisory radius OR within +50 km AND category ≥ CAT2.
	- MEDIUM if within radius + 120 km buffer.
	- LOW otherwise.
	"""
	# Guard: unknown zip
	if zip_code not in zip_centroids:
		return {"risk": "ERROR", "reason": "Unknown ZIP code — cannot assess risk."}

	z = zip_centroids[zip_code]
	center = advisory.get("center", {"lat": 0.0, "lon": 0.0})
	radius = float(advisory.get("radius_km", 0))
	cat = _parse_cat(advisory.get("category"))

	dist_km = haversine_km(z["lat"], z["lon"], center["lat"], center["lon"])

	if dist_km <= radius:
		return {
			"risk": "HIGH",
			"distance_km": dist_km,
			"reason": f"Inside advisory radius (dist={dist_km:.1f} km ≤ {radius:.1f} km)."
		}
	if cat >= 2 and dist_km <= radius + 50:
		return {
			"risk": "HIGH",
			"distance_km": dist_km,
			"reason": f"Within 50 km buffer at CAT2+ (dist={dist_km:.1f} km)."
	}
	if dist_km <= radius + 120:
		return {
			"risk": "MEDIUM",
			"distance_km": dist_km,
			"reason": f"Within 120 km buffer (dist={dist_km:.1f} km)."
	}
	return {
		"risk": "LOW",
		"distance_km": dist_km,
		"reason": f"Outside buffers (dist={dist_km:.1f} km)."
	}