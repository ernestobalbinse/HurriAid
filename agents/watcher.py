# agents/watcher.py
from __future__ import annotations
from pydantic import PrivateAttr


import json
import os
import math
import time
from typing import Dict, Any, Tuple, Optional

# --- local minimal context (shim) ---
class _MiniActions:
    def __init__(self):
        self.escalate = False  # LoopAgent-style stop flag

class _MiniContext:
    def __init__(self):
        self.session_state = {}
        self.actions = _MiniActions()


# --- ADK presence (UI shows a friendly error if missing) ---
try:
    from google.adk.agents import LoopAgent, BaseAgent
    from google.adk.agents.invocation_context import InvocationContext
except Exception as e:
    from core.parallel_exec import ADKNotAvailable
    raise ADKNotAvailable(f"Google ADK not available: {e}")

# --- Try to import pgeocode ---
try:
    import pgeocode  # pip install pgeocode
    _PGEOCODE_AVAILABLE = True
except Exception:
    _PGEOCODE_AVAILABLE = False

# Optional helper: if your repo already wraps pgeocode.
def _resolve_zip_from_helper(zip_code: str) -> Optional[Tuple[float, float]]:
    try:
        from tools.zip_resolver import resolve_zip_latlon  # your helper (if present)
        lat, lon = resolve_zip_latlon(zip_code)
        if lat is None or lon is None:
            return None
        return float(lat), float(lon)
    except Exception:
        return None

# Cache a single pgeocode Nominatim instance (US)
_geocoder = None
def _get_geocoder():
    global _geocoder
    if _geocoder is None and _PGEOCODE_AVAILABLE:
        _geocoder = pgeocode.Nominatim("us")
    return _geocoder

def _resolve_zip_latlon(zip_code: str) -> Optional[Tuple[float, float]]:
    """
    Resolve ZIP -> (lat, lon) using:
      1) tools.zip_resolver.resolve_zip_latlon if available,
      2) pgeocode.Nominatim('us') otherwise.
    Returns None if unknown or dependency missing.
    """
    # 1) Prefer project helper if present
    coords = _resolve_zip_from_helper(zip_code)
    if coords is not None:
        return coords

    # 2) Use pgeocode directly
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

# --------- math & risk helpers ---------
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

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

# --------- ADK sub-agents ---------
class AdvisoryReader(BaseAgent):
    _data_dir: str = PrivateAttr()

    def __init__(self, data_dir: str):
        super().__init__(name="AdvisoryReader")
        self._data_dir = data_dir

    async def run_async(self, context: InvocationContext) -> None:
        t0 = time.perf_counter()
        advisory_path = os.path.join(self._data_dir, "sample_advisory.json")  # <-- use _data_dir
        try:
            adv = _load_json(advisory_path)
        except Exception:
            adv = {}

        center = (adv.get("center") or {})
        adv_norm = {
            "center": {"lat": float(center.get("lat", 25.77)), "lon": float(center.get("lon", -80.19))},
            "radius_km": float(adv.get("radius_km", 100.0)),
            "category": adv.get("category", "TS"),
            "issued_at": adv.get("issued_at", ""),
            "active": bool(adv.get("active", True)),
        }

        context.session_state["advisory"] = adv_norm
        context.session_state["active"] = adv_norm["active"]
        context.session_state.setdefault("timings_ms", {})
        context.session_state["timings_ms"]["watcher_ms_read"] = (time.perf_counter() - t0) * 1000.0

class RiskAnalyzer(BaseAgent):
    _data_dir: str = PrivateAttr()
    _zip_code: str = PrivateAttr()

    def __init__(self, data_dir: str, zip_code: str):
        super().__init__(name="RiskAnalyzer")
        self._data_dir = data_dir
        self._zip_code = zip_code

    async def run_async(self, context: InvocationContext) -> None:
        t0 = time.perf_counter()
        state = context.session_state
        adv = state.get("advisory") or {}

        # Resolve ZIP via pgeocode/helper
        coords = _resolve_zip_latlon(self._zip_code)  # <-- use _zip_code
        if coords is None:
            reason = "pgeocode not installed" if not _PGEOCODE_AVAILABLE else f"Unknown ZIP {self._zip_code}"
            state["analysis"] = {"risk": "ERROR", "reason": reason}
            return

        zlat, zlon = coords
        clat, clon = float(adv["center"]["lat"]), float(adv["center"]["lon"])
        dist_km = _haversine_km(zlat, zlon, clat, clon)
        radius_km = float(adv["radius_km"])
        inside = dist_km <= radius_km
        risk = _risk_heuristic(dist_km, radius_km, str(adv.get("category", "")))

        state["zip_point"] = {"lat": zlat, "lon": zlon}
        state["analysis"] = {"risk": risk, "distance_km": round(dist_km, 1)}
        state["watcher_text"] = _fmt_watch_text(self._zip_code, risk, dist_km, inside, radius_km)

        state.setdefault("timings_ms", {})
        state["timings_ms"]["watcher_ms_analyze"] = (time.perf_counter() - t0) * 1000.0

class StopIfInactive(BaseAgent):
    def __init__(self):
        super().__init__(name="StopIfInactive")

    async def run_async(self, context: InvocationContext) -> None:
        if not bool(context.session_state.get("active", True)):
            context.actions.escalate = True

# --------- Builder + one-shot runner ---------
def build_watcher_loop_agent(data_dir: str, zip_code: str) -> LoopAgent:
    return LoopAgent(
        name="WatcherLoop",
        sub_agents=[
            AdvisoryReader(data_dir),
            RiskAnalyzer(data_dir, zip_code),
            StopIfInactive(),
        ],
        # Default to single iteration per UI click (non-blocking).
        # If you truly want continuous looping inside the same call, increase this —
        # but Streamlit will be blocked until it finishes.
        max_iterations=1,
        description="Reads advisory, computes ZIP risk via pgeocode, stops if inactive."
    )

def run_watcher_once(data_dir: str, zip_code: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Streamlit-friendly one-iteration run.
    Executes the three ADK sub-agents sequentially with a minimal context.
    Produces the same session_state as the LoopAgent would after one pass.
    """
    t0 = time.perf_counter()
    ctx = _MiniContext()

    async def _one_iter():
        reader = AdvisoryReader(data_dir)
        analyzer = RiskAnalyzer(data_dir, zip_code)
        stopper = StopIfInactive()

        await reader.run_async(ctx)
        await analyzer.run_async(ctx)
        await stopper.run_async(ctx)

    import asyncio
    try:
        asyncio.run(_one_iter())
    except RuntimeError:
        # If we're (rarely) already inside a running loop, create a dedicated loop
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_one_iter())
        finally:
            loop.close()

    state = ctx.session_state
    timings = state.get("timings_ms", {})
    timings["watcher_ms"] = timings.get("watcher_ms_read", 0.0) + timings.get("watcher_ms_analyze", 0.0)
    timings["watcher_ms_total"] = (time.perf_counter() - t0) * 1000.0
    state["timings_ms"] = timings
    return state, timings
