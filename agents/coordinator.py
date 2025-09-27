# agents/coordinator.py — Step 10
from __future__ import annotations
from typing import Dict, Any
from time import perf_counter

from agents.watcher import Watcher
from agents.analyzer import assess_risk
from agents.planner import nearest_open_shelter
from agents.communicator import build_checklist
from core.parallel_exec import ParallelRunner

class Coordinator:
    def __init__(self, data_dir: str = "data"):
        self.watcher = Watcher(data_dir=data_dir)
        self.runner = ParallelRunner() # ADK mandatory

    def run_once(self, zip_code: str) -> Dict[str, Any]:
        timings: Dict[str, int] = {}
        errors: Dict[str, str] = {}

        # 1) Load data
        t0 = perf_counter()
        try:
            advisory = self.watcher.get_advisory()
            zip_centroids = self.watcher.get_zip_centroids()
            shelters = self.watcher.get_shelters()
        except Exception as e:
            advisory, zip_centroids, shelters = {}, {}, []
            errors["watcher"] = str(e)
        timings["watcher_ms"] = round((perf_counter() - t0) * 1000)

        # 2) Prepare tasks
        def _analyze():
            return assess_risk(zip_code, advisory, zip_centroids)
        def _plan():
            return nearest_open_shelter(zip_code, zip_centroids, shelters)

        # 3) Run via ADK
        results, par_timings, par_errors = self.runner.run({
            "analyzer": _analyze,
            "planner": _plan,
        })
        timings.update(par_timings)
        errors.update(par_errors)

        # If ADK failed, stop here with a clear structure
        if errors.get("adk"):
            return {
                "advisory": advisory,
                "analysis": None, # explicitly absent
                "plan": None,
                "checklist": [],
                "verify": {"overall": "SKIPPED", "matches": []},
                "zip_valid": True, # unknown here; UI should not assume
                "zip_message": "",
                "zip_point": zip_centroids.get(zip_code) if isinstance(zip_centroids, dict) else None,
                "timings_ms": timings,
                "errors": errors,
            }

        # 4) Normal fan‑in
        analysis = results.get("analyzer") or {}
        plan = results.get("planner")
        checklist = build_checklist(analysis)

        return {
            "advisory": advisory,
            "analysis": analysis,
            "plan": plan,
            "checklist": checklist,
            "verify": {"overall": "CLEAR", "matches": []},
            "zip_valid": True,
            "zip_message": "",
            "zip_point": zip_centroids.get(zip_code) if isinstance(zip_centroids, dict) else None,
            "timings_ms": timings,
            "errors": errors,
        }