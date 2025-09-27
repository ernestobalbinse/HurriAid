# agents/coordinator.py — Live Watcher support
from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
from time import perf_counter

from agents.watcher import Watcher
from agents.analyzer import assess_risk
from agents.planner import nearest_open_shelter
from agents.communicator import build_checklist
from agents.verifier_llm import verify_items_with_llm
from core.parallel_exec import ParallelRunner, ADKNotAvailable


class Coordinator:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.watcher = Watcher(data_dir=data_dir)          # <-- make sure Watcher is available
        self.runner = ParallelRunner()                     # <-- ADK mandatory

    # ---- fast risk probe (cheap) ----
    def probe_risk(self, zip_code: str) -> Tuple[Dict, Dict, Dict, List[Dict]]:
        """
        Return (analysis_only, advisory, zip_centroids, shelters) without running planner/LLM.
        """
        advisory = self.watcher.get_advisory()
        zip_centroids = self.watcher.get_zip_centroids()
        shelters = self.watcher.get_shelters()
        analysis = assess_risk(zip_code, advisory, zip_centroids)
        return analysis, advisory, zip_centroids, shelters

    # ---- full fan-out run (Analyzer + Planner + LLM) ----
    def _fan_out(self, zip_code: str,
                 advisory: Dict, zip_centroids: Dict, shelters: List[Dict]) -> Tuple[Dict[str, Any], Dict[str, str], Dict[str, int]]:
        timings: Dict[str, int] = {}
        errors: Dict[str, str] = {}

        # prepare tasks that close over already-loaded data
        def _analyze():
            return assess_risk(zip_code, advisory, zip_centroids)

        def _plan():
            return nearest_open_shelter(zip_code, zip_centroids, shelters)

        def _verify_llm():
            base_items = [
                "Open windows during hurricane",
                "Drink water",
                "Taping windows prevents shattering",
            ]
            return verify_items_with_llm(base_items)

        t0 = perf_counter()
        results, par_timings, par_errors = self.runner.run({
            "analyzer": _analyze,
            "planner": _plan,
            "verifier_llm": _verify_llm,
        })
        timings.update(par_timings)
        errors.update(par_errors)
        timings["parallel_ms"] = round((perf_counter() - t0) * 1000)

        return results, errors, timings

    # ---- public: one-shot (unchanged behavior) ----
    def run_once(self, zip_code: str) -> Dict[str, Any]:
        t0 = perf_counter()
        timings: Dict[str, int] = {}
        errors: Dict[str, str] = {}

        try:
            analysis, advisory, zip_centroids, shelters = self.probe_risk(zip_code)
        except Exception as e:
            return {
                "advisory": {},
                "analysis": {"risk": "ERROR", "reason": str(e)},
                "plan": None,
                "checklist": [],
                "verify": {"overall": "CLEAR", "matches": []},
                "zip_valid": False,
                "zip_message": "",
                "zip_point": None,
                "timings_ms": {"total_ms": round((perf_counter() - t0) * 1000)},
                "errors": {"watcher": str(e)},
            }
        timings["watcher_ms"] = round((perf_counter() - t0) * 1000)

        # fan-out
        results, perr, ptim = self._fan_out(zip_code, advisory, zip_centroids, shelters)
        errors.update(perr)
        timings.update(ptim)

        if errors.get("adk"):
            return {
                "advisory": advisory,
                "analysis": None,
                "plan": None,
                "checklist": [],
                "verify": {"overall": "SKIPPED", "matches": []},
                "zip_valid": True,
                "zip_message": "",
                "zip_point": zip_centroids.get(zip_code),
                "timings_ms": timings | {"total_ms": round((perf_counter() - t0) * 1000)},
                "errors": errors,
            }

        analysis = results.get("analyzer") or {}
        plan = results.get("planner")
        verify_llm = results.get("verifier_llm") or {"overall": "CLEAR", "matches": []}
        checklist = build_checklist(analysis)

        return {
            "advisory": advisory,
            "analysis": analysis,
            "plan": plan,
            "checklist": checklist,
            "verify": verify_llm,
            "zip_valid": True,
            "zip_message": "",
            "zip_point": (zip_centroids.get(zip_code) if isinstance(zip_centroids, dict) else None),
            "timings_ms": timings | {"total_ms": round((perf_counter() - t0) * 1000)},
            "errors": errors,
        }

    # ---- public: run only when risk changes (for Live Watcher) ----
    def run_if_risk_changed(self, zip_code: str, prev_risk: Optional[str]) -> Dict[str, Any]:
        """
        Probe risk. If it differs from prev_risk (and not ERROR), trigger full fan-out.
        Otherwise return a lightweight payload with current analysis/advisory.
        """
        t0 = perf_counter()
        analysis, advisory, zip_centroids, shelters = self.probe_risk(zip_code)
        current = analysis.get("risk")
        changed = (current != prev_risk) and (current != "ERROR")

        if not changed:
            return {
                "changed": False,
                "advisory": advisory,
                "analysis": analysis,
                "plan": None,
                "checklist": [],
                "verify": {"overall": "CLEAR", "matches": []},
                "zip_point": zip_centroids.get(zip_code),
                "timings_ms": {"watcher_ms": round((perf_counter() - t0) * 1000)},
                "errors": {},
            }

        # risk changed → run full fan-out
        results, errors, ptim = self._fan_out(zip_code, advisory, zip_centroids, shelters)
        analysis2 = results.get("analyzer") or analysis
        plan = results.get("planner")
        verify_llm = results.get("verifier_llm") or {"overall": "CLEAR", "matches": []}
        checklist = build_checklist(analysis2)

        return {
            "changed": True,
            "advisory": advisory,
            "analysis": analysis2,
            "plan": plan,
            "checklist": checklist,
            "verify": verify_llm,
            "zip_point": zip_centroids.get(zip_code),
            "timings_ms": ptim | {"watcher_ms": ptim.get("watcher_ms", 0)},
            "errors": errors,
        }
