# agents/watcher.py
from __future__ import annotations
from core.units import km_to_mi

import json
import os
import math
import time
import hashlib
import secrets
from typing import Dict, Any, Tuple, Optional

# ADK + GENAI
from google.adk.agents import LlmAgent
from google.genai import types  # type: ignore

# Project helpers
from core.adk_helpers import run_llm_agent_text_debug

# ---------------- Constants ----------------
# IDs for ADK session/runner
APP_NAME = "HurriAid"
USER_ID  = os.getenv("ADK_USER_ID", "local_user")   # <— add this line
DEFAULT_MODEL_ID = os.getenv("ADK_MODEL_ID", "gemini-2.0-flash")


RISK_INSTRUCTION = """\
You are a hurricane risk classifier. Output ONLY strict JSON like:
{{"risk":"SAFE|LOW|MEDIUM|HIGH","why":"<one concise sentence>","proof":"{proof}"}}

Definitions:
- SAFE: advisory inactive OR distance_km > radius_km + 200 OR (distance_km > 300 AND category in [TS, CAT1]).
- LOW: outside the advisory and not close; monitor only.
- MEDIUM: within (radius_km + 120) OR inside the advisory at TS/CAT1.
- HIGH: inside advisory radius OR within 50 km at CAT2+.

Style:
- Use clear, human language (friendly, not robotic).
- ONE sentence (≤ 25 words).
- No emojis, no prefixes, no markdown.

Rules:
- Return ONLY JSON (no extra text).
- The "proof" value MUST be exactly "{proof}".

Examples:

INPUT
zip=33101 distance_km=1.3 radius_km=50.0 category=TS active=true
OUTPUT
{{"risk":"HIGH","why":"You’re very close to a tropical storm’s center, so conditions can worsen quickly.","proof":"{proof}"}}

INPUT
zip=94105 distance_km=620.0 radius_km=80.0 category=CAT1 active=true
OUTPUT
{{"risk":"SAFE","why":"You are far from the storm and well outside any likely impact area.","proof":"{proof}"}}

Facts for this case:
- zip: {zip}
- distance_km: {distance_km}
- radius_km: {radius_km}
- category: {category}
- active: {active}
"""

# ---------------- ZIP → lat/lon ----------------
try:
    import pgeocode  # pip install pgeocode
    PGEOCODE_AVAILABLE = True
except Exception:
    PGEOCODE_AVAILABLE = False

_geocoder = None
def _get_geocoder():
    global _geocoder
    if _geocoder is None and PGEOCODE_AVAILABLE:
        _geocoder = pgeocode.Nominatim("us")
    return _geocoder

def _resolve_zip_from_helper(zip_code: str) -> Optional[Tuple[float, float]]:
    """Optional project helper (if present)."""
    try:
        from tools.zip_resolver import resolve_zip_latlon
        lat, lon = resolve_zip_latlon(zip_code)
        if lat is None or lon is None:
            return None
        return float(lat), float(lon)
    except Exception:
        return None

def resolve_zip_latlon(zip_code: str) -> Optional[Tuple[float, float]]:
    """
    Public resolver exported for other modules.
    Tries project helper then pgeocode.
    """
    coords = _resolve_zip_from_helper(zip_code)
    if coords is not None:
        return coords
    if not PGEOCODE_AVAILABLE:
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

# ---------------- Utility ----------------
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2)**2
    return 2 * R * math.asin(math.sqrt(a))

def _load_json_with_bom(path: str) -> Dict[str, Any]:
    """
    Load JSON, tolerating BOM by using utf-8-sig.
    """
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)

def _sha256_file(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None

def _fmt_watch_text(zip_code: str, risk: str, dist_km: float, inside: bool, radius_km: float) -> str:
    where = "Inside" if inside else "Outside"
    dist_mi = km_to_mi(dist_km)
    radius_mi = km_to_mi(radius_km)
    return (
        f"Risk ZIP: {zip_code}\n"
        f"Risk: {risk}\n"
        f"Distance to storm center: {dist_mi:.1f} mi\n"
        f"Advisory area: {where} (radius ≈ {radius_mi:.1f} mi)"
    )


def _json_from_text(t: Optional[str]) -> Optional[Dict[str, Any]]:
    if not t or not isinstance(t, str):
        return None
    try:
        return json.loads(t)
    except Exception:
        return None

# ---------------- AI Risk Classifier ----------------
def _ai_classify_risk(
    *,
    zip_code: str,
    distance_km: float,
    radius_km: float,
    category: str,
    active: bool,
    session_id: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Calls an LLM to classify risk and explain 'why'.
    Returns (obj, debug) where obj has keys: risk, why, proof.
    Raises RuntimeError on failure so caller can surface a clear UI error.
    """
    proof = secrets.token_hex(3)  # a short per-call token

    agent = LlmAgent(
        model=DEFAULT_MODEL_ID,
        name="RiskClassifier",
        include_contents="none",
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(  # type: ignore
            temperature=0.6,  # slightly expressive
            max_output_tokens=160,
            response_mime_type="application/json",
        ),
    )

    prompt = RISK_INSTRUCTION.format(
        zip=zip_code,
        distance_km=distance_km,
        radius_km=radius_km,
        category=category,
        active=str(bool(active)).lower(),
        proof=proof,
    )

    dbg: Dict[str, Any] = {
        "explainer_prompt": prompt,
        "attempts": [],
        "proof": proof,
    }

    def _call(tag: str, pr: str) -> Tuple[Optional[str], Any, Optional[str]]:
        text, events, err = run_llm_agent_text_debug(
            app_name=APP_NAME,
            user_id=USER_ID,          # <— add this
            session_id=session_id,
            agent=agent,
            prompt=pr,
        )

        dbg["attempts"].append({"tag": tag, "err": f"{err}" if err else None, "text": text})
        return text, events, err

    # try once
    text1, _, _ = _call("first", prompt)
    obj = _json_from_text(text1)

    # retry with explicit nudge if needed
    if obj is None:
        text2, _, _ = _call("retry", prompt + "\nReturn ONLY the JSON object now. No prose.")
        obj = _json_from_text(text2)

    if not obj:
        raise RuntimeError("empty model text")

    # validate keys + proof
    if "risk" not in obj or "why" not in obj or "proof" not in obj:
        raise RuntimeError('missing keys in AI JSON (expected "risk","why","proof")')

    if str(obj.get("proof", "")).strip() != proof:
        raise RuntimeError("AI JSON missing/invalid proof")

    obj["risk"] = str(obj["risk"]).upper().strip()
    return obj, dbg

# ---------------- Public entry: one-shot watcher ----------------
def run_watcher_once(data_dir: str, zip_code: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Streamlit-friendly one-iteration run.
    Produces a dict 'state' with advisory, analysis, risk_explainer, watcher_text, debug, timings_ms, etc.
    """
    t0_total = time.perf_counter()
    state: Dict[str, Any] = {"debug": {}, "timings_ms": {}}
    timings = state["timings_ms"]

    # 1) Load advisory
    adv_path = os.path.join(data_dir, "sample_advisory.json")
    t0 = time.perf_counter()
    try:
        advisory_raw = _load_json_with_bom(adv_path)
        sha = _sha256_file(adv_path)
        state["debug"]["advisory_path"] = adv_path
        if sha:
            state["debug"]["advisory_sha256"] = sha
    except Exception as e:
        state["analysis"] = {
            "risk": risk,
            "distance_km": round(dist_km, 1),
            "distance_mi": round(km_to_mi(dist_km) or dist_km * 0.621371, 1),
        }
        timings["watcher_ms_total"] = (time.perf_counter() - t0_total) * 1000.0
        state["advisory"] = {}
        return state, timings

    # normalize advisory
    center = advisory_raw.get("center") or {}
    adv = {
        "center": {"lat": float(center.get("lat", 25.77)), "lon": float(center.get("lon", -80.19))},
        "radius_km": float(advisory_raw.get("radius_km", 100.0)),
        "category": str(advisory_raw.get("category", "TS")),
        "issued_at": advisory_raw.get("issued_at", ""),
        "active": bool(advisory_raw.get("active", True)),
    }
    state["advisory_raw"] = advisory_raw  # for UI debugging
    state["advisory"] = adv
    timings["watcher_ms_read"] = (time.perf_counter() - t0) * 1000.0

    # 2) If inactive -> stop (UI shows paused)
    if not adv["active"]:
        state["analysis"] = {"risk": "SAFE", "distance_km": None}
        state["risk_explainer"] = "Advisory is inactive for this area."
        timings["watcher_ms_total"] = (time.perf_counter() - t0_total) * 1000.0
        return state, timings

    # 3) ZIP → lat/lon
    t0 = time.perf_counter()
    coords = resolve_zip_latlon(zip_code)
    if coords is None:
        reason = "pgeocode not installed or unknown ZIP"
        state["analysis"] = {"risk": "ERROR", "reason": reason}
        timings["watcher_ms_analyze"] = (time.perf_counter() - t0) * 1000.0
        timings["watcher_ms_total"] = (time.perf_counter() - t0_total) * 1000.0
        return state, timings

    zlat, zlon = coords
    state["zip_point"] = {"lat": zlat, "lon": zlon}

    clat, clon = float(adv["center"]["lat"]), float(adv["center"]["lon"])
    dist_km = _haversine_km(zlat, zlon, clat, clon)
    radius_km = float(adv["radius_km"])
    inside = dist_km <= radius_km
    timings["watcher_ms_analyze"] = (time.perf_counter() - t0) * 1000.0

    # 4) AI: risk + why
    t0 = time.perf_counter()
    ai_err = None
    ai_dbg: Dict[str, Any] = {}
    try:
        obj, ai_dbg = _ai_classify_risk(
            zip_code=zip_code,
            distance_km=round(dist_km, 1),
            radius_km=radius_km,
            category=str(adv.get("category", "")),
            active=bool(adv.get("active", True)),
            session_id=f"sess_risk_{zip_code}",
        )
        risk = obj["risk"]
        why = str(obj.get("why", "")).strip()
        state["debug"]["risk_ai_verified"] = True
        state["risk_explainer"] = why  # <- UI shows this as "Why (AI)"
        state["debug"]["risk_raw"] = obj
    except Exception as e:
        ai_err = f"{e}"
        state["debug"]["risk_ai_verified"] = False
        state["risk_explainer"] = None
        state["debug"]["risk_raw"] = None

        # surface the error in analysis.reason so UI can show it under Risk
        state["analysis"] = {"risk": "ERROR", "reason": f"AI risk failed: {ai_err}"}
        timings["watcher_ms_explainer"] = (time.perf_counter() - t0) * 1000.0
        timings["watcher_ms"] = timings.get("watcher_ms_read", 0.0) + timings.get("watcher_ms_analyze", 0.0)
        timings["watcher_ms_total"] = (time.perf_counter() - t0_total) * 1000.0
        # also include debug attempts/prompt
        state["debug"]["risk_ai"] = ai_dbg
        return state, timings

    timings["watcher_ms_explainer"] = (time.perf_counter() - t0) * 1000.0
    state["debug"]["risk_ai"] = ai_dbg

    # 5) Finalize analysis using AI-provided risk
    state["analysis"] = {"risk": risk, "distance_km": round(dist_km, 1)}

    # 6) Watcher text block
    state["watcher_text"] = _fmt_watch_text(zip_code, risk, dist_km, inside, radius_km)

    # 7) Total timings
    timings["watcher_ms"] = timings.get("watcher_ms_read", 0.0) + timings.get("watcher_ms_analyze", 0.0)
    timings["watcher_ms_total"] = (time.perf_counter() - t0_total) * 1000.0

    return state, timings
