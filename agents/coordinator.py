# agents/coordinator.py — pgeocode ZIP + JSON advisory/shelters, map-friendly outputs
from __future__ import annotations
from typing import Dict, Any
from time import perf_counter

from agents.watcher import Watcher
from agents.analyzer import assess_risk
from agents.planner import nearest_open_shelter
from agents.communicator import build_checklist
# If you previously removed ParallelRunner, keep your current runner;
# this version runs analyzer/planner sequentially for simplicity/stability.
# from core.parallel_exec import ParallelRunner, ADKNotAvailable


class Coordinator:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.watcher = Watcher(data_dir=data_dir, fl_only=True)

    def run_once(self, zip_code: str) -> Dict[str, Any]:
        timings: Dict[str, float] = {}
        errors: Dict[str, str] = {}
        result: Dict[str, Any] = {}

        # 1) Load advisory & shelters
        t0 = perf_counter()
        try:
            advisory = self.watcher.get_advisory()
        except Exception as e:
            advisory = {}
            errors["watcher"] = f"Advisory: {e}"
        try:
            shelters = self.watcher.get_shelters()
        except Exception as e:
            shelters = []
            errors["watcher"] = (errors.get("watcher", "") + f" | Shelters: {e}").strip()
        timings["watcher_ms"] = round((perf_counter() - t0) * 1000.0, 2)

        # 2) Analyzer (uses watcher for reliable zip centroid)
        t1 = perf_counter()
        try:
            analysis = assess_risk(zip_code, advisory, self.watcher)
        except Exception as e:
            analysis = {"risk": "ERROR", "reason": str(e)}
            errors["analyzer"] = str(e)
        timings["analyzer_ms"] = round((perf_counter() - t1) * 1000.0, 2)

        # 3) Planner (uses watcher + shelters)
        t2 = perf_counter()
        try:
            plan = None if analysis.get("risk") == "ERROR" else nearest_open_shelter(zip_code, self.watcher, shelters)
        except Exception as e:
            plan = None
            errors["planner"] = str(e)
        timings["planner_ms"] = round((perf_counter() - t2) * 1000.0, 2)

        # 4) Checklist derived from analysis
        checklist = build_checklist(analysis)

        # 5) zip_point for UI map (even if analyzer errored, try to provide a point)
        try:
            zp = self.watcher.get_zip_centroid(zip_code)
            zip_point = {"lat": zp["lat"], "lon": zp["lon"]}
        except Exception:
            zip_point = None

        # Total timing (simple sum since sequential here)
        timings["total_ms"] = round(sum([
            timings.get("watcher_ms", 0.0),
            timings.get("analyzer_ms", 0.0),
            timings.get("planner_ms", 0.0),
        ]), 2)

        return {
            "advisory": advisory,          # <-- has "center" + "radius_km" for the map
            "analysis": analysis,
            "plan": plan,
            "checklist": checklist,
            "verify": {"overall": "CLEAR", "matches": []},  # UI triggers LLM manually; no background calls
            "zip_point": zip_point,        # <-- {lat, lon} for the UI dot
            "timings_ms": timings,
            "errors": errors,
        }
