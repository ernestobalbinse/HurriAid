# agents/coordinator.py
from __future__ import annotations

import time
from typing import Any, Dict

# Single-responsibility imports. These must NOT import Coordinator to avoid cycles.
from agents.watcher import run_watcher_once
from agents.parallel_pipeline import run_parallel_once

__all__ = ["Coordinator"]


class Coordinator:
    """
    Runs one full “press the button” cycle from the UI:

      1) Watcher  – load advisory data, classify risk with AI, generate the short “why”.
      2) Parallel – AI checklist + shelter planner (and anything else you add later).

    It merges everything into a single dict that the UI can render:
      {
        advisory, analysis, zip_point, risk_ai, checklist, plan, debug, errors, timings_ms
      }

    Notes:
      • This file is intentionally dumb. It just orchestrates calls, merges dicts,
        and totals timings. All domain logic lives in watcher/parallel modules.
      • We assume AI is always available; there are no non-AI fallbacks here.
    """

    def __init__(self, data_dir: str):
        self.data_dir = data_dir

    def run_once(self, zip_code: str) -> Dict[str, Any]:
        t_start = time.perf_counter()

        # Skeleton result so the UI never explodes on missing keys.
        result: Dict[str, Any] = {
            "advisory": {},
            "analysis": {},
            "zip_point": {},
            "risk_ai": {},          # e.g., {"risk":"MEDIUM","why":"..."}
            "checklist": [],
            "plan": None,
            "debug": {},
            "errors": {},
            "timings_ms": {},
        }

        # -----------------------------
        # Agent 1: Watcher 
        # -----------------------------
        try:
            state, watch_timings = run_watcher_once(self.data_dir, zip_code)
            state = state if isinstance(state, dict) else {}

            # Merge top-level state (but not errors/timings; we handle those below).
            for k, v in state.items():
                if k in ("errors", "timings_ms"):
                    continue
                result[k] = v

            # Merge watcher debug if provided
            w_dbg = state.get("debug")
            if isinstance(w_dbg, dict):
                result["debug"].update(w_dbg)

            # Merge watcher errors, if any
            w_errs = state.get("errors")
            if isinstance(w_errs, dict):
                result["errors"].update(w_errs)

            # Merge/compute watcher timing
            wt = 0.0
            if isinstance(watch_timings, dict):
                result["timings_ms"].update(watch_timings)
                wt = float(
                    watch_timings.get("watcher_ms_total")
                    or (
                        (watch_timings.get("watcher_ms_read") or 0.0)
                        + (watch_timings.get("watcher_ms_analyze") or 0.0)
                        + (watch_timings.get("explainer_ms") or 0.0)
                    )
                )
            if wt > 0:
                result["timings_ms"]["watcher_ms"] = wt

        except Exception as e:
            # Keep going so the UI can show errors cleanly.
            result["errors"]["watcher"] = f"{type(e).__name__}: {e}"

        # Prepare a clean dict for parallel phase (avoid mutating result mid-read).
        state_for_parallel: Dict[str, Any] = dict(result)

        # ----------------------------------------
        # Agent 2: Parallel (checklist + planner)
        # ----------------------------------------
        try:
            par_state, par_timings = run_parallel_once(self.data_dir, zip_code, state_for_parallel)
            par_state = par_state if isinstance(par_state, dict) else {}

            # Merge debug without clobbering watcher debug
            p_dbg = par_state.get("debug")
            if isinstance(p_dbg, dict):
                result["debug"].update(p_dbg)

            # Merge main outputs (skip errors/timings—the next blocks handle those)
            for k, v in par_state.items():
                if k in ("errors", "timings_ms", "debug"):
                    continue
                result[k] = v

            # Merge parallel errors
            p_errs = par_state.get("errors")
            if isinstance(p_errs, dict):
                result["errors"].update(p_errs)

            # Merge/normalize parallel timing (function may return a dict or a number)
            if isinstance(par_timings, dict):
                result["timings_ms"].update(par_timings)
                if "parallel_ms" not in result["timings_ms"]:
                    # Provide a friendly bucket if the pipeline only reported sub-steps
                    subtotal = 0.0
                    for k, v in par_timings.items():
                        if isinstance(v, (int, float)) and k.endswith("_ms"):
                            subtotal += float(v)
                    if subtotal > 0:
                        result["timings_ms"]["parallel_ms"] = subtotal
            elif isinstance(par_timings, (int, float)):
                result["timings_ms"]["parallel_ms"] = float(par_timings)

        except Exception as e:
            result["errors"]["parallel"] = f"{type(e).__name__}: {e}"

        # -----------------------------
        # Final total for the status UI
        # -----------------------------
        result["timings_ms"]["total_ms"] = (time.perf_counter() - t_start) * 1000.0

        return result
