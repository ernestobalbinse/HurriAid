# agents/coordinator.py
from __future__ import annotations

import time
from typing import Dict, Any, Optional

# Importing watcher here ensures ADK presence is checked during Coordinator construction.
# If ADK is missing, agents/watcher.py will raise ADKNotAvailable and your UI will show a clear error.
from agents.watcher import run_watcher_once

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

        # 1) Watcher (LoopAgent) — one deterministic iteration per UI click
        state, watch_timings = run_watcher_once(self.data_dir, zip_code)

        advisory = state.get("advisory") or {}
        analysis = state.get("analysis") or {}
        zip_point = state.get("zip_point")
        watcher_text = state.get("watcher_text", "")
        active = bool(state.get("active", True))

        # 2) Handle "no active hurricane" → keep UI minimal + clear message in Agent Status
        if not active:
            # We won’t draw the advisory polygon when inactive
            advisory = {}
            # Nudge risk to SAFE if analyzer didn’t set one
            if not analysis or analysis.get("risk") in (None, "—"):
                analysis = {"risk": "SAFE", "reason": "No active hurricane"}
            # Surface a simple note under Agent Status (your UI already shows per-agent errors there)
            result["errors"]["watcher"] = "No active hurricane"

        # 3) Populate the result the UI already expects
        result["advisory"] = advisory
        result["analysis"] = analysis
        result["zip_point"] = zip_point
        result["watcher_text"] = watcher_text  # not rendered yet; we’ll use this in Step 3

        # (Planner/checklist will be filled in later steps)
        result["plan"] = None
        result["checklist"] = []

        # 4) Timings
        result["timings_ms"].update(watch_timings)
        result["timings_ms"]["parallel_ms"] = result["timings_ms"].get("parallel_ms", 0.0)
        result["timings_ms"]["total_ms"] = (time.perf_counter() - t0) * 1000.0

        return result
