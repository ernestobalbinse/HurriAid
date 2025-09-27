# core/parallel_exec.py — smarter class discovery (avoid *Extension), with safe ctor
from __future__ import annotations
from typing import Callable, Dict, Any, Tuple, Optional, List
from time import perf_counter
import os, importlib, importlib.util, inspect, pkgutil

TaskMap = Dict[str, Callable[[], Any]]

class ADKNotAvailable(RuntimeError):
    pass

ADK_MODULES = tuple(filter(None, [
    os.getenv("HURRIAID_ADK_MODULE"),  # e.g., "google.adk"
    "google.adk",                      # google-adk 1.15.x
    "adk",
    "google_adk",
    "google_adk_parallel",
]))

REGISTER_NAMES = ("register_tool", "add_function", "add_tool", "register")
RUN_NAMES      = ("run_parallel", "run", "execute", "start", "invoke")

PREFER_KEYWORDS = ("Agent", "Executor", "Runner", "Coordinator", "Orchestrator")
AVOID_KEYWORDS  = ("Extension",)   # <- don't pick extension classes

def _import_first(mod_names: List[str]):
    tried = []
    for name in mod_names:
        if not name: continue
        tried.append(name)
        spec = importlib.util.find_spec(name)
        if spec:
            return importlib.import_module(name)
    raise ADKNotAvailable("Google ADK not found. Tried: " + ", ".join(tried))

def _looks_like_executor(cls: type) -> bool:
    has_reg = any(hasattr(cls, r) for r in REGISTER_NAMES)
    has_run = any(hasattr(cls, r) for r in RUN_NAMES)
    return has_reg and has_run

def _rank(cls: type) -> int:
    """Higher is better; avoid Extension; prefer Agent/Executor/Runner names."""
    name = cls.__name__
    score = 0
    if any(k in name for k in PREFER_KEYWORDS): score += 5
    if any(k in name for k in AVOID_KEYWORDS):  score -= 10
    # prefer non-abstract classes
    if not inspect.isabstract(cls): score += 1
    return score

def _find_executor_class(root_mod) -> Optional[type]:
    candidates: List[type] = []

    # 1) root-level classes
    for _, obj in inspect.getmembers(root_mod, inspect.isclass):
        if _looks_like_executor(obj): candidates.append(obj)

    # 2) submodules
    if hasattr(root_mod, "__path__"):
        for m in pkgutil.walk_packages(root_mod.__path__, root_mod.__name__ + "."):
            try:
                mod = importlib.import_module(m.name)
            except Exception:
                continue
            for _, obj in inspect.getmembers(mod, inspect.isclass):
                if _looks_like_executor(obj): candidates.append(obj)

    if not candidates:
        return None

    # rank & pick best
    candidates.sort(key=_rank, reverse=True)
    return candidates[0]

def _ctor_kwargs_for(cls):
    import os, inspect
    kw = {}
    try:
        sig = inspect.signature(cls)
    except Exception:
        return kw

    params = sig.parameters
    # Friendly names for project & region
    PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCLOUD_PROJECT") or os.getenv("PROJECT_ID")
    LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION") or os.getenv("VERTEX_LOCATION") or "us-central1"

    # Name-ish
    if "extension_name" in params and params["extension_name"].default is inspect._empty:
        kw["extension_name"] = "HurriAid"
    elif "name" in params and params["name"].default is inspect._empty:
        kw["name"] = "HurriAid"

    # Project
    if PROJECT:
        for p in ("project", "project_id", "gcp_project"):
            if p in params and params[p].default is inspect._empty:
                kw[p] = PROJECT
                break

    # Location/region
    for p in ("location", "region", "gcp_region"):
        if p in params and params[p].default is inspect._empty:
            kw[p] = LOCATION
            break

    return kw


class ParallelRunner:
    def __init__(self):
        self._adk_mod = _import_first(list(ADK_MODULES))
        self._executor_cls = _find_executor_class(self._adk_mod)
        if not self._executor_cls:
            raise ADKNotAvailable(
                f"No suitable executor in {self._adk_mod.__name__}. "
                f"Expected a class with register_* and run_* methods (not an Extension)."
            )

        # choose method names
        self._register_name = next((n for n in REGISTER_NAMES if hasattr(self._executor_cls, n)), None)
        self._run_name      = next((n for n in RUN_NAMES      if hasattr(self._executor_cls, n)), None)
        if not (self._register_name and self._run_name):
            raise ADKNotAvailable("Executor found but missing required register/run methods.")

        self._ctor_kwargs = _ctor_kwargs_for(self._executor_cls)

    def run(self, tasks: TaskMap) -> Tuple[Dict[str, Any], Dict[str, int], Dict[str, str]]:
        t0 = perf_counter()
        results: Dict[str, Any] = {}
        timings_ms: Dict[str, int] = {}
        errors: Dict[str, str] = {}

        try:
            # Construct agent with friendly defaults if needed
            try:
                agent = self._executor_cls(**self._ctor_kwargs)
            except TypeError as e:
                # last-ditch: try without kwargs
                agent = self._executor_cls()

            # Register each callable
            for name, fn in tasks.items():
                getattr(agent, self._register_name)(name, fn)

            # Execute
            t_adk = perf_counter()
            run_fn = getattr(agent, self._run_name)
            try:
                out = run_fn(list(tasks.keys()))  # common pattern: pass names
            except TypeError:
                out = run_fn(tasks)                # some take dict name->fn

            timings_ms["parallel_ms"] = round((perf_counter() - t_adk) * 1000)

            # Normalize output
            if isinstance(out, dict):
                for name in tasks.keys():
                    results[name] = out.get(name)
                    mk = f"{name}_ms"
                    if mk in out:
                        timings_ms[mk] = out[mk]
            else:
                results["adk_raw"] = out

        except Exception as e:
            errors["adk"] = f"ADK execution failed: {e}"
        finally:
            timings_ms["total_ms"] = round((perf_counter() - t0) * 1000)

        return results, timings_ms, errors
