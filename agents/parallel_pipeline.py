# agents/parallel_pipeline.py
from __future__ import annotations

import time
from typing import Any, Dict, Tuple

# Checklist is optional; we’ll import if available
try:
    from agents.ai_checklist import make_checklist_from_state  # (state, zip) -> (items, debug, err)
    _HAS_CHECKLIST = True
except Exception:
    _HAS_CHECKLIST = False

from agents.ai_planner import run_planner_once


def run_parallel_once(
    data_dir: str,
    zip_code: str,
    state: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, float]]:
    """
    Runs “parallel” work (AI checklist + deterministic planner).
    Returns:
      par_out: dict with keys possibly including 'checklist', 'plan', 'debug', 'errors'
      par_timings: {'parallel_ms': float, 'planner_ms': float, 'checklist_ms': float}
    """
    t_all = time.perf_counter()
    par_out: Dict[str, Any] = {"errors": {}, "debug": {}, "timings_ms": {}}
    timings: Dict[str, float] = {}

    # ---------- Checklist (optional) ----------
    if _HAS_CHECKLIST:
        t0 = time.perf_counter()
        try:
            items, dbg, err = make_checklist_from_state(state, zip_code)
            timings["checklist_ms"] = (time.perf_counter() - t0) * 1000.0
            par_out["debug"].update({"checklist": dbg or {}})
            if err:
                par_out["errors"]["checklist"] = err
            elif items:
                par_out["checklist"] = items
        except Exception as e:
            timings["checklist_ms"] = (time.perf_counter() - t0) * 1000.0
            par_out["errors"]["checklist"] = f"{type(e).__name__}: {e}"
    else:
        par_out["errors"]["checklist"] = "Checklist module not available"
        # (No checklist_ms timing)

    # ---------- Planner (always on) ----------
    planner_out, planner_ms, planner_err = run_planner_once(data_dir, zip_code, state)
    timings["planner_ms"] = planner_ms
    # Merge outputs
    if "debug" in planner_out:
        dbg_merge = par_out.get("debug", {})
        dbg_merge.update(planner_out["debug"])
        par_out["debug"] = dbg_merge
    if "plan" in planner_out:
        par_out["plan"] = planner_out["plan"]
    if planner_err:
        par_out["errors"]["planner"] = planner_err

    # ---------- Totals ----------
    timings["parallel_ms"] = (time.perf_counter() - t_all) * 1000.0
    par_out["timings_ms"].update(timings)

    return par_out, timings
