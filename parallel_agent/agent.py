"""
Parallel agent to run Analyzer + Planner concurrently using Google ADK.

Flow:
  1) Watcher loads advisory, ZIP map, shelters (sequential, fast).
  2) Analyzer + Planner run in parallel tools.
  3) Normalize results and timings.
"""

from __future__ import annotations
from typing import Dict, Any, Callable
from time import perf_counter

# --- Your existing app modules
from agents.watcher import Watcher
from agents.analyzer import Analyzer
from agents.planner import Planner

# --- ADK (installed as google-adk; import path is 'google.adk')
from google.adk.agents import Agent


class ParallelAgentRunner:
    """
    Small wrapper that constructs an ADK Agent for this run
    and executes 'analyze' and 'plan' concurrently.
    """

    def __init__(self, data_dir: str = "data", model: str = "gemini-2.0-flash"):
        self.data_dir = data_dir
        self.model = model

    def _build_context(self, zip_code: str) -> Dict[str, Any]:
        w = Watcher(self.data_dir)
        advisory = w.get_advisory_offline()
        zip_map = w.get_zip_centroids()
        shelters = w.get_shelters()
        return dict(advisory=advisory, zip_map=zip_map, shelters=shelters, zip=zip_code)

    # Tool functions close over a context dict so they don't re-load files
    @staticmethod
    def _tool_analyze(ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Analyzer tool (pure function: inputs -> dict)."""
        zip_code = ctx["zip"]
        analyzer = Analyzer()
        # assume your Analyzer has .analyze(zip_code, advisory, zip_map)
        return analyzer.analyze(zip_code, ctx["advisory"], ctx["zip_map"])

    @staticmethod
    def _tool_plan(ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Planner tool, depends only on zip centroid + shelters."""
        zip_code = ctx["zip"]
        planner = Planner()
        # planner should compute nearest open shelter against shelters.json
        # if your planner needs analysis, you can pass it via ctx later.
        return planner.plan(zip_code, ctx["zip_map"], ctx["shelters"])

    def _make_agent(self, ctx: Dict[str, Any]) -> Agent:
        """
        Build an ADK Agent and register two tools that capture the shared context.
        Each tool is a zero-arg callable for easy parallel invocation.
        """
        # Wrap tools so ADK can call them without parameters
        def analyze_tool() -> Dict[str, Any]:
            return self._tool_analyze(ctx)

        def plan_tool() -> Dict[str, Any]:
            return self._tool_plan(ctx)

        agent = Agent(
            name="parallel_agent",
            model=self.model,
            description="Runs Analyzer and Planner concurrently for a given ZIP.",
            instruction="Run tools and return their JSON results without paraphrasing.",
            tools=[analyze_tool, plan_tool],
        )
        return agent

    def run(self, zip_code: str) -> Dict[str, Any]:
        """
        Public entry: returns a dict with 'analysis', 'plan', 'timings_ms', 'errors'.
        """
        t0 = perf_counter()
        errors: Dict[str, str] = {}
        timings: Dict[str, int] = {}
        out: Dict[str, Any] = {}

        # 1) Context
        t_ctx = perf_counter()
        try:
            ctx = self._build_context(zip_code)
        except Exception as e:
            errors["watcher"] = f"Watcher failed: {e}"
            return {"errors": errors, "timings_ms": {"total_ms": int((perf_counter()-t0)*1000)}}
        timings["watcher_ms"] = round((perf_counter() - t_ctx) * 1000)

        # 2) Agent with two tools
        agent = self._make_agent(ctx)

        # 3) Parallel run (ADK executes registered tools concurrently)
        t_par = perf_counter()
        try:
            # Many ADK builds support task selection by tool names.
            # If not, calling with no args runs the registered tools once.
            result = agent.run_parallel(["analyze_tool", "plan_tool"])  # try named run
        except Exception:
            # Fallback: ask the agent to run all tools once in parallel
            result = agent.run_parallel()
        timings["parallel_ms"] = round((perf_counter() - t_par) * 1000)

        # 4) Normalize output shapes
        # Depending on ADK version, you may get a dict keyed by function names,
        # or a list of call results. We handle the common cases defensively.
        try:
            # Case A: dict of tool_name -> payload
            if isinstance(result, dict):
                # try a few likely keys
                analysis = result.get("analyze_tool") or result.get("analyze") or result.get("Analyzer") or {}
                plan = result.get("plan_tool") or result.get("plan") or result.get("Planner") or {}

            # Case B: list of call results in registration order
            elif isinstance(result, list) and len(result) >= 2:
                analysis, plan = result[0], result[1]
            else:
                analysis, plan = {}, {}
        except Exception as e:
            errors["adk"] = f"Result normalization failed: {e}"
            analysis, plan = {}, {}

        out.update({
            "advisory": ctx["advisory"],
            "analysis": analysis or {},
            "plan": plan or {},
            "timings_ms": {**timings, "total_ms": round((perf_counter()-t0)*1000)},
            "errors": errors,
        })
        return out
