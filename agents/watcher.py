# agents/watcher.py
from __future__ import annotations

import json
import os
import math
import time
from typing import Dict, Any, Tuple, Optional

# ---- LLM explainer (soft dep; app still works with fallback) ----
from agents.ai_explainer import build_risk_explainer_agent

# ---- pgeocode for ZIP -> lat/lon ----
try:
    import pgeocode  # pip install pgeocode
    _PGEOCODE_AVAILABLE = True
except Exception:
    _PGEOCODE_AVAILABLE = False

# ---------- Helpers ----------

def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _resolve_zip_from_helper(zip_code: str) -> Optional[Tuple[float, float]]:
    """Use project helper if present."""
    try:
        from tools.zip_resolver import resolve_zip_latlon  # optional helper
        lat, lon = resolve_zip_latlon(zip_code)
        if lat is None or lon is None:
            return None
        return float(lat), float(lon)
    except Exception:
        return None

_geocoder = None
def _get_geocoder():
    global _geocoder
    if _geocoder is None and _PGEOCODE_AVAILABLE:
        _geocoder = pgeocode.Nominatim("us")
    return _geocoder

def _resolve_zip_latlon(zip_code: str) -> Optional[Tuple[float, float]]:
    # 1) project helper
    coords = _resolve_zip_from_helper(zip_code)
    if coords is not None:
        return coords
    # 2) pgeocode
    if not _PGEOCODE_AVAILABLE:
        return None
    try:
        nomi = _get_geocoder()
        rec = nomi.query_postal_code(str(zip_code))
        lat, lon = float(rec["latitude"]), float(rec["longitude"])
        if math.isnan(lat) or math.isnan(lon):
            return None
        return lat, lon
    except Exception:
        return None

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

def _cat_rank(cat: str) -> int:
    if not cat:
        return 0
    s = str(cat).upper().replace("CATEGORY", "CAT").replace(" ", "")
    if s.startswith("CAT"):
        try:
            return max(1, int(s.replace("CAT", "")))
        except Exception:
            return 1
    if "DEPRESSION" in s or s == "TD":
        return 0
    if "TS" in s or "STORM" in s:
        return 1
    return 0

def _risk_heuristic(dist_km: float, radius_km: float, category: str) -> str:
    # HIGH if inside radius OR (within 50 km at CAT2+)
    # MEDIUM if within (radius + 120 km) OR inside at TS/CAT1
    # LOW otherwise
    cat = _cat_rank(category)
    inside = dist_km <= float(radius_km)
    if inside or (dist_km <= 50.0 and cat >= 2):
        return "HIGH"
    if inside or dist_km <= (float(radius_km) + 120.0) or (inside and cat <= 1):
        return "MEDIUM"
    return "LOW"

def _fmt_watch_text(zip_code: str, risk: str, dist_km: float, inside: bool, radius_km: float) -> str:
    where = "Inside" if inside else "Outside"
    return (
        f"Risk ZIP: {zip_code}\n"
        f"Risk: {risk}\n"
        f"Distance to storm center: {dist_km:.1f} km\n"
        f"Advisory area: {where} (radius ≈ {float(radius_km):.1f} km)"
    )

# ---------- Functional "steps" (Streamlit-safe, no ADK InvocationContext) ----------

def _step_read_advisory(state: Dict[str, Any], data_dir: str, timings: Dict[str, float]) -> None:
    t0 = time.perf_counter()
    path = os.path.join(data_dir, "sample_advisory.json")
    try:
        adv = _load_json(path)
    except Exception:
        adv = {}

    center = (adv.get("center") or {})
    adv_norm = {
        "center": {
            "lat": float(center.get("lat", 25.77)),
            "lon": float(center.get("lon", -80.19)),
        },
        "radius_km": float(adv.get("radius_km", 100.0)),
        "category": adv.get("category", "TS"),
        "issued_at": adv.get("issued_at", ""),
        "active": bool(adv.get("active", True)),
    }
    state["advisory"] = adv_norm
    state["active"] = adv_norm["active"]
    timings["watcher_ms_read"] = (time.perf_counter() - t0) * 1000.0

def _step_analyze_risk(state: Dict[str, Any], zip_code: str, timings: Dict[str, float]) -> None:
    t0 = time.perf_counter()
    adv = state.get("advisory") or {}

    if not adv:
        state["analysis"] = {"risk": "ERROR", "reason": "No advisory data"}
        timings["watcher_ms_analyze"] = (time.perf_counter() - t0) * 1000.0
        return

    coords = _resolve_zip_latlon(zip_code)
    if coords is None:
        reason = "pgeocode not installed" if not _PGEOCODE_AVAILABLE else f"Unknown ZIP {zip_code}"
        state["analysis"] = {"risk": "ERROR", "reason": reason}
        timings["watcher_ms_analyze"] = (time.perf_counter() - t0) * 1000.0
        return

    zlat, zlon = coords
    clat, clon = float(adv["center"]["lat"]), float(adv["center"]["lon"])
    dist_km = _haversine_km(zlat, zlon, clat, clon)
    radius_km = float(adv["radius_km"])
    inside = dist_km <= radius_km
    risk = _risk_heuristic(dist_km, radius_km, str(adv.get("category", "")))

    state["zip_point"] = {"lat": zlat, "lon": zlon}
    state["analysis"] = {"risk": risk, "distance_km": round(dist_km, 1)}
    state["watcher_text"] = _fmt_watch_text(zip_code, risk, dist_km, inside, radius_km)
    timings["watcher_ms_analyze"] = (time.perf_counter() - t0) * 1000.0

def _step_explain_risk(state: Dict[str, Any], zip_code: str, timings: Dict[str, float]) -> None:
    """Call LLM explainer; fall back deterministically; store debug."""
    t0 = time.perf_counter()
    adv = state.get("advisory") or {}
    analysis = state.get("analysis") or {}

    if not adv or not analysis or analysis.get("risk") in (None, "ERROR"):
        return
    if not bool(state.get("active", True)):
        return

    risk = str(analysis.get("risk", ""))
    dist_km = analysis.get("distance_km", "")
    radius_km = (adv or {}).get("radius_km", "")
    category = (adv or {}).get("category", "")

    prompt = (
        f"ZIP: {zip_code}\n"
        f"RISK: {risk}\n"
        f"DIST_KM: {dist_km}\n"
        f"RADIUS_KM: {radius_km}\n"
        f"CATEGORY: {category}\n"
        "Return ONE sentence (<=25 words) and start it with '🧠 AI: '."
    )

    raw_text: Optional[str] = None
    text: Optional[str] = None
    used_ai = False
    ev_summ = []
    err_str = None

    try:
        # Build agent + run with debug to capture events/errors
        agent = build_risk_explainer_agent()
        from core.adk_helpers import run_llm_agent_text_debug
        raw_text, ev_summ, err_str = run_llm_agent_text_debug(
            agent, prompt, session_id=f"risk_expl_{zip_code}_{risk}"
        )

        # If the runner produced nothing and no explicit error, surface it
        if err_str is None and not ev_summ and not raw_text:
            err_str = "NO_EVENTS"

        # Accept any non-empty model text as AI; normalize prefix
        if isinstance(raw_text, str) and raw_text.strip():
            text = raw_text.strip()
            if not text.startswith("🧠 AI:"):
                text = "🧠 AI: " + text
            used_ai = True
    except Exception as e:
        err_str = f"{type(e).__name__}: {e}"
        raw_text = None
        text = None
        used_ai = False


    if not used_ai:
        # Deterministic fallback (no 🧠 prefix)
        inside = (
            isinstance(dist_km, (int, float))
            and isinstance(radius_km, (int, float))
            and float(dist_km) <= float(radius_km)
        )
        where = "inside" if inside else "outside"
        text = f"Risk is {risk} because ZIP {zip_code} is {where} the advisory radius and {dist_km} km from the storm center."

    # Outputs + debug
    state["risk_explainer"] = text
    state.setdefault("flags", {})["risk_explainer_ai"] = used_ai

    dbg = state.setdefault("debug", {})
    dbg["watcher_impl"] = "shim-functional-v1"   # <- so you can confirm this file is active
    dbg["risk_explainer_raw"] = raw_text
    dbg["risk_explainer_prompt"] = prompt
    dbg["risk_explainer_events"] = ev_summ
    if err_str:
        dbg["risk_explainer_error"] = err_str

    timings["explainer_ms"] = (time.perf_counter() - t0) * 1000.0

# ---------- Public API used by Coordinator/UI ----------

def run_watcher_once(data_dir: str, zip_code: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Streamlit-friendly, deterministic single pass:
      - read advisory
      - compute risk
      - generate AI explainer (or fallback)
    Returns (state, timings).
    """
    t0 = time.perf_counter()
    state: Dict[str, Any] = {}
    timings: Dict[str, float] = {}

    _step_read_advisory(state, data_dir, timings)
    _step_analyze_risk(state, zip_code, timings)
    _step_explain_risk(state, zip_code, timings)

    # Aggregate timings
    timings["watcher_ms"] = timings.get("watcher_ms_read", 0.0) + timings.get("watcher_ms_analyze", 0.0)
    timings["watcher_ms_total"] = (time.perf_counter() - t0) * 1000.0
    state["timings_ms"] = timings

    # Safety net so UI never sees an empty analysis
    state.setdefault("analysis", {"risk": "ERROR", "reason": "Watcher produced no analysis"})
    return state, timings
