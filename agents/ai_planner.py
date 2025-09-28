# agents/ai_planner.py
from __future__ import annotations

import json
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

# ---------- small geo helpers ----------
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    from math import radians, sin, cos, asin, sqrt
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    lat1 = radians(lat1)
    lat2 = radians(lat2)
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

# ---------- ZIP -> lat/lon fallback (if watcher didn't set) ----------
_PGEOCODE_AVAILABLE = False
try:
    import pgeocode  # type: ignore
    _PGEOCODE_AVAILABLE = True
except Exception:
    _PGEOCODE_AVAILABLE = False

def _resolve_zip_latlon(zip_code: str) -> Optional[Tuple[float, float]]:
    """
    Resolve a US ZIP to (lat, lon) using pgeocode if available.
    Returns None if not resolvable.
    """
    if not _PGEOCODE_AVAILABLE:
        return None
    try:
        nomi = pgeocode.Nominatim("us")
        rec = nomi.query_postal_code(str(zip_code))
        lat = float(rec["latitude"])
        lon = float(rec["longitude"])
        if math.isnan(lat) or math.isnan(lon):
            return None
        return lat, lon
    except Exception:
        return None

# ---------- shelters IO ----------
class SheltersError(RuntimeError):
    pass

def _load_shelters(data_dir: str) -> List[Dict[str, Any]]:
    path = os.path.join(data_dir, "shelters.json")
    if not os.path.exists(path):
        raise SheltersError(f"shelters.json not found at {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise SheltersError(f"Invalid shelters.json: {e}")

    if not isinstance(data, list):
        raise SheltersError("shelters.json must be a JSON array of shelters")

    # expected minimal fields: name, lat, lon, open (bool)
    cleaned: List[Dict[str, Any]] = []
    for i, s in enumerate(data):
        if not isinstance(s, dict):
            continue
        try:
            name = str(s.get("name") or f"Shelter #{i+1}")
            lat = float(s["lat"])
            lon = float(s["lon"])
            is_open = bool(s.get("open", True))
        except Exception:
            # Skip badly formed entries
            continue
        cleaned.append({"name": name, "lat": lat, "lon": lon, "open": is_open})
    if not cleaned:
        raise SheltersError("No valid shelters in shelters.json")
    return cleaned

# ---------- ETA estimate ----------
def _estimate_eta_min(distance_km: float, category: str) -> int:
    """
    Very simple drive-time estimate based on conditions:
    - Base: 45 km/h
    - If CAT3+ => 30 km/h
    - If TS/CAT1 => 40 km/h
    """
    cat_str = (category or "").upper().replace("CATEGORY", "CAT")
    speed = 45.0
    try:
        # parse CATN
        if cat_str.startswith("CAT"):
            n = int(cat_str.replace("CAT", "").strip() or "0")
            speed = 30.0 if n >= 3 else 40.0
        elif "TS" in cat_str:
            speed = 40.0
    except Exception:
        pass
    speed = max(15.0, min(speed, 80.0))
    minutes = (distance_km / speed) * 60.0
    return max(1, int(round(minutes)))

# ---------- main planner ----------
def plan_nearest_open_shelter_from_state(
    state: Dict[str, Any],
    zip_code: str,
    data_dir: str,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Returns (plan, debug)
      plan: { name, lat, lon, distance_km, eta_min }
      debug: extra diagnostics for UI
    """
    dbg: Dict[str, Any] = {"planner": {}}

    # 1) Find origin
    zpt = state.get("zip_point") or {}
    if isinstance(zpt, dict) and "lat" in zpt and "lon" in zpt:
        zlat, zlon = float(zpt["lat"]), float(zpt["lon"])
        dbg["planner"]["origin"] = {"source": "zip_point", "lat": zlat, "lon": zlon}
    else:
        # fallback: resolve from zip
        coords = _resolve_zip_latlon(zip_code)
        if coords is None:
            dbg["planner"]["error"] = f"ZIP {zip_code} not resolvable"
            return None, dbg
        zlat, zlon = coords
        dbg["planner"]["origin"] = {"source": "pgeocode", "lat": zlat, "lon": zlon}

    # 2) Load shelters
    try:
        shelters = _load_shelters(data_dir)
        dbg["planner"]["shelters_count"] = len(shelters)
    except SheltersError as e:
        dbg["planner"]["error"] = str(e)
        return None, dbg

    # 3) Filter open + compute nearest
    open_shelters = [s for s in shelters if bool(s.get("open", True))]
    if not open_shelters:
        dbg["planner"]["error"] = "No open shelters"
        return None, dbg

    best = None
    best_d = 1e9
    for s in open_shelters:
        d = _haversine_km(zlat, zlon, float(s["lat"]), float(s["lon"]))
        if d < best_d:
            best_d = d
            best = s

    if not best:
        dbg["planner"]["error"] = "No nearest shelter found"
        return None, dbg

    # 4) ETA – use advisory category if present
    adv = state.get("advisory") or {}
    category = str(adv.get("category", ""))

    plan = {
        "name": best["name"],
        "lat": float(best["lat"]),
        "lon": float(best["lon"]),
        "distance_km": round(best_d, 1),
        "eta_min": _estimate_eta_min(best_d, category),
    }
    dbg["planner"]["selected"] = plan
    return plan, dbg


def run_planner_once(
    data_dir: str,
    zip_code: str,
    state: Dict[str, Any],
) -> Tuple[Dict[str, Any], float, Optional[str]]:
    """
    Convenience wrapper used by parallel pipeline.
    Returns: (outputs, elapsed_ms, error_msg)
      outputs -> {"plan": <plan>, "debug": {"planner": ...}}
    """
    t0 = time.perf_counter()
    outputs: Dict[str, Any] = {"debug": {}}
    err: Optional[str] = None

    try:
        plan, dbg = plan_nearest_open_shelter_from_state(state, zip_code, data_dir)
        outputs["debug"].update(dbg or {})
        if plan:
            outputs["plan"] = plan
        else:
            err = (dbg or {}).get("planner", {}).get("error") or "Planner produced no plan"
    except Exception as e:
        err = f"{type(e).__name__}: {e}"

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return outputs, elapsed_ms, err
