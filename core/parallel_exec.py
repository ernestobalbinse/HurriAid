# core/parallel_exec.py — Step 10 (ADK mandatory, no fallback)
from __future__ import annotations
from typing import Callable, Dict, Any, Tuple
from time import perf_counter
import importlib, importlib.util

TaskMap = Dict[str, Callable[[], Any]]

ADK_MODULES = (
    "google_adk", # preferred
    "google_adk_parallel", # alternative name, if any
    "a2a_sdk", # legacy/internal — keep if needed
    "adk", # last‑ditch alias
)

class ADKNotAvailable(RuntimeError):
    pass

class ParallelRunner:
    """Parallel executor that **requires** Google ADK.
    - Loads the first importable module from ADK_MODULES.
    - Exposes `run(tasks)` which executes tool fns via ADK's ParallelAgent.
    - If ADK is missing or its API is incompatible, returns an `adk` error and no task results.
    """
    def __init__(self):
        self._adk_mod = self._import_adk()
        self._validate_api()

    def _import_adk(self):
        for name in ADK_MODULES:
            try:
                if importlib.util.find_spec(name) is not None:
                    return importlib.import_module(name)
            except Exception:
                # try next alias
                pass
        raise ADKNotAvailable(
            "Google ADK not found. Install the ADK package (e.g. 'pip install google-adk')."
        )

    def _validate_api(self):
        # Expect a ParallelAgent class with register_tool(name, fn) and run_parallel(task_names)
        ParallelAgent = getattr(self._adk_mod, "ParallelAgent", None)
        if ParallelAgent is None:
            raise ADKNotAvailable("ADK module lacks ParallelAgent class.")
        # Create one instance up front to fail fast on constructor errors
        self._agent = ParallelAgent()
        if not hasattr(self._agent, "register_tool") or not hasattr(self._agent, "run_parallel"):
            raise ADKNotAvailable("ADK ParallelAgent missing register_tool/run_parallel methods.")

    def run(self, tasks: TaskMap) -> Tuple[Dict[str, Any], Dict[str, int], Dict[str, str]]:
        t0 = perf_counter()
        results: Dict[str, Any] = {}
        timings_ms: Dict[str, int] = {}
        errors: Dict[str, str] = {}

        try:
            # fresh agent per run in case tools keep state
            agent = self._agent.__class__()
            # register python callables as tools
            for name, fn in tasks.items():
                agent.register_tool(name, fn)
            # execute in parallel
            t_adk = perf_counter()
            adk_out = agent.run_parallel(list(tasks.keys()))
            timings_ms["parallel_ms"] = round((perf_counter() - t_adk) * 1000)


            # Collect results per task name; timing keys are optional depending on ADK impl
            for name in tasks.keys():
                results[name] = adk_out.get(name)
                ms_key = f"{name}_ms"
                if ms_key in adk_out:
                    timings_ms[ms_key] = adk_out[ms_key]
        except ADKNotAvailable as e:
            errors["adk"] = str(e)
        except Exception as e:
            errors["adk"] = f"ADK execution failed: {e}"
        finally:
            timings_ms["total_ms"] = round((perf_counter() - t0) * 1000)

        return results, timings_ms, errors