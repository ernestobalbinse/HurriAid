# agents/parallel_pipeline.py
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Dict, List, Tuple

from agents.watcher import _resolve_zip_latlon, _PGEOCODE_AVAILABLE  # reuse ZIP -> lat/lon
from agents.ai_planner import build_planner_agent
from agents.ai_checklist import build_checklist_agent
from core.adk_helpers import run_llm_agent_text_debug
from core.shelters import read_shelters, SheltersError

__all__ = ["run_parallel_once"]

# -------- JSON helpers --------

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_JSON_ARRAY_RE  = re.compile(r"\[.*\]", re.DOTALL)

def _strip_fences(s: str) -> str:
    if not isinstance(s, str):
        return ""
    t = s.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:].lstrip()
    return t.strip()

def _extract_json_object(s: str) -> str:
    t = _strip_fences(s)
    m = _JSON_OBJECT_RE.search(t)
    return m.group(0).strip() if m else ""

def _extract_json_array(s: str) -> str:
    t = _strip_fences(s)
    m = _JSON_ARRAY_RE.search(t)
    return m.group(0).strip() if m else ""


# -------- Checklist (AI) --------

async def _run_checklist(state: Dict[str, Any], timings: Dict[str, float]) -> None:
    t0 = time.perf_counter()
    dbg = state.setdefault("debug", {})

    analysis = state.get("analysis") or {}
    advisory = state.get("advisory") or {}
    if not analysis:
        state.setdefault("errors", {})["checklist"] = "No analysis in state."
        timings["checklist_ms"] = (time.perf_counter() - t0) * 1000.0
        return

    zip_code  = state.get("zip_code") or ""
    risk      = str(analysis.get("risk", ""))
    dist_km   = analysis.get("distance_km")
    radius_km = advisory.get("radius_km")
    category  = advisory.get("category")

    facts_lines = [
        f"zip={zip_code}",
        f"risk={risk}",
        f"distance_km={dist_km}",
        f"radius_km={radius_km}",
        f"category={category}",
    ]
    prompt = "Facts:\n" + "\n".join(facts_lines)

    agent = build_checklist_agent()
    text, events, err = await asyncio.to_thread(
        run_llm_agent_text_debug,
        agent, prompt, "hurri_aid", "ui_user", "sess_checklist"
    )

    dbg["checklist_prompt"] = prompt
    dbg["checklist_raw"]    = text
    dbg["checklist_error"]  = err
    dbg["checklist_events"] = events

    if err:
        state.setdefault("errors", {})["checklist"] = str(err)
        timings["checklist_ms"] = (time.perf_counter() - t0) * 1000.0
        return

    items: List[str] = []
    raw_arr = _extract_json_array(text or "")
    if raw_arr:
        try:
            parsed = json.loads(raw_arr)
            if isinstance(parsed, list):
                items = [str(x).strip() for x in parsed if str(x).strip()]
        except Exception as e:
            state.setdefault("errors", {})["checklist"] = f"Invalid checklist JSON: {e}"
            timings["checklist_ms"] = (time.perf_counter() - t0) * 1000.0
            return
    else:
        for line in (text or "").splitlines():
            line = line.strip()
            if line.startswith("- "):
                items.append(line[2:].strip())

    state["checklist"] = items
    timings["checklist_ms"] = (time.perf_counter() - t0) * 1000.0


# -------- Planner (AI) --------

async def _run_planner(state: Dict[str, Any], data_dir: str, zip_code: str, timings: Dict[str, float]) -> None:
    t0 = time.perf_counter()
    dbg = state.setdefault("debug", {})

    # Ensure ZIP coordinates available
    zp = state.get("zip_point")
    if not (isinstance(zp, dict) and "lat" in zp and "lon" in zp):
        coords = _resolve_zip_latlon(zip_code)
        if coords is None:
            reason = "pgeocode not installed" if not _PGEOCODE_AVAILABLE else f"Unknown ZIP {zip_code}"
            state.setdefault("errors", {})["planner"] = reason
            state["plan"] = None
            timings["planner_ms"] = (time.perf_counter() - t0) * 1000.0
            return
        zlat, zlon = coords
        state["zip_point"] = {"lat": zlat, "lon": zlon}
    else:
        zlat, zlon = float(zp["lat"]), float(zp["lon"])

    # Load shelters.json and attach file debug
    try:
        shelters, sdbg = read_shelters(data_dir)
        dbg.update(sdbg)  # shelters_path / shelters_sha256 / shelters_mtime
    except SheltersError as e:
        state.setdefault("errors", {})["planner"] = str(e)
        state["plan"] = None
        timings["planner_ms"] = (time.perf_counter() - t0) * 1000.0
        return

    shelters_json = json.dumps(shelters, separators=(",", ":"))
    prompt = f"zip_lat={zlat} zip_lon={zlon}\nshelters_json={shelters_json}"

    agent = build_planner_agent()
    text, events, err = await asyncio.to_thread(
        run_llm_agent_text_debug, agent, prompt,
        "hurri_aid", "ui_user", "sess_planner"
    )

    dbg["planner_prompt"] = prompt
    dbg["planner_raw"] = text
    dbg["planner_error"] = err
    dbg["planner_events"] = events

    if err:
        state.setdefault("errors", {})["planner"] = str(err)
        state["plan"] = None
        timings["planner_ms"] = (time.perf_counter() - t0) * 1000.0
        return

    raw_json = _extract_json_object(text or "")
    if not raw_json:
        state.setdefault("errors", {})["planner"] = "Planner returned no JSON."
        state["plan"] = None
        timings["planner_ms"] = (time.perf_counter() - t0) * 1000.0
        return

    try:
        obj = json.loads(raw_json)
    except Exception as e:
        state.setdefault("errors", {})["planner"] = f"Invalid planner JSON: {e}"
        state["plan"] = None
        timings["planner_ms"] = (time.perf_counter() - t0) * 1000.0
        return

    try:
        name = str(obj.get("name", "")).strip()
        lat = float(obj.get("lat", 0.0))
        lon = float(obj.get("lon", 0.0))
        dist = float(obj.get("distance_km", -1.0))
        eta  = int(obj.get("eta_min", -1))
        if name and dist >= 0 and eta >= 0:
            state["plan"] = {
                "name": name, "lat": lat, "lon": lon,
                "distance_km": round(dist, 1), "eta_min": eta
            }
        else:
            state.setdefault("errors", {})["planner"] = "No open shelter in AI result."
            state["plan"] = None
    except Exception as e:
        state.setdefault("errors", {})["planner"] = f"Planner fields missing: {e}"
        state["plan"] = None

    timings["planner_ms"] = (time.perf_counter() - t0) * 1000.0


# -------- Orchestrator --------
def _run_coro(coro):
    """Run an async coroutine safely from sync context (Streamlit)."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

def _run_parallel_core(data_dir: str, state: Dict[str, Any], zip_code: str) -> Tuple[Dict[str, Any], Dict[str, float]]:
    t0_total = time.perf_counter()
    timings: Dict[str, float] = {}

    if zip_code and not state.get("zip_code"):
        state["zip_code"] = zip_code

    async def _run_both():
        await asyncio.gather(
            _run_checklist(state, timings),
            _run_planner(state, data_dir, zip_code, timings),
        )

    _run_coro(_run_both())

    timings["parallel_ms"] = (time.perf_counter() - t0_total) * 1000.0
    return state, timings

def run_parallel_once(data_dir: str, *args) -> Tuple[Dict[str, Any], Dict[str, float]]:
    """
    Backward-compatible entry point.
    Accepts either:
      - (data_dir, state, zip_code)  <-- preferred
      - (data_dir, zip_code, state)  <-- tolerated (old callsites)
    """
    if len(args) != 2:
        raise TypeError("run_parallel_once expects 3 args: (data_dir, state, zip_code)")

    a, b = args
    # Detect which arg is state
    if isinstance(a, dict) and isinstance(b, str):
        state, zip_code = a, b
    elif isinstance(a, str) and isinstance(b, dict):
        zip_code, state = a, b
    else:
        raise TypeError("run_parallel_once signature must be (data_dir, state: dict, zip_code: str)")

    return _run_parallel_core(data_dir, state, zip_code)
