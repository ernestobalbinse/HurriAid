# agents/coordinator.py (relevant bits)
from core.parallel_exec import ParallelRunner, ADKNotAvailable
from agents.watcher import Watcher
from agents.analyzer import assess_risk
from agents.planner import nearest_open_shelter
from agents.communicator import build_checklist
from agents.verifier_llm import verify_items_with_llm

class Coordinator:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.watcher = Watcher(data_dir=data_dir)
        self.runner = ParallelRunner()  # ← neutral runner

    def run_once(self, zip_code: str):
        timings, errors = {}, {}

        # load data (watcher)
        from time import perf_counter
        t0 = perf_counter()
        try:
            advisory = self.watcher.get_advisory()
            zip_centroids = self.watcher.get_zip_centroids()
            shelters = self.watcher.get_shelters()
        except Exception as e:
            advisory, zip_centroids, shelters = {}, {}, []
            errors["watcher"] = str(e)
        timings["watcher_ms"] = round((perf_counter() - t0) * 1000)

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

        results, par_timings, par_errors = self.runner.run({
            "analyzer": _analyze,
            "planner": _plan,
            "verifier_llm": _verify_llm,
        })
        timings.update(par_timings)
        errors.update(par_errors)

        analysis = results.get("analyzer") or {}
        plan     = results.get("planner")
        verify   = results.get("verifier_llm") or {"overall": "CLEAR", "matches": []}
        checklist = build_checklist(analysis)

        return {
            "advisory": advisory,
            "analysis": analysis,
            "plan": plan,
            "checklist": checklist,
            "verify": verify,
            "zip_valid": True,
            "zip_message": "",
            "zip_point": zip_centroids.get(zip_code) if isinstance(zip_centroids, dict) else None,
            "timings_ms": timings,
            "errors": errors,
        }
