# agents/coordinator.py
from __future__ import annotations

import time
from typing import Any, Dict, Tuple

from agents.watcher import run_watcher_once
from agents.parallel_pipeline import run_parallel_once

class Coordinator:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir

    def run_once(self, zip_code: str) -> Dict[str, Any]:
        t0 = time.perf_counter()

        # 1) Watcher (advisory + risk)
        state, watch_timings = run_watcher_once(self.data_dir, zip_code)
        state.setdefault("timings_ms", {}).update(watch_timings)

        # 2) Parallel (checklist + planner)
        state, par_timings = run_parallel_once(self.data_dir, zip_code, state)
        state["timings_ms"].update(par_timings)
        state["timings_ms"]["total_ms"] = (time.perf_counter() - t0) * 1000.0

        # 3) Return a UI-friendly result dict
        return {
            "advisory": state.get("advisory", {}),
            "advisory_raw": state.get("advisory_raw"),
            "analysis": state.get("analysis", {}),
            "analysis_explainer": state.get("risk_explainer"),
            "watcher_text": state.get("watcher_text"),
            "zip_point": state.get("zip_point"),
            "checklist": state.get("checklist", []),
            "plan": state.get("plan"),
            "errors": state.get("errors", {}),
            "debug": state.get("debug", {}),
            "timings_ms": state.get("timings_ms", {}),
        }
