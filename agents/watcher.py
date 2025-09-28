# agents/watcher.py
from __future__ import annotations
from core.advisory import read_advisory, AdvisoryError


import os
import json
import math
import time
import hashlib
from typing import Dict, Any, Tuple, Optional

# --- AI explainer (your existing modules) ---
from agents.ai_explainer import build_risk_explainer_agent
from core.adk_helpers import run_llm_agent_text_debug

# --- ZIP -> lat/lon using pgeocode ---
try:
    import pgeocode  # pip install pgeocode
    _PGEOCODE_OK = True
except Exception:
    _PGEOCODE_OK = False

_GEOCODER = None
def _get_geocoder():
    global _GEOCODER
    if _GEOCODER is None and _PGEOCODE_OK:
        _GEOCODER = pgeocode.Nominatim("us")
    return _GEOCODER

def _resolve_zip_latlon(zip_code: str) -> Optional[Tuple[float, float]]:
    if not _PGEOCODE_OK:
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

# ---------- math & risk ----------
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb/2) ** 2
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
    """
    HIGH if inside radius OR (<= 50 km at CAT2+)
    MEDIUM if <= (radius+120 km) OR inside at TS/CAT1
    LOW otherwise
    """
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

# ---------- safe coercions ----------
def _to_float(x, default: float) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _to_bool(x, default: bool = True) -> bool:
    if isinstance(x, bool):
        return x
    if x is None:
        return default
    if isinstance(x, (int, float)):
        return bool(x)
    if isinstance(x, str):
        s = x.strip().lower()
        if s in {"true", "1", "yes", "y", "on"}:
            return True
        if s in {"false", "0", "no", "n", "off"}:
            return False
    return default

# ---------- Step 1: read advisory from file (store RAW + normalized) ----------
def _step_read_advisory(state: Dict[str, Any], data_dir: str, timings: Dict[str, float]) -> None:
    t0 = time.perf_counter()
    try:
        raw, adv_norm, dbg = read_advisory(data_dir)
    except AdvisoryError as e:
        # hard fail: surface clear error and stop downstream steps
        state["errors"] = {**state.get("errors", {}), "advisory": str(e)}
        state["analysis"] = {"risk": "ERROR", "reason": f"Advisory invalid: {e}"}
        timings["watcher_ms_read"] = (time.perf_counter() - t0) * 1000.0
        return

    state["advisory_raw"] = raw
    state["advisory"] = adv_norm
    state["active"] = bool(adv_norm.get("active", True))
    dbg_all = state.setdefault("debug", {})
    dbg_all.update(dbg)

    timings["watcher_ms_read"] = (time.perf_counter() - t0) * 1000.0

# --- replace your existing _step_analyze_risk with this ---
def _step_analyze_risk(state: Dict[str, Any], zip_code: str, timings: Dict[str, float]) -> None:
    t0 = time.perf_counter()

    adv = state.get("advisory")
    if not isinstance(adv, dict):
        state["analysis"] = {"risk": "ERROR", "reason": "No advisory loaded"}
        timings["watcher_ms_analyze"] = (time.perf_counter() - t0) * 1000.0
        return

    center = adv.get("center")
    radius_km = adv.get("radius_km")
    if not isinstance(center, dict) or "lat" not in center or "lon" not in center:
        state["analysis"] = {"risk": "ERROR", "reason": "Advisory missing center.lat/lon"}
        timings["watcher_ms_analyze"] = (time.perf_counter() - t0) * 1000.0
        return
    if radius_km is None:
        state["analysis"] = {"risk": "ERROR", "reason": "Advisory missing radius_km"}
        timings["watcher_ms_analyze"] = (time.perf_counter() - t0) * 1000.0
        return

    # Resolve ZIP
    coords = _resolve_zip_latlon(zip_code)
    if coords is None:
        reason = "pgeocode not installed" if not _PGEOCODE_OK else f"Unknown ZIP {zip_code}"
        state["analysis"] = {"risk": "ERROR", "reason": reason}
        timings["watcher_ms_analyze"] = (time.perf_counter() - t0) * 1000.0
        return

    zlat, zlon = coords
    clat, clon = float(center["lat"]), float(center["lon"])
    dist_km = _haversine_km(zlat, zlon, clat, clon)
    r_km = float(radius_km)
    inside = dist_km <= r_km
    risk = _risk_heuristic(dist_km, r_km, str(adv.get("category", "")))

    state["zip_point"] = {"lat": zlat, "lon": zlon}
    state["analysis"]  = {"risk": risk, "distance_km": round(dist_km, 1)}
    state["watcher_text"] = _fmt_watch_text(zip_code, risk, dist_km, inside, r_km)

    timings["watcher_ms_analyze"] = (time.perf_counter() - t0) * 1000.0


# ---------- Step 3: AI explainer ----------
def _clean_explainer(s: str) -> str:
    if not isinstance(s, str):
        return ""
    out = s.strip()
    if out.startswith("🧠 AI:"):
        out = out[len("🧠 AI:"):].strip()
    if out.lower().startswith("ai:"):
        out = out[3:].strip()
    return " ".join(out.split())

def _step_ai_explainer(state: Dict[str, Any], zip_code: str, timings: Dict[str, float]) -> None:
    t0 = time.perf_counter()

    adv = state.get("advisory") or {}
    analysis = state.get("analysis") or {}
    if not adv or not analysis or analysis.get("risk") in (None, "ERROR"):
        timings["explainer_ms"] = (time.perf_counter() - t0) * 1000.0
        return

    risk = str(analysis.get("risk", ""))
    dist_km = analysis.get("distance_km", "")
    radius_km = adv.get("radius_km", "")
    category = adv.get("category", "")

    agent = build_risk_explainer_agent()
    prompt = (
        "Explain hurricane risk in one sentence (<=25 words), plain text only.\n"
        "Facts:\n"
        f"- zip: {zip_code}\n"
        f"- risk: {risk}\n"
        f"- distance_km: {dist_km}\n"
        f"- radius_km: {radius_km}\n"
        f"- category: {category}\n"
        "Respond ONLY with the sentence. No emojis, no prefixes.\n"
    )

    text, events, err = run_llm_agent_text_debug(
        agent, prompt,
        app_name="hurri_aid",
        user_id="ui_user",
        session_id="sess_explainer"
    )

    dbg = state.setdefault("debug", {})
    dbg["explainer_prompt"] = prompt
    dbg["explainer_events"] = events
    dbg["explainer_error"] = err
    dbg["explainer_raw"] = text

    final = _clean_explainer(text or "")
    # Per requirement: rely solely on AI (no deterministic fallback)
    state["risk_explainer"] = final if final else None
    state["analysis_explainer"] = state.get("risk_explainer")

    timings["explainer_ms"] = (time.perf_counter() - t0) * 1000.0

# ---------- public entry point ----------
def run_watcher_once(data_dir: str, zip_code: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    T0 = time.perf_counter()
    state: Dict[str, Any] = {}
    timings: Dict[str, float] = {}

    _step_read_advisory(state, data_dir, timings)

    # NEW: if advisory step flagged an error, don’t proceed
    if (state.get("analysis") or {}).get("risk") == "ERROR" or "advisory" not in state:
        timings["watcher_ms"] = timings.get("watcher_ms_read", 0.0)
        timings["watcher_ms_total"] = (time.perf_counter() - T0) * 1000.0
        state["timings_ms"] = timings
        return state, timings

    _step_analyze_risk(state, zip_code, timings)
    _step_ai_explainer(state, zip_code, timings)

    timings["watcher_ms"] = timings.get("watcher_ms_read", 0.0) + timings.get("watcher_ms_analyze", 0.0)
    timings["watcher_ms_total"] = (time.perf_counter() - T0) * 1000.0
    state["timings_ms"] = timings
    state.setdefault("analysis", {"risk": "ERROR", "reason": "Watcher produced no analysis"})
    return state, timings
