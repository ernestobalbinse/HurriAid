# agents/coordinator.py
from __future__ import annotations

import time
from typing import Any, Dict, Tuple

# Import the two execution phases. These modules must NOT import Coordinator.
from agents.watcher import run_watcher_once
from agents.parallel_pipeline import run_parallel_once

__all__ = ["Coordinator"]


class Coordinator:
    """
    Orchestrates a single UI-triggered cycle:
      1) Watcher (advisory load + risk compute + optional AI why)
      2) Parallel (AI checklist, planner, etc.)
    Merges outputs and timings into one result dict consumed by the UI.
    """

    def __init__(self, data_dir: str):
        self.data_dir = data_dir

    def run_once(self, zip_code: str) -> Dict[str, Any]:
        started = time.perf_counter()
        result: Dict[str, Any] = {
            "errors": {},
            "timings_ms": {},
        }

        # ---- Phase 1: Watcher (blocking, single pass) ----
        try:
            state, watch_timings = run_watcher_once(self.data_dir, zip_code)
            if not isinstance(state, dict):
                raise TypeError("Watcher returned non-dict state")

            # Merge watcher state directly (advisory, analysis, zip_point, watcher_text, debug, etc.)
            for k, v in state.items():
                # We'll merge errors and timings separately below
                if k in ("timings_ms", "errors"):
                    continue
                result[k] = v

            # Merge timings
            result["timings_ms"].update(watch_timings or {})
            # Provide a flat 'Watcher' bucket for the UI
            watcher_total = (
                watch_timings.get("watcher_ms_total")
                or (
                    (watch_timings.get("watcher_ms_read") or 0.0)
                    + (watch_timings.get("watcher_ms_analyze") or 0.0)
                    + (watch_timings.get("explainer_ms") or 0.0)
                )
            )
            if watcher_total:
                result["timings_ms"]["watcher_ms"] = watcher_total

            # Merge watcher errors (if any)
            w_errs = state.get("errors") or {}
            if isinstance(w_errs, dict):
                result["errors"].update(w_errs)
        except Exception as e:
            result["errors"]["watcher"] = f"{type(e).__name__}: {e}"

        # Ensure downstream phases have a dict state to read
        state_for_parallel = {}
        state_for_parallel.update(result)

        # ---- Phase 2: Parallel (AI checklist / planner, etc.) ----
        try:
            par_state, par_timings = run_parallel_once(self.data_dir, zip_code, state_for_parallel)
            if isinstance(par_state, dict):
                # Merge top-level outputs like 'checklist', 'plan', and 'debug'
                # Merge 'debug' carefully to avoid overwriting watcher debug
                if "debug" in par_state and isinstance(par_state["debug"], dict):
                    dbg = result.get("debug", {})
                    if not isinstance(dbg, dict):
                        dbg = {}
                    dbg.update(par_state["debug"])
                    result["debug"] = dbg

                # Merge other keys (skip errors/timings here; handle below)
                for k, v in par_state.items():
                    if k in ("errors", "timings_ms", "debug"):
                        continue
                    result[k] = v

                # Merge parallel errors
                p_errs = par_state.get("errors") or {}
                if isinstance(p_errs, dict):
                    result["errors"].update(p_errs)

            # Merge timings
            if isinstance(par_timings, dict):
                result["timings_ms"].update(par_timings)
        except Exception as e:
            result["errors"]["parallel"] = f"{type(e).__name__}: {e}"

        # ---- Finalize totals ----
        # Try to compute a reasonable 'total_ms' for the UI
        total_ms = (time.perf_counter() - started) * 1000.0
        result["timings_ms"]["total_ms"] = total_ms

        return result
