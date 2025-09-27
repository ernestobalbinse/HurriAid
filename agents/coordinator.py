from __future__ import annotations
from typing import Dict, Any
from time import perf_counter

from agents.watcher import Watcher
from agents.analyzer import assess_risk
from agents.planner import nearest_open_shelter
from agents.communicator import build_checklist
from agents.verifier import Verifier
from core.parallel_exec import ParallelRunner
from core.utils import validate_zip, append_history

class Coordinator:
    def __init__(self, data_dir: str = "data", use_adk_preferred: bool = True):
        self.watcher = Watcher(data_dir=data_dir)
        self.verifier = Verifier(data_dir=data_dir)
        self.runner = ParallelRunner(use_adk_preferred=use_adk_preferred)

    def run_once(self, zip_code: str, offline: bool = True) -> Dict[str, Any]:
        timings = {}
        errors = {}

        # Load data
        t0 = perf_counter()
        try:
            advisory = self.watcher.get_advisory(offline=offline)
            zip_centroids = self.watcher.get_zip_centroids()
            shelters = self.watcher.get_shelters()
        except Exception as e:
            advisory, zip_centroids, shelters = {}, {}, []
            errors["watcher"] = str(e)
        timings["watcher_ms"] = round((perf_counter() - t0) * 1000)

        # Validate ZIP
        is_valid_zip, msg = validate_zip(zip_code, zip_centroids)

        # Prepare tasks (Analyzer/Planner only run meaningfully if ZIP exists)
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
        checklist = build_checklist(analysis)

        # Verifier (simple demo text; in a real app, this could check social posts or user input)
        # Here we just derive a short text from the checklist to simulate a check.
        verify_text = " ".join(checklist).lower()
        verify_result = self.verifier.check(verify_text)

        timings.update(par_timings)
        errors.update(par_errors)

        out = {
            "advisory": advisory,
            "analysis": analysis,
            "plan": plan,
            "checklist": checklist,
            "verify": verify_result,
            "zip_valid": is_valid_zip,
            "zip_message": msg,
            "timings_ms": timings,
            "errors": errors,
        }

        # Persist minimal history row
        try:
            row = {
            "zip": zip_code,
            "risk": analysis.get("risk", "—"),
            "eta": (plan or {}).get("eta_min", "—"),
            }
            append_history(row)
        except Exception:
            pass

        return out
