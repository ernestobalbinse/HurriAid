# agents/coordinator.py — Live Watcher + FL ZIP resolver
from __future__ import annotations
from typing import Dict, Any
from time import perf_counter

from agents.watcher import Watcher
from agents.analyzer import assess_risk
from agents.planner import nearest_open_shelter
from agents.communicator import build_checklist
from agents.verifier_llm import verify_items_with_llm
from core.parallel_exec import ParallelRunner, ADKNotAvailable

# NEW: allow any Florida ZIP by resolving ZIP -> lat/lon on the fly
from tools.zip_resolver import resolve_fl_zip, ZipNotFound, ZipNotInFlorida


class Coordinator:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.watcher = Watcher(data_dir=data_dir)
        self.runner = ParallelRunner(max_workers=3)

    def run_once(self, zip_code: str) -> Dict[str, Any]:
        t_total0 = perf_counter()
        timings: Dict[str, float] = {}
        errors: Dict[str, str] = {}

        # 1) Load advisory, centroids, shelters (precise ms)
        t0 = perf_counter()
        try:
            advisory = self.watcher.get_advisory()
            zip_centroids = self.watcher.get_zip_centroids()
            shelters = self.watcher.get_shelters()
        except Exception as e:
            advisory, zip_centroids, shelters = {}, {}, []
            errors["watcher"] = str(e)
        timings["watcher_ms"] = round((perf_counter() - t0) * 1000.0, 2)

        # Ensure dict shape even on watcher error
        if not isinstance(zip_centroids, dict):
            zip_centroids = {}

        # 2) Resolve/validate ZIP (accept any Florida ZIP)
        t1 = perf_counter()
        zip_valid = True
        zip_message = ""
        zip_point = zip_centroids.get(zip_code)

        if zip_point is None:
            try:
                # Will raise if invalid/unknown or not in FL
                resolved = resolve_fl_zip(zip_code)
                zip_point = {"lat": resolved["lat"], "lon": resolved["lon"]}
                # Inject into centroids so downstream modules work unchanged
                zip_centroids[zip_code] = zip_point
            except ZipNotFound as e:
                zip_valid = False
                zip_message = str(e)
            except ZipNotInFlorida as e:
                zip_valid = False
                zip_message = str(e)
            except Exception as e:
                zip_valid = False
                zip_message = f"ZIP resolution failed: {e}"

        timings["zip_resolve_ms"] = round((perf_counter() - t1) * 1000.0, 2)

        # If ZIP is invalid or not in Florida, short-circuit with a clear message
        if not zip_valid:
            timings["total_ms"] = round((perf_counter() - t_total0) * 1000.0, 2)
            return {
                "advisory": advisory,
                "analysis": {"risk": "ERROR", "reason": zip_message or "Invalid ZIP."},
                "plan": None,
                "checklist": [],
                "verify": {"overall": "CLEAR", "matches": []},
                "zip_valid": False,
                "zip_message": zip_message,
                "zip_point": None,
                "timings_ms": timings,
                "errors": errors,
            }

        # 3) Prepare tasks for parallel run (same signatures as before)
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

        # 4) Run in parallel (ParallelRunner should return per-task timings/errors)
        results, par_timings, par_errors = self.runner.run({
            "analyzer": _analyze,
            "planner": _plan,
            "verifier_llm": _verify_llm,
        })
        timings.update(par_timings or {})
        errors.update(par_errors or {})

        # 5) Fan-in
        analysis = results.get("analyzer") or {}
        plan = results.get("planner")
        verify_llm = results.get("verifier_llm") or {"overall": "CLEAR", "matches": []}
        checklist = build_checklist(analysis)

        timings["total_ms"] = round((perf_counter() - t_total0) * 1000.0, 2)

        return {
            "advisory": advisory,
            "analysis": analysis,
            "plan": plan,
            "checklist": checklist,
            "verify": verify_llm,
            "zip_valid": True,
            "zip_message": "",
            "zip_point": zip_point,
            "timings_ms": timings,
            "errors": errors,
        }
