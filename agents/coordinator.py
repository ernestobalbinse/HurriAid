from __future__ import annotations
from typing import Dict, Any
from time import perf_counter

from agents.watcher import Watcher
from agents.analyzer import assess_risk
from agents.planner import nearest_open_shelter
from agents.communicator import build_checklist
from core.parallel_exec import ParallelRunner

class Coordinator:
    def __init__(self, data_dir: str = "data", use_adk_preferred: bool = True):
        self.watcher = Watcher(data_dir=data_dir)
        self.runner = ParallelRunner(use_adk_preferred=use_adk_preferred)

    def run_once(self, zip_code: str) -> Dict[str, Any]:
        timings, errors = {}, {}

        # 1) Load local data (fast)
        t0 = perf_counter()
        try:
            advisory = self.watcher.get_advisory()
            zip_centroids = self.watcher.get_zip_centroids()
            shelters = self.watcher.get_shelters()
        except Exception as e:
            advisory, zip_centroids, shelters = {}, {}, []
            errors["watcher"] = str(e)
        timings["watcher_ms"] = round((perf_counter() - t0) * 1000)

        # 2) Fan-out: run Analyzer + Planner in parallel
        def _analyze():
            return assess_risk(zip_code, advisory, zip_centroids)
        def _plan():
            return nearest_open_shelter(zip_code, zip_centroids, shelters)
        results, par_timings, par_errors = self.runner.run({
            "analyzer": _analyze,
            "planner": _plan,
        })
        analysis = results.get("analyzer") or {}
        plan = results.get("planner")

        # 3) Fan-in: build checklist (risk-aware)
        checklist = build_checklist(analysis)

        timings.update(par_timings)
        errors.update(par_errors)

        return {
            "advisory": advisory,
            "analysis": analysis,
            "plan": plan,
            "checklist": checklist,
            "timings_ms": timings,
            "errors": errors,
        }