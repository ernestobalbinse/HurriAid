# agents/coordinator.py
from __future__ import annotations
from typing import Dict, Any, Tuple

from agents.watcher import run_watcher_once

class Coordinator:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir

    def run_once(self, zip_code: str) -> Dict[str, Any]:
        state, timings = run_watcher_once(self.data_dir, zip_code)

        # Assemble a single result dict the UI can consume
        result: Dict[str, Any] = {
            "advisory_raw": state.get("advisory_raw") or {},
            "advisory": state.get("advisory") or {},
            "analysis": state.get("analysis") or {},
            "watcher_text": state.get("watcher_text"),
            "zip_point": state.get("zip_point"),
            "risk_explainer": state.get("risk_explainer"),
            "analysis_explainer": state.get("analysis_explainer"),
            "timings_ms": state.get("timings_ms") or timings or {},
            "debug": state.get("debug") or {},
            # keep placeholders so UI doesn't break if you add these later:
            "plan": state.get("plan"),
            "errors": state.get("errors") or {},
        }
        return result
