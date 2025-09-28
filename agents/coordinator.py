# agents/coordinator.py
from __future__ import annotations
import time
from typing import Dict, Any
from concurrent.futures import ThreadPoolExecutor

from agents.watcher import run_watcher_once
from agents.planner import plan_nearest_open_shelter
from agents.communicator import checklist_for_risk

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

        # 1) Watcher (produces advisory, analysis, zip_point)
        state, watch_timings = run_watcher_once(self.data_dir, zip_code)
        advisory = state.get("advisory") or {}
        analysis = state.get("analysis") or {}
        zip_point = state.get("zip_point")
        watcher_text = state.get("watcher_text", "")
        active = bool(state.get("active", True))

        # Handle inactive
        if not active:
            advisory = {}
            if not analysis or analysis.get("risk") in (None, "—"):
                analysis = {"risk": "SAFE", "reason": "No active hurricane"}
            result["errors"]["watcher"] = "No active hurricane"

        # 2) Parallel: Planner + Communicator (only if ZIP/risk is valid)
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
                f_check = ex.submit(_timed, checklist_for_risk, analysis.get("risk", ""))

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
        result["watcher_text"] = watcher_text  # available if you choose to render it

        # 4) Timings
        result["timings_ms"].update(watch_timings)
        result["timings_ms"]["total_ms"] = (time.perf_counter() - t0) * 1000.0
        # ensure key exists even if no parallel work ran
        result["timings_ms"].setdefault("parallel_ms", 0.0)

        return result
