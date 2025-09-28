# agents/watcher.py
from __future__ import annotations

import json
import os
import math
import time
import hashlib
import secrets
from typing import Dict, Any, Tuple, Optional

from core.units import km_to_mi

# ADK + GenAI (we call the LLM directly via the ADK runner utilities)
from google.adk.agents.llm_agent import LlmAgent
from google.genai import types  # type: ignore

from core.adk_helpers import run_llm_agent_text_debug

# ---------------- App identity ----------------
APP_NAME = "HurriAid"
USER_ID = os.getenv("ADK_USER_ID", "local_user")
DEFAULT_MODEL_ID = os.getenv("ADK_MODEL_ID", "gemini-2.0-flash")

# ---------------- Risk prompt ----------------
# Plain-English instructions the model can reliably follow.
RISK_INSTRUCTION = """\
You are a hurricane risk classifier. Return ONLY strict JSON:
{{"risk":"SAFE|LOW|MEDIUM|HIGH","why":"<one concise sentence>","proof":"{proof}"}}

Use this guidance:
- SAFE   : advisory inactive, OR distance_km > radius_km + 200, OR (distance_km > 300 AND category in [TS, CAT1]).
- LOW    : outside advisory and not close; monitor only.
- MEDIUM : within (radius_km + 120) OR inside advisory at TS/CAT1.
- HIGH   : inside advisory radius OR within 50 km at CAT2+.

Style:
- Friendly, natural tone.
- ONE short sentence (<= 25 words).
- No emojis, no markdown, no prefixes.

Facts for this case (one per line):
zip={zip}
distance_km={distance_km}
radius_km={radius_km}
category={category}
active={active}

Remember:
- The "proof" field MUST echo exactly "{proof}".
"""

# ---------------- ZIP → lat/lon ----------------
# A simple, local ZIP geocoder. AI handles reasoning, not geocoding.
try:
    import pgeocode  # pip install pgeocode
    _PGEOCODE = pgeocode.Nominatim("us")
    PGEOCODE_AVAILABLE = True
except Exception:
    _PGEOCODE = None
    PGEOCODE_AVAILABLE = False


def resolve_zip_latlon(zip_code: str) -> Optional[Tuple[float, float]]:
    """Return (lat, lon) for a US ZIP or None if we can’t resolve it."""
    if not PGEOCODE_AVAILABLE:
        return None
    try:
        rec = _PGEOCODE.query_postal_code(str(zip_code))
        lat, lon = float(rec["latitude"]), float(rec["longitude"])
        if math.isnan(lat) or math.isnan(lon):
            return None
        return lat, lon
    except Exception:
        return None


# ---------------- Math helpers ----------------
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance between two lat/lon points in kilometers."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ---------------- File & text helpers ----------------
def _load_json_with_bom(path: str) -> Dict[str, Any]:
    """Read JSON safely (tolerates BOM)."""
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _sha256_file(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None


def _fmt_watch_text(zip_code: str, risk: str, dist_km: float, inside: bool, radius_km: float) -> str:
    """User-facing summary block (miles for readability)."""
    where = "Inside" if inside else "Outside"
    dist_mi = km_to_mi(dist_km)
    radius_mi = km_to_mi(radius_km)
    return (
        f"ZIP: {zip_code}\n"
        f"Risk: {risk}\n"
        f"Distance to storm center: {dist_mi:.1f} mi\n"
        f"Advisory area: {where} (radius ≈ {radius_mi:.1f} mi)"
    )


def _json_from_text(t: Optional[str]) -> Optional[Dict[str, Any]]:
    """Best-effort: parse the model’s text into JSON, or return None."""
    if not t or not isinstance(t, str):
        return None
    # Strip common code-fence wrappers if the model slipped them in.
    s = t.strip()
    if s.startswith("```"):
        a, b = s.find("{"), s.rfind("}")
        if a != -1 and b != -1 and b > a:
            s = s[a : b + 1]
    try:
        return json.loads(s)
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
    Ask the LLM for a single risk label and a one-line explanation.
    We include a short 'proof' nonce so we can verify the response is fresh and for this prompt.
    """
    proof = secrets.token_hex(3)  # tiny per-call token we expect back

    agent = LlmAgent(
        model=DEFAULT_MODEL_ID,
        name="RiskClassifier",
        include_contents="none",
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(  # type: ignore
            temperature=0.55,  # a touch of warmth without drifting
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

    dbg: Dict[str, Any] = {"explainer_prompt": prompt, "attempts": [], "proof": proof}

    def _call(tag: str, pr: str):
        text, events, err = run_llm_agent_text_debug(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
            agent=agent,
            prompt=pr,
        )
        dbg["attempts"].append({"tag": tag, "err": f"{err}" if err else None, "text": text})
        return text, events, err

    # First try
    text1, _, _ = _call("first", prompt)
    obj = _json_from_text(text1)

    # Gentle nudge if the model forgot the strict JSON format
    if obj is None:
        text2, _, _ = _call("retry", prompt + "\nReturn ONLY the JSON object now. No prose.")
        obj = _json_from_text(text2)

    if not obj:
        raise RuntimeError("empty model text")

    # Validate expected keys and nonce
    if "risk" not in obj or "why" not in obj or "proof" not in obj:
        raise RuntimeError('missing keys in AI JSON (expected "risk","why","proof")')
    if str(obj.get("proof", "")).strip() != proof:
        raise RuntimeError("AI JSON missing/invalid proof")

    obj["risk"] = str(obj["risk"]).upper().strip()
    return obj, dbg


# ---------------- One-shot watcher entry ----------------
def run_watcher_once(data_dir: str, zip_code: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    One pass for the UI:
      1) Load the advisory for this run.
      2) Resolve ZIP to a point.
      3) Compute distance to storm, basic inside/outside.
      4) Ask AI for risk + short “why”.
      5) Return a tidy state dict + timing stats.
    """
    t0_total = time.perf_counter()
    state: Dict[str, Any] = {"debug": {}, "timings_ms": {}, "errors": {}}
    timings = state["timings_ms"]

    # --- 1) Load advisory file ---
    t0 = time.perf_counter()
    adv_path = os.path.join(data_dir, "sample_advisory.json")
    try:
        advisory_raw = _load_json_with_bom(adv_path)
        sha = _sha256_file(adv_path)
        state["debug"]["advisory_path"] = adv_path
        if sha:
            state["debug"]["advisory_sha256"] = sha
    except Exception as e:
        state["errors"]["advisory"] = f"Failed to read advisory: {e}"
        state["analysis"] = {"risk": "ERROR", "reason": "Advisory file not available"}
        timings["watcher_ms_read"] = (time.perf_counter() - t0) * 1000.0
        timings["watcher_ms_total"] = (time.perf_counter() - t0_total) * 1000.0
        return state, timings

    # Normalize core fields we care about
    center = advisory_raw.get("center") or {}
    adv = {
        "center": {"lat": float(center.get("lat", 25.77)), "lon": float(center.get("lon", -80.19))},
        "radius_km": float(advisory_raw.get("radius_km", 100.0)),
        "category": str(advisory_raw.get("category", "TS")),
        "issued_at": advisory_raw.get("issued_at", ""),
        "active": bool(advisory_raw.get("active", True)),
    }
    state["advisory_raw"] = advisory_raw  # helpful for debugging in the UI
    state["advisory"] = adv
    timings["watcher_ms_read"] = (time.perf_counter() - t0) * 1000.0

    # If the advisory is inactive, we stop early with a calm message.
    if not adv["active"]:
        state["analysis"] = {"risk": "SAFE", "distance_km": None}
        state["risk_explainer"] = "The advisory is currently inactive for this area."
        timings["watcher_ms_total"] = (time.perf_counter() - t0_total) * 1000.0
        return state, timings

    # --- 2) ZIP → lat/lon ---
    t0 = time.perf_counter()
    coords = resolve_zip_latlon(zip_code)
    if coords is None:
        reason = "ZIP could not be resolved (install pgeocode or check the ZIP)"
        state["analysis"] = {"risk": "ERROR", "reason": reason}
        timings["watcher_ms_analyze"] = (time.perf_counter() - t0) * 1000.0
        timings["watcher_ms_total"] = (time.perf_counter() - t0_total) * 1000.0
        return state, timings

    zlat, zlon = coords
    state["zip_point"] = {"lat": zlat, "lon": zlon}

    # --- 3) Distance/inside calculation ---
    clat, clon = float(adv["center"]["lat"]), float(adv["center"]["lon"])
    dist_km = _haversine_km(zlat, zlon, clat, clon)
    radius_km = float(adv["radius_km"])
    inside = dist_km <= radius_km
    timings["watcher_ms_analyze"] = (time.perf_counter() - t0) * 1000.0

    # --- 4) AI risk + why ---
    t0 = time.perf_counter()
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
        state["risk_explainer"] = why               # <- UI shows this as “Why (AI)”
        state["debug"]["risk_raw"] = obj            # raw JSON the model returned
    except Exception as e:
        state["debug"]["risk_ai_verified"] = False
        state["risk_explainer"] = None
        state["debug"]["risk_raw"] = None
        state["errors"]["risk_ai"] = f"{e}"
        state["analysis"] = {"risk": "ERROR", "reason": f"AI risk failed: {e}"}
        timings["explainer_ms"] = (time.perf_counter() - t0) * 1000.0
        # Total up to now and carry the debug block for the UI
        state["debug"]["risk_ai"] = ai_dbg
        timings["watcher_ms"] = timings.get("watcher_ms_read", 0.0) + timings.get("watcher_ms_analyze", 0.0)
        timings["watcher_ms_total"] = (time.perf_counter() - t0_total) * 1000.0
        return state, timings

    timings["explainer_ms"] = (time.perf_counter() - t0) * 1000.0
    state["debug"]["risk_ai"] = ai_dbg

    # --- 5) Final analysis & UX text ---
    state["analysis"] = {"risk": risk, "distance_km": round(dist_km, 1)}
    state["watcher_text"] = _fmt_watch_text(zip_code, risk, dist_km, inside, radius_km)

    # --- 6) Totals for the UI timing panel ---
    timings["watcher_ms"] = (
        timings.get("watcher_ms_read", 0.0)
        + timings.get("watcher_ms_analyze", 0.0)
        + timings.get("explainer_ms", 0.0)
    )
    timings["watcher_ms_total"] = (time.perf_counter() - t0_total) * 1000.0

    return state, timings
