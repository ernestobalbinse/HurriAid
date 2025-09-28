# agents/coordinator.py
from __future__ import annotations

import time
from typing import Dict, Any
from concurrent.futures import ThreadPoolExecutor

from agents.watcher import run_watcher_once
from agents.planner import plan_nearest_open_shelter
from agents.communicator import checklist_for_risk

# AI checklist (optional; falls back if not present)
try:
    from agents.ai_communicator import build_checklist_llm_agent
    from core.adk_helpers import ai_checklist
    _AI_CHECKLIST_OK = True
except Exception:
    _AI_CHECKLIST_OK = False


class Coordinator:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir

    def run_once(self, zip_code: str) -> Dict[str, Any]:
        t0 = time.perf_counter()
        result: Dict[str, Any] = {
            "advisory": {},
            "analysis": {},
            "plan": None,
            "checklist": [],
            "zip_point": None,
            "timings_ms": {},
            "errors": {},
        }

        # 1) Watcher (one iteration)
        state, watch_timings = run_watcher_once(self.data_dir, zip_code)
        advisory = state.get("advisory") or {}
        analysis = state.get("analysis") or {}
        zip_point = state.get("zip_point")
        watcher_text = state.get("watcher_text", "")
        active = bool(state.get("active", True))
        debug = state.get("debug") or {}
        flags = state.get("flags") or {}

        # Inactive -> SAFE + note
        if not active:
            advisory = {}
            if not analysis or analysis.get("risk") in (None, "—"):
                analysis = {"risk": "SAFE", "reason": "No active hurricane"}
            result["errors"]["watcher"] = "No active hurricane"

        # 2) Parallel: Planner + Communicator (AI checklist if available)
        plan = None
        checklist = []
        parallel_t0 = time.perf_counter()

        if analysis.get("risk") not in ("ERROR", None) and zip_point:
            def _timed(fn, *args, **kwargs):
                t = time.perf_counter()
                out = fn(*args, **kwargs)
                return out, (time.perf_counter() - t) * 1000.0

            with ThreadPoolExecutor(max_workers=2) as ex:
                f_plan = ex.submit(_timed, plan_nearest_open_shelter, zip_point, self.data_dir)

                def _ai_or_fallback():
                    if _AI_CHECKLIST_OK:
                        items = ai_checklist(zip_code, analysis.get("risk", ""), build_checklist_llm_agent)
                        if items:
                            return items
                    return checklist_for_risk(analysis.get("risk", ""))

                f_check = ex.submit(_timed, _ai_or_fallback)

                plan, planner_ms = f_plan.result()
                checklist, comm_ms = f_check.result()

            result["timings_ms"]["planner_ms"] = float(planner_ms)
            result["timings_ms"]["communicator_ms"] = float(comm_ms)

        result["timings_ms"]["parallel_ms"] = (time.perf_counter() - parallel_t0) * 1000.0

        # 3) Populate UI contract
        result["advisory"] = advisory
        result["analysis"] = analysis
        result["zip_point"] = zip_point
        result["plan"] = plan
        result["checklist"] = checklist
        result["watcher_text"] = watcher_text
        result["analysis_explainer"] = state.get("risk_explainer")  # already added earlier

        # NEW: forward watcher debug + flags so UI can inspect them
        if debug:
            result["debug"] = debug
        if flags:
            result.setdefault("flags", {}).update(flags)

        # 4) Timings
        result["timings_ms"].update(watch_timings)
        result["timings_ms"]["total_ms"] = (time.perf_counter() - t0) * 1000.0
        result["timings_ms"].setdefault("parallel_ms", 0.0)

        return result
