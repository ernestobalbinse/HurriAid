# agents/parallel_pipeline.py
from __future__ import annotations

import time
from typing import Any, Dict, Tuple

# Always-on AI pieces
from agents.ai_checklist import make_checklist_from_state  # (state, zip) -> (items, debug, err)
from agents.ai_planner import run_planner_once


def run_parallel_once(
    data_dir: str,
    zip_code: str,
    state: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, float]]:
    """
    Runs the “parallel” phase of the app (conceptually parallel, implemented sequentially here):
      • AI Checklist — short, risk-aware list of actions
      • Planner      — nearest open shelter + rough ETA

    Returns:
      par_out: {
        "checklist": [...],         # when available
        "plan": {...},              # when available
        "debug": {...},             # merged debug snapshots from sub-steps
        "errors": {...},            # per-step errors (doesn't crash the UI)
        "timings_ms": { ... }       # same keys as the returned timings dict
      }
      timings: {
        "checklist_ms": float,
        "planner_ms": float,
        "parallel_ms": float
      }
    """
    t_parallel_start = time.perf_counter()
    par_out: Dict[str, Any] = {"errors": {}, "debug": {}, "timings_ms": {}}
    timings: Dict[str, float] = {}

    # Make sure we have a dict to read from (keeps UI happy even if a caller breaks).
    if not isinstance(state, dict):
        state = {}

    # ---------------------------
    # 1) AI Checklist
    # ---------------------------
    t0 = time.perf_counter()
    try:
        items, dbg, err = make_checklist_from_state(state, zip_code)
        timings["checklist_ms"] = (time.perf_counter() - t0) * 1000.0
        if isinstance(dbg, dict):
            par_out["debug"].update({"checklist": dbg})
        if err:
            par_out["errors"]["checklist"] = err
        elif items:
            par_out["checklist"] = items
        else:
            par_out["errors"]["checklist"] = "Empty checklist from AI"
    except Exception as e:
        timings["checklist_ms"] = (time.perf_counter() - t0) * 1000.0
        par_out["errors"]["checklist"] = f"{type(e).__name__}: {e}"

    # ---------------------------
    # 2) Shelter Planner
    # ---------------------------
    planner_out, planner_ms, planner_err = run_planner_once(data_dir, zip_code, state)
    timings["planner_ms"] = planner_ms

    # Merge planner debug and results
    if isinstance(planner_out, dict):
        if "debug" in planner_out and isinstance(planner_out["debug"], dict):
            par_out["debug"].update(planner_out["debug"])
        if "plan" in planner_out:
            par_out["plan"] = planner_out["plan"]

    if planner_err:
        par_out["errors"]["planner"] = planner_err

    # ---------------------------
    # Totals
    # ---------------------------
    timings["parallel_ms"] = (time.perf_counter() - t_parallel_start) * 1000.0
    par_out["timings_ms"].update(timings)

    return par_out, timings
