# agents/ai_planner.py
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

# ------------------------------------------------------------
# What this file does (in plain English)
# ------------------------------------------------------------
# Given:
#   - the user's origin point in state["zip_point"] (lat/lon),
#   - and a shelters.json file (list of shelters with lat/lon/open),
# we pick the nearest OPEN shelter, compute the distance (miles),
# and estimate a simple drive-time ETA (minutes) based on storm category.
#
# We intentionally avoid extra fallbacks (like ad-hoc geocoding) to keep
# the logic predictable. If the watcher/AI didn’t set zip_point, we fail
# fast with a clear message.


# ----------------------------- small geo helpers -----------------------------
def _haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Great-circle distance between two points (miles).
    """
    R_MI = 3958.7613  # Earth radius in miles
    from math import radians, sin, cos, asin, sqrt

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    lat1r = radians(lat1)
    lat2r = radians(lat2)

    a = sin(dlat / 2) ** 2 + cos(lat1r) * cos(lat2r) * sin(dlon / 2) ** 2
    return 2 * R_MI * asin(sqrt(a))


# ------------------------------- shelters IO --------------------------------
class SheltersError(RuntimeError):
    pass


def _load_shelters(data_dir: str) -> List[Dict[str, Any]]:
    """
    Read shelters.json and return a cleaned list:
      [{"name": str, "lat": float, "lon": float, "open": bool}, ...]
    We skip malformed entries rather than guess.
    """
    path = os.path.join(data_dir, "shelters.json")
    if not os.path.exists(path):
        raise SheltersError(f"shelters.json not found at {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        raise SheltersError(f"Invalid shelters.json: {e}")

    if not isinstance(raw, list):
        raise SheltersError("shelters.json must be a JSON array of shelters")

    cleaned: List[Dict[str, Any]] = []
    for idx, s in enumerate(raw):
        if not isinstance(s, dict):
            continue
        try:
            name = str(s.get("name") or f"Shelter #{idx+1}")
            lat = float(s["lat"])
            lon = float(s["lon"])
            is_open = bool(s.get("open", True))
        except Exception:
            # Skip any entry we can't trust
            continue
        cleaned.append({"name": name, "lat": lat, "lon": lon, "open": is_open})

    if not cleaned:
        raise SheltersError("No valid shelters found in shelters.json")

    return cleaned


# ----------------------------- simple ETA model -----------------------------
def _estimate_eta_min(distance_mi: float, category: str) -> int:
    """
    Ballpark drive-time in minutes using conservative speeds (mph).
      - Base: ~28 mph
      - TS / CAT1: ~25 mph
      - CAT3 or higher: ~18 mph

    We keep it simple and pessimistic—safer for planning.
    """
    cat = (category or "").upper().replace("CATEGORY", "CAT")
    speed_mph = 28.0
    try:
        if cat.startswith("CAT"):
            n = int((cat.replace("CAT", "").strip() or "0"))
            speed_mph = 18.0 if n >= 3 else 25.0
        elif "TS" in cat:
            speed_mph = 25.0
    except Exception:
        pass

    # Bound speeds to something reasonable
    speed_mph = max(10.0, min(speed_mph, 70.0))
    minutes = (distance_mi / speed_mph) * 60.0
    return max(1, int(round(minutes)))


# ------------------------------- main planner -------------------------------
def plan_nearest_open_shelter_from_state(
    state: Dict[str, Any],
    zip_code: str,     # kept for interface parity; not used here
    data_dir: str,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Returns (plan, debug)

    plan:
      {
        "name": str,
        "lat": float,
        "lon": float,
        "distance_mi": float,
        "eta_min": int
      }

    debug: extra diagnostics under {"planner": {...}} for the UI.
    """
    dbg: Dict[str, Any] = {"planner": {}}

    # 1) Find origin (must be set by watcher/AI)
    zpt = state.get("zip_point")
    if not (isinstance(zpt, dict) and "lat" in zpt and "lon" in zpt):
        dbg["planner"]["error"] = (
            "Origin not available. Expected state['zip_point'] with 'lat' and 'lon'."
        )
        return None, dbg

    try:
        zlat, zlon = float(zpt["lat"]), float(zpt["lon"])
    except Exception:
        dbg["planner"]["error"] = "Origin lat/lon could not be parsed as numbers."
        return None, dbg

    dbg["planner"]["origin"] = {"source": "zip_point", "lat": zlat, "lon": zlon}

    # 2) Load shelters
    try:
        shelters = _load_shelters(data_dir)
        dbg["planner"]["shelters_count"] = len(shelters)
    except SheltersError as e:
        dbg["planner"]["error"] = str(e)
        return None, dbg

    # 3) Filter to open shelters and pick the closest one
    open_shelters = [s for s in shelters if bool(s.get("open", True))]
    if not open_shelters:
        dbg["planner"]["error"] = "No open shelters are available."
        return None, dbg

    best: Optional[Dict[str, Any]] = None
    best_d: float = float("inf")
    for s in open_shelters:
        d = _haversine_mi(zlat, zlon, float(s["lat"]), float(s["lon"]))
        if d < best_d:
            best_d = d
            best = s

    if not best:
        dbg["planner"]["error"] = "Could not select a nearest shelter."
        return None, dbg

    # 4) Build plan with a conservative ETA
    adv = state.get("advisory") or {}
    category = str(adv.get("category", ""))

    plan = {
        "name": best["name"],
        "lat": float(best["lat"]),
        "lon": float(best["lon"]),
        "distance_mi": round(best_d, 1),
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
    Thin wrapper used by the parallel pipeline.

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
            err = (dbg or {}).get("planner", {}).get("error") or "Planner produced no plan."
    except Exception as e:
        err = f"{type(e).__name__}: {e}"

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return outputs, elapsed_ms, err
