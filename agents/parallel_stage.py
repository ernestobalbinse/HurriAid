# agents/parallel_stage.py
from pydantic import PrivateAttr
from google.adk.agents import BaseAgent, ParallelAgent
from google.adk.agents.invocation_context import InvocationContext
from agents.planner import plan_nearest_open_shelter
from agents.ai_communicator import build_checklist_llm_agent

class PlannerAgent(BaseAgent):
    _data_dir: str = PrivateAttr()
    _zip_key: str = PrivateAttr()

    def __init__(self, data_dir: str, zip_key: str = "zip_point"):
        super().__init__(name="PlannerAgent")
        self._data_dir = data_dir
        self._zip_key = zip_key

    async def run_async(self, ctx: InvocationContext) -> None:
        zp = ctx.session_state.get(self._zip_key)
        ctx.session_state["plan"] = plan_nearest_open_shelter(zp, self._data_dir)

def build_parallel_agent(data_dir: str):
    checklist_agent = build_checklist_llm_agent()
    # set where the LLM should store JSON text if you want automatic state save:
    checklist_agent.output_key = "checklist_json"
    return ParallelAgent(
        name="PlanAndCommunicate",
        sub_agents=[PlannerAgent(data_dir), checklist_agent],
        description="Runs planner (deterministic) and checklist (LLM) in parallel."
)
