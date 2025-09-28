# core/parallel_exec.py
from __future__ import annotations
from typing import Dict, Callable, Any, Tuple
from time import perf_counter
from concurrent.futures import ThreadPoolExecutor, as_completed

class ADKNotAvailable(RuntimeError):
    """Raised when Google ADK is required but not installed/configured."""
    pass


class ParallelRunner:
    """
    Tiny, no-frills parallel executor for local tasks.

    What it does:
    - Runs a dict of callables in threads.
    - Captures each task's result OR error without stopping the others.
    - Measures per-task runtime (ms) and a total "parallel_ms".
    - Returns three dicts: results, timings, errors.

    Use this when you want quick concurrency without extra dependencies.
    """
    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers

    def run(self, tasks: Dict[str, Callable[[], Any]]) -> Tuple[Dict[str, Any], Dict[str, float], Dict[str, str]]:
        results: Dict[str, Any] = {}
        timings: Dict[str, float] = {}
        errors:  Dict[str, str] = {}

        t0 = perf_counter()

        def _wrap(name: str, fn: Callable[[], Any]) -> Dict[str, Any]:
            start = perf_counter()
            try:
                out = fn()
                elapsed_ms = (perf_counter() - start) * 1000.0
                return {"ok": True, "name": name, "out": out, "elapsed_ms": elapsed_ms}
            except Exception as e:
                elapsed_ms = (perf_counter() - start) * 1000.0
                return {"ok": False, "name": name, "err": f"{type(e).__name__}: {e}", "elapsed_ms": elapsed_ms}

        # Fire off all tasks and collect as they finish.
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            fut_map = {ex.submit(_wrap, name, fn): name for name, fn in tasks.items()}
            for fut in as_completed(fut_map):
                r = fut.result()
                name = r["name"]
                timings[f"{name}_ms"] = round(float(r["elapsed_ms"]), 2)  # two-decimal precision
                if r["ok"]:
                    results[name] = r["out"]
                else:
                    errors[name] = r["err"]

        timings["parallel_ms"] = round((perf_counter() - t0) * 1000.0, 2)
        return results, timings, errors
