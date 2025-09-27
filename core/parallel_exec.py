from __future__ import annotations
from typing import Callable, Dict, Any, Tuple
from time import perf_counter

# Type alias: mapping name -> callable that returns (result_dict)
TaskMap = Dict[str, Callable[[], Any]]

class ParallelRunner:
    """Run tasks in parallel. If Google ADK ParallelAgent is available, use it; otherwise fall back."""
    def __init__(self, use_adk_preferred: bool = True):
        self._adk_ok = False
        self._adk = None
        if use_adk_preferred:
            try:
                # NOTE: Adjust these imports to your actual ADK package.
                # Many environments expose an Agent/Tool API. We avoid strict typing to keep it portable.
                import google_adk # placeholder module name per project brief
                self._adk = google_adk
                self._adk_ok = True
            except Exception:
                self._adk_ok = False

    def run(self, tasks: TaskMap) -> Tuple[Dict[str, Any], Dict[str, int], Dict[str, str]]:
        t0 = perf_counter()
        results: Dict[str, Any] = {}
        timings_ms: Dict[str, int] = {}
        errors: Dict[str, str] = {}

        if self._adk_ok and self._adk is not None:
            # --- ADK path (pseudo-API; adapt to your environment) ---
            try:
                t_adk = perf_counter()
                # Example sketch — create a ParallelAgent and register tools for each task
                # Replace with your actual ADK API calls
                agent = self._adk.ParallelAgent()
                for name, fn in tasks.items():
                    agent.register_tool(name, fn)
                adk_out = agent.run_parallel(list(tasks.keys()))
                timings_ms["parallel_ms"] = round((perf_counter() - t_adk) * 1000)
                for name in tasks.keys():
                    try:
                        results[name] = adk_out.get(name)
                        timings_ms[f"{name}_ms"] = adk_out.get(f"{name}_ms", 0)
                    except Exception as e:
                        errors[name] = str(e)
            except Exception as e:
                # If ADK fails at runtime, fall back immediately
                self._run_fallback(tasks, results, timings_ms, errors)
        else:
            # --- Fallback path ---
            self._run_fallback(tasks, results, timings_ms, errors)

        timings_ms["total_ms"] = round((perf_counter() - t0) * 1000)
        return results, timings_ms, errors
    
    def _run_fallback(self, tasks: TaskMap, results, timings_ms, errors):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from time import perf_counter
        t = perf_counter()
        with ThreadPoolExecutor(max_workers=max(2, len(tasks))) as ex:
            fut_map = {ex.submit(self._timed_call, name, fn): name for name, fn in tasks.items()}
            for fut in as_completed(fut_map):
                name = fut_map[fut]
                try:
                    res, ms = fut.result()
                    results[name] = res
                    timings_ms[f"{name}_ms"] = ms
                except Exception as e:
                    errors[name] = str(e)
        timings_ms["parallel_ms"] = round((perf_counter() - t) * 1000)

    @staticmethod
    def _timed_call(name: str, fn: Callable[[], Any]):
        t = perf_counter()
        res = fn()
        ms = round((perf_counter() - t) * 1000)
        return res, ms