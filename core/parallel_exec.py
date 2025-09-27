# core/parallel_exec.py
from __future__ import annotations
import os, time
from typing import Callable, Dict, Any, Tuple

# Force AI Studio by default (no billing). You can always export TRUE later.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

class ADKNotAvailable(RuntimeError):
    pass

class ParallelRunner:
    """
    Minimal runner that executes independent callables concurrently.
    We keep this ADK-neutral so switching between AI Studio and Vertex is just env.
    """
    def __init__(self):
        # If you truly want to *require* google-adk to be importable even in AI Studio mode:
        try:
            import google.adk  # noqa: F401
        except Exception as e:
            raise ADKNotAvailable(f"google-adk import failed: {e}")

    def run(self, tasks: Dict[str, Callable[[], Any]]) -> Tuple[Dict[str, Any], Dict[str, int], Dict[str, str]]:
        """
        tasks: dict of name -> zero-arg callable
        returns: (results, timings_ms, errors)
        """
        results: Dict[str, Any] = {}
        timings: Dict[str, int] = {}
        errors: Dict[str, str] = {}

        # Use standard threads; these tasks are mostly I/O / light CPU
        from concurrent.futures import ThreadPoolExecutor, as_completed

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=len(tasks) or 1) as pool:
            fut_map = {pool.submit(self._wrap, k, fn): k for k, fn in tasks.items()}
            for fut in as_completed(fut_map):
                name = fut_map[fut]
                try:
                    res, dur_ms = fut.result()
                    results[name] = res
                    timings[f"{name}_ms"] = dur_ms
                except Exception as e:
                    errors[name] = str(e)

        timings["parallel_ms"] = round((time.perf_counter() - t0) * 1000)

        # If the ADK import itself failed earlier, you'd never get here (we raise in __init__).
        # We purposely don't call any Vertex APIs here.
        return results, timings, errors

    @staticmethod
    def _wrap(name: str, fn: Callable[[], Any]) -> Tuple[Any, int]:
        t = time.perf_counter()
        out = fn()
        return out, round((time.perf_counter() - t) * 1000)
