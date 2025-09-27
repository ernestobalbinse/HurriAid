# agents/coordinator.py — ADK-mandatory parallel run
from __future__ import annotations
from typing import Dict, Any, List
from time import perf_counter

from agents.watcher import Watcher
from agents.analyzer import assess_risk
from agents.planner import nearest_open_shelter
from agents.communicator import build_checklist
from agents.verifier_llm import verify_items_with_llm  # optional third task
from core.parallel_exec import ParallelRunner, ADKNotAvailable


class Coordinator:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.watcher = Watcher(data_dir=data_dir)
        try:
            self.runner = ParallelRunner()  # ADK required
        except ADKNotAvailable as e:
            # Let UI show the blocking banner and stop
            raise ADKNotAvailable(f"Google ADK is required: {e}") from e

    def run_once(self, zip_code: str) -> Dict[str, Any]:
        timings: Dict[str, int] = {}
        errors: Dict[str, str] = {}

        # 1) Load inputs (sequential, fast)
        t0 = perf_counter()
        try:
            advisory = self.watcher.get_advisory()
            zip_centroids = self.watcher.get_zip_centroids()
            shelters = self.watcher.get_shelters()
        except Exception as e:
            advisory, zip_centroids, shelters = {}, {}, []
            errors["watcher"] = str(e)
        timings["watcher_ms"] = round((perf_counter() - t0) * 1000)

        # 2) Wrap tools as no-arg callables for the parallel runner
        def _analyze() -> Dict[str, Any]:
            return assess_risk(zip_code, advisory, zip_centroids)

        def _plan() -> Dict[str, Any]:
            return nearest_open_shelter(zip_code, zip_centroids, shelters)

        def _verify_llm() -> Dict[str, Any]:
            base_items: List[str] = [
                "Open windows during hurricane",      # expect FALSE
                "Drink water",                         # expect TRUE (SAFE)
                "Taping windows prevents shattering",  # often MISLEADING
            ]
            return verify_items_with_llm(base_items)

        # 3) Run in parallel (Analyzer + Planner + Verifier)
        try:
            results, par_timings, par_errors = self.runner.run({
                "analyzer": _analyze,
                "planner": _plan,
                "verifier_llm": _verify_llm,   # remove if you don’t want LLM here
            })
        except ADKNotAvailable as e:
            errors["adk"] = f"ADK not available: {e}"
            results, par_timings, par_errors = {}, {}, {}
        except Exception as e:
            errors["adk"] = f"ADK execution failed: {e}"
            results, par_timings, par_errors = {}, {}, {}

        timings.update(par_timings or {})
        errors.update(par_errors or {})

        # Early exit if ADK failed
        if errors.get("adk"):
            return {
                "advisory": advisory,
                "analysis": None,
                "plan": None,
                "checklist": [],
                "verify": {"overall": "SKIPPED", "matches": []},
                "zip_valid": True,
                "zip_message": "",
                "zip_point": zip_centroids.get(zip_code) if isinstance(zip_centroids, dict) else None,
                "timings_ms": timings,
                "errors": errors,
            }

        # 4) Fan-in + post
        analysis = results.get("analyzer") or {}
        plan = results.get("planner") or None
        verify_llm = results.get("verifier_llm") or {"overall": "CLEAR", "matches": []}
        checklist = build_checklist(analysis)

        zip_valid = (analysis.get("risk") != "ERROR") if isinstance(analysis, dict) else True
        zip_message = analysis.get("reason", "") if isinstance(analysis, dict) else ""

        return {
            "advisory": advisory,
            "analysis": analysis,
            "plan": plan,
            "checklist": checklist,
            "verify": verify_llm,
            "zip_valid": zip_valid,
            "zip_message": zip_message,
            "zip_point": zip_centroids.get(zip_code) if isinstance(zip_centroids, dict) else None,
            "timings_ms": timings,
            "errors": errors,
        }
