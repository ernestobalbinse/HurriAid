# agents/watcher.py
from __future__ import annotations

import json
import math
import os
import time
import hashlib
from typing import Any, Dict, Optional, Tuple

# --- ADK helper (required). If missing, UI should surface ADKNotAvailable.
try:
    from core.adk_helpers import run_llm_agent_text_debug
except Exception as e:
    from core.parallel_exec import ADKNotAvailable
    raise ADKNotAvailable(f"Google ADK helper missing: {e}")

# --- Build a tiny LLM agent locally
from google.adk.agents import LlmAgent
try:
    from google.genai import types  # optional for generation config
except Exception:
    types = None  # fall back to default config if unavailable

# --- Preferred ZIP resolver if your project provides it
def _resolve_zip_from_helper(zip_code: str) -> Optional[Tuple[float, float]]:
    try:
        from tools.zip_resolver import resolve_zip_latlon
        lat, lon = resolve_zip_latlon(zip_code)
        if lat is None or lon is None:
            return None
        return float(lat), float(lon)
    except Exception:
        return None

# --- pgeocode fallback (project uses pgeocode)
PGEOCODE_AVAILABLE = False
try:
    import pgeocode
    _NOMI = pgeocode.Nominatim("us")
    PGEOCODE_AVAILABLE = True
except Exception:
    _NOMI = None
    PGEOCODE_AVAILABLE = False


def _resolve_zip_latlon(zip_code: str) -> Optional[Tuple[float, float]]:
    """
    Resolve a US ZIP to (lat, lon).
    Prefer tools.zip_resolver if present; else use pgeocode.
    """
    coords = _resolve_zip_from_helper(zip_code)
    if coords:
        return coords
    if not PGEOCODE_AVAILABLE or _NOMI is None:
        return None
    try:
        rec = _NOMI.query_postal_code(str(zip_code))
        lat = float(rec["latitude"])
        lon = float(rec["longitude"])
        if math.isnan(lat) or math.isnan(lon):
            return None
        return lat, lon
    except Exception:
        return None


# --- File & math helpers
def _load_json_utf8sig(path: str) -> Dict[str, Any]:
    """Load JSON using utf-8-sig to tolerate BOM."""
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)

def _sha256_hex(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

def _fmt_watch_text(zip_code: str, risk: str, dist_km: float, inside: bool, radius_km: float) -> str:
    where = "Inside" if inside else "Outside"
    return (
        f"Risk ZIP: {zip_code}\n"
        f"Risk: {risk}\n"
        f"Distance to storm center: {dist_km:.1f} km\n"
        f"Advisory area: {where} (radius ≈ {float(radius_km):.1f} km)"
    )

def _strip_code_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


# --- Advisory read/normalize
def _read_advisory(data_dir: str) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    Returns (advisory_norm, debug_info, advisory_raw_or_none)
    advisory_norm keys: center{lat,lon}, radius_km, category, issued_at, active
    """
    path = os.path.join(data_dir, "sample_advisory.json")
    dbg: Dict[str, Any] = {
        "advisory_path": path,
        "advisory_sha256": _sha256_hex(path),
    }

    raw: Optional[Dict[str, Any]] = None
    try:
        raw = _load_json_utf8sig(path)
        center = raw.get("center") or {}
        adv = {
            "center": {"lat": float(center.get("lat", 25.77)), "lon": float(center.get("lon", -80.19))},
            "radius_km": float(raw.get("radius_km", 100.0)),
            "category": str(raw.get("category", "TS")),
            "issued_at": str(raw.get("issued_at", "")),
            "active": bool(raw.get("active", True)),
        }
        dbg["advisory_radius_source"] = "file"
        dbg["advisory_radius_raw_value"] = raw.get("radius_km", None)
        return adv, dbg, raw
    except Exception as e:
        dbg["advisory_error"] = f"Advisory invalid: {e}"
        # Return a safe inactive default; UI will show No active hurricane.
        adv = {
            "center": {"lat": 25.77, "lon": -80.19},
            "radius_km": 100.0,
            "category": "TS",
            "issued_at": "",
            "active": False,
        }
        return adv, dbg, raw


# --- AI prompt (JSON only; escape braces for .format)
RISK_INSTRUCTION = (
    "You are a hurricane risk classifier and explainer.\n"
    "Return JSON ONLY on one line: "
    "{{\"risk\":\"LOW|MEDIUM|HIGH\",\"why\":\"<1–2 sentences, 20–45 words, clear and relatable>\"}}\n"
    "\n"
    "Style rules:\n"
    "- Sound natural and empathetic, not robotic.\n"
    "- Use concrete details from the facts (distance, radius, category).\n"
    "- No emojis, no prefixes, no markdown, no extra keys.\n"
    "- Do NOT invent places/people; stick strictly to the facts provided.\n"
    "\n"
    "Good examples:\n"
    "Input facts: zip=33101, category=TS, radius_km=50, distance_km=12.4, inside_radius=TRUE\n"
    "Output: {{\"risk\":\"MEDIUM\",\"why\":\"You’re inside the 50-km advisory zone and close to the storm’s center, so gusty squalls and brief power flickers are possible as bands move through.\"}}\n"
    "\n"
    "Input facts: zip=32226, category=CAT2, radius_km=80, distance_km=140.0, inside_radius=FALSE\n"
    "Output: {{\"risk\":\"LOW\",\"why\":\"You’re outside the advisory radius and roughly 140 km from the center. Expect periods of rain and breezes, but damaging winds are unlikely at this distance.\"}}\n"
    "\n"
    "Facts:\n"
    "- zip: {zip}\n"
    "- category: {category}\n"
    "- radius_km: {radius_km}\n"
    "- distance_km: {distance_km}\n"
    "- inside_radius: {inside}\n"
)

def _make_risk_agent() -> LlmAgent:
    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    gen_cfg = None
    if types is not None:
        try:
            gen_cfg = types.GenerateContentConfig(
                temperature=0.65,      # a bit more expressive
                top_p=0.9,             # allow more varied wording
                max_output_tokens=180, # room for 1–2 sentences
            )
        except Exception:
            gen_cfg = None

    return LlmAgent(
        name="RiskClassifier",
        model=model,
        include_contents="none",
        instruction="Follow the caller’s instructions exactly; respond ONLY as specified.",
        generate_content_config=gen_cfg,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

def _ai_classify_risk(
    zip_code: str,
    category: str,
    radius_km: float,
    distance_km: float,
    inside: bool
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Calls the LLM via ADK helper. Returns (parsed_json_or_None, debug_dict).
    """
    prompt = RISK_INSTRUCTION.format(
        zip=zip_code,
        category=str(category),
        radius_km=str(float(radius_km)),
        distance_km=f"{distance_km:.1f}",
        inside=str(bool(inside)).upper(),
    )

    agent = _make_risk_agent()

    # pass BOTH app_name and agent so ADK Runner can initialize
    text, events, err = run_llm_agent_text_debug(
        agent=agent,
        prompt=prompt,
        app_name="hurri_watch",
        user_id="ui",
        session_id=f"sess_risk_{zip_code}",
    )

    debug = {
        "explainer_prompt": prompt,
        "risk_events": events or [],
        "risk_error": err,
        "risk_raw": text,
    }

    if err or text is None:
        return None, debug

    # Strip code fences if any and parse JSON
    try:
        if isinstance(text, dict):
            obj = text
        else:
            obj = json.loads(_strip_code_fences(str(text)))
    except Exception as e:
        debug["risk_parse_error"] = f"JSON parse failed: {e}"
        obj = None

    return obj, debug


# --- Public entry point used by Coordinator
def run_watcher_once(data_dir: str, zip_code: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    One-shot run for Streamlit. Produces a state dict for the UI.
    AI-only: if the LLM output is invalid, we return ERROR (no heuristic fallback).
    """
    t0 = time.perf_counter()
    state: Dict[str, Any] = {"debug": {}, "errors": {}, "timings_ms": {}}
    timings = state["timings_ms"]
    debug = state["debug"]

    # 1) Advisory
    t_read = time.perf_counter()
    advisory, adv_dbg, adv_raw = _read_advisory(data_dir)
    timings["watcher_ms_read"] = (time.perf_counter() - t_read) * 1000.0
    state["advisory"] = advisory
    state["active"] = bool(advisory.get("active", True))
    if adv_raw is not None:
        state["advisory_raw"] = adv_raw
    debug.update(adv_dbg)

    # Stop early if inactive; UI shows the "No active hurricane" banner.
    if not state["active"]:
        state["analysis"] = {"risk": "SAFE"}
        timings["watcher_ms_total"] = (time.perf_counter() - t0) * 1000.0
        return state, timings

    # 2) ZIP -> lat/lon
    coords = _resolve_zip_latlon(zip_code)
    if coords is None:
        reason = "pgeocode not installed" if not PGEOCODE_AVAILABLE else f"Unknown ZIP {zip_code}"
        state["analysis"] = {"risk": "ERROR", "reason": reason}
        timings["watcher_ms_total"] = (time.perf_counter() - t0) * 1000.0
        return state, timings

    zlat, zlon = coords
    state["zip_point"] = {"lat": zlat, "lon": zlon}

    # 3) Distance/inside
    clat = float(advisory["center"]["lat"])
    clon = float(advisory["center"]["lon"])
    radius_km = float(advisory["radius_km"])
    category = str(advisory.get("category", "TS"))
    dist_km = _haversine_km(zlat, zlon, clat, clon)
    inside = dist_km <= radius_km

    # 4) Ask AI for risk + why (JSON)
    t_ai = time.perf_counter()
    obj, ai_dbg = _ai_classify_risk(
        zip_code=zip_code,
        category=category,
        radius_km=radius_km,
        distance_km=dist_km,
        inside=inside,
    )
    timings["watcher_ms_analyze"] = (time.perf_counter() - t_ai) * 1000.0
    debug.update(ai_dbg)

    if not obj:
        state["analysis"] = {"risk": "ERROR", "reason": ai_dbg.get("risk_error") or ai_dbg.get("risk_parse_error") or "NO_TEXT"}
        timings["watcher_ms_total"] = (time.perf_counter() - t0) * 1000.0
        return state, timings

    risk = str(obj.get("risk", "")).upper().strip()
    why = str(obj.get("why", "")).strip()

    # AI-only: if invalid, error out (no heuristic fallback)
    if risk not in ("LOW", "MEDIUM", "HIGH") or not why:
        state["analysis"] = {"risk": "ERROR", "reason": "AI risk parse failed: missing/invalid `risk` or `why`"}
        timings["watcher_ms_total"] = (time.perf_counter() - t0) * 1000.0
        return state, timings

    # 5) Persist outputs for UI
    state["analysis"] = {"risk": risk, "distance_km": round(dist_km, 1)}
    state["risk_explainer"] = why
    state["watcher_text"] = _fmt_watch_text(zip_code, risk, dist_km, inside, radius_km)

    # Debug visibility
    debug["risk_obj"] = obj
    debug["watcher_impl"] = "ai-risk-v5"

    # Timings total
    timings["watcher_ms"] = timings.get("watcher_ms_read", 0.0) + timings.get("watcher_ms_analyze", 0.0)
    timings["watcher_ms_total"] = (time.perf_counter() - t0) * 1000.0
    return state, timings


# --- Back-compat for older imports (parallel_pipeline, etc.)
_PGEOCODE_AVAILABLE = PGEOCODE_AVAILABLE
