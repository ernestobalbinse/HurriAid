# agents/parallel_pipeline.py
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional, Tuple, List

from agents.ai_checklist import make_checklist_from_state

# --- simple geo util (copy from watcher) ---
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import radians, sin, cos, asin, sqrt
    R = 6371.0
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlmb = radians(lon2 - lon1)
    a = sin(dphi/2)**2 + cos(phi1)*cos(phi2)*sin(dlmb/2)**2
    return 2 * R * asin(sqrt(a))

def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _plan_nearest_open_shelter(data_dir: str, state: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    """Load shelters.json and find nearest 'open' shelter to state['zip_point']."""
    dbg: Dict[str, Any] = {}
    zpt = state.get("zip_point") or {}
    if not isinstance(zpt.get("lat"), (int, float)) or not isinstance(zpt.get("lon"), (int, float)):
        return None, {"reason": "NO_ZIP_POINT"}, "NO_ZIP_POINT"

    zlat, zlon = float(zpt["lat"]), float(zpt["lon"])
    path = os.path.join(data_dir, "shelters.json")
    dbg["path"] = path
    try:
        payload = _read_json(path)
    except Exception as e:
        return None, {"path": path, "error": str(e)}, "READ_ERROR"

    shelters = payload if isinstance(payload, list) else payload.get("shelters", [])
    best = None
    best_d = 1e9

    for s in shelters or []:
        try:
            if not s.get("open", True):
                continue
            lat = float(s["lat"])
            lon = float(s["lon"])
            d = _haversine_km(zlat, zlon, lat, lon)
            if d < best_d:
                best_d = d
                best = {
                    "name": s.get("name", "Shelter"),
                    "lat": lat,
                    "lon": lon,
                    "distance_km": round(d, 1),
                    # naive ETA: ~1 min per km at 60 km/h
                    "eta_min": int(round(d))
                }
        except Exception:
            continue

    if not best:
        return None, {"reason": "NO_OPEN_SHELTERS"}, "NO_OPEN_SHELTERS"
    return best, dbg, None

def run_parallel_once(data_dir: str, zip_code: str, state: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, float]]:
    """
    Returns (updated_state, par_timings) so Coordinator can do:
        state, par_timings = run_parallel_once(...)
        state["timings_ms"].update(par_timings)
    """
    # be defensive about the incoming state structure
    if not isinstance(state, dict):
        state = {}
    state.setdefault("errors", {})
    state.setdefault("debug", {})
    state.setdefault("timings_ms", {})

    # --- AI Checklist ---
    t0 = time.perf_counter()
    items, dbg, err = make_checklist_from_state(state, zip_code)
    state["debug"]["checklist"] = dbg
    if err:
        state["errors"]["checklist"] = err
    if items:
        state["checklist"] = items
    checklist_ms = (time.perf_counter() - t0) * 1000.0

    # --- Shelter planner ---
    t1 = time.perf_counter()
    plan, pdebug, perr = _plan_nearest_open_shelter(data_dir, state)
    state["debug"]["planner"] = pdebug
    if perr:
        state["errors"]["planner"] = perr
    if plan:
        state["plan"] = plan
    planner_ms = (time.perf_counter() - t1) * 1000.0

    par_timings = {
        "checklist_ms": checklist_ms,
        "planner_ms": planner_ms,
        "parallel_ms": checklist_ms + planner_ms,
    }
    return state, par_timings
