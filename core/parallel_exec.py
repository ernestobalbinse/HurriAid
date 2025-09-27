# core/parallel_exec.py — ADK mandatory, but no exceptions in __init__
from __future__ import annotations
from typing import Callable, Dict, Any, Tuple, Optional
from time import perf_counter
import importlib, importlib.util

TaskMap = Dict[str, Callable[[], Any]]

ADK_MODULES = ("google_adk", "google_adk_parallel", "a2a_sdk", "adk")

class ADKNotAvailable(RuntimeError):
    pass

class ParallelRunner:
    def __init__(self):
        self._adk_mod: Optional[Any] = None
        self._adk_error: Optional[str] = None
        self._agent_cls = None
        self._probe_adk()

    def _probe_adk(self):
        # Try import
        for name in ADK_MODULES:
            try:
                if importlib.util.find_spec(name) is not None:
                    self._adk_mod = importlib.import_module(name)
                    break
            except Exception:
                pass
        if not self._adk_mod:
            self._adk_error = "Google ADK not found. Install the ADK package (e.g. 'pip install google-adk')."
            return
        # Validate API
        agent_cls = getattr(self._adk_mod, "ParallelAgent", None)
        if agent_cls is None:
            self._adk_error = "ADK module lacks ParallelAgent class."
            return
        if not hasattr(agent_cls, "__call__"):
            # defensive; most classes are callable to construct
            self._adk_error = "ADK ParallelAgent is not constructible."
            return
        # Check methods on a temp instance
        try:
            agent = agent_cls()
            if not hasattr(agent, "register_tool") or not hasattr(agent, "run_parallel"):
                self._adk_error = "ADK ParallelAgent missing register_tool/run_parallel methods."
                return
            self._agent_cls = agent_cls
        except Exception as e:
            self._adk_error = f"ADK initialization failed: {e}"

    def run(self, tasks: TaskMap) -> Tuple[Dict[str, Any], Dict[str, int], Dict[str, str]]:
        results: Dict[str, Any] = {}
        timings_ms: Dict[str, int] = {}
        errors: Dict[str, str] = {}

        t0 = perf_counter()
        try:
            if self._adk_error:
                raise ADKNotAvailable(self._adk_error)

            agent = self._agent_cls()  # fresh instance per run
            for name, fn in tasks.items():
                agent.register_tool(name, fn)

            t_adk = perf_counter()
            out = agent.run_parallel(list(tasks.keys()))
            timings_ms["parallel_ms"] = round((perf_counter() - t_adk) * 1000)

            for name in tasks.keys():
                results[name] = out.get(name)
                k = f"{name}_ms"
                if k in out:
                    timings_ms[k] = out[k]

        except ADKNotAvailable as e:
            errors["adk"] = str(e)
        except Exception as e:
            errors["adk"] = f"ADK execution failed: {e}"
        finally:
            timings_ms["total_ms"] = round((perf_counter() - t0) * 1000)

        return results, timings_ms, errors
