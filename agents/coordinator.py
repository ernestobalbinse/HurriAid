from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter
from typing import Dict, Any


from agents.watcher import Watcher
from agents.analyzer import assess_risk
from agents.planner import nearest_open_shelter


class Coordinator:
	def __init__(self, data_dir: str = "data", max_workers: int = 3):
		self.watcher = Watcher(data_dir=data_dir)
		self.max_workers = max_workers

	def run_once(self, zip_code: str) -> Dict[str, Any]:
		t0 = perf_counter()
		timings = {}
		errors = {}

		# 1) Fan‑out: load static data synchronously (fast/local), then run compute agents in parallel
		try:
			t = perf_counter()
			advisory = self.watcher.get_advisory()
			zip_centroids = self.watcher.get_zip_centroids()
			shelters = self.watcher.get_shelters()
			timings["watcher_ms"] = round((perf_counter() - t) * 1000)
		except Exception as e:
			errors["watcher"] = str(e)
			advisory, zip_centroids, shelters = {}, {}, []

		analysis = None
		plan = None

		# 2) Parallel run of Analyzer + Planner
		def _run_analyzer():
			t = perf_counter()
			try:
				res = assess_risk(zip_code, advisory, zip_centroids)
				return ("analyzer", res, round((perf_counter() - t) * 1000), None)
			except Exception as e:
				return ("analyzer", None, round((perf_counter() - t) * 1000), str(e))

		def _run_planner():
			t = perf_counter()
			try:
				res = nearest_open_shelter(zip_code, zip_centroids, shelters)
				return ("planner", res, round((perf_counter() - t) * 1000), None)
			except Exception as e:
				return ("planner", None, round((perf_counter() - t) * 1000), str(e))

		with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
			futures = [ex.submit(_run_analyzer), ex.submit(_run_planner)]
			for fut in as_completed(futures):
				name, res, ms, err = fut.result()
				timings[f"{name}_ms"] = ms
				if err:
					errors[name] = err
				else:
					if name == "analyzer":
						analysis = res
					elif name == "planner":
						plan = res

		timings["total_ms"] = round((perf_counter() - t0) * 1000)

		return {
			"advisory": advisory,
			"analysis": analysis,
			"plan": plan,
			"timings_ms": timings,
			"errors": errors,
		}