# core/parallel_exec.py
from __future__ import annotations
from typing import Callable, Dict, Any, Tuple, Optional
from time import perf_counter
import importlib
import importlib.util

TaskMap = Dict[str, Callable[[], Any]]

class ParallelRunner:
    """
    Run tasks in parallel. Prefer Google ADK ParallelAgent if available,
    otherwise fall back to threads. Uses dynamic import so editors don't warn.
    """
    def __init__(self, use_adk_preferred: bool = True,
                 preferred_modules: Tuple[str, ...] = ("google_adk", "google_adk_parallel", "a2a_sdk", "adk")):
        self._adk_mod: Optional[Any] = None
        self._adk_ok = False

        if use_adk_preferred:
            for modname in preferred_modules:
                try:
                    if importlib.util.find_spec(modname) is not None:
                        self._adk_mod = importlib.import_module(modname)
                        self._adk_ok = True
                        break
                except Exception:
                    # If any probe fails, keep trying others
                    pass

    def run(self, tasks: TaskMap) -> Tuple[Dict[str, Any], Dict[str, int], Dict[str, str]]:
        t0 = perf_counter()
        results: Dict[str, Any] = {}
        timings_ms: Dict[str, int] = {}
        errors: Dict[str, str] = {}

        if self._adk_ok and self._adk_mod is not None:
            try:
                t_adk = perf_counter()
                # Adapt to your ADK API here:
                # Try to get a ParallelAgent class and two basic methods we need.
                ParallelAgent = getattr(self._adk_mod, "ParallelAgent", None)
                if ParallelAgent is None:
                    raise RuntimeError("ParallelAgent not found in ADK module")

                agent = ParallelAgent()
                # Expect agent.register_tool(name, fn) and agent.run_parallel(list_of_names) -> dict
                register_tool = getattr(agent, "register_tool", None)
                run_parallel = getattr(agent, "run_parallel", None)
                if not callable(register_tool) or not callable(run_parallel):
                    raise RuntimeError("ADK agent API mismatch (register_tool/run_parallel missing)")

                for name, fn in tasks.items():
                    register_tool(name, fn)

                adk_out = run_parallel(list(tasks.keys()))
                timings_ms["parallel_ms"] = round((perf_counter() - t_adk) * 1000)

                for name in tasks.keys():
                    # Expect structure: {"analyzer": {...}, "planner": {...}, "analyzer_ms": int, ...}
                    results[name] = adk_out.get(name)
                    ms_key = f"{name}_ms"
                    if ms_key in adk_out:
                        timings_ms[ms_key] = adk_out[ms_key]

            except Exception as e:
                # If ADK path fails, fall back
                self._run_fallback(tasks, results, timings_ms, errors)
                errors["adk"] = str(e)
        else:
            self._run_fallback(tasks, results, timings_ms, errors)

        timings_ms["total_ms"] = round((perf_counter() - t0) * 1000)
        return results, timings_ms, errors

    def _run_fallback(self, tasks: TaskMap, results, timings_ms, errors):
        from concurrent.futures import ThreadPoolExecutor, as_completed
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
