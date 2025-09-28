# agents/ai_risk.py
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field

from google.adk.agents.llm_agent import LlmAgent
from google.genai import types


# What we want back from the LLM (strict JSON)
class RiskOutput(BaseModel):
    risk: Literal["SAFE", "LOW", "MEDIUM", "HIGH"] = Field(
        description="Overall hurricane risk."
    )
    why: str = Field(
        description="One sentence (<=25 words) explanation in plain English."
    )


def build_risk_agent() -> LlmAgent:
    """
    Build a small, steady LLM agent that classifies hurricane risk
    and returns STRICT JSON matching RiskOutput.
    """

    # This instruction is intentionally concise. Your watcher can still
    # override `agent.instruction` at runtime to inject the specific facts.
    instruction = (
        "You are a hurricane risk classifier.\n\n"
        "You will receive a Facts block with keys like:\n"
        "  zip, distance_mi, radius_mi, category, inside_advisory (true/false).\n\n"
        "Decide ONE overall risk: SAFE, LOW, MEDIUM, or HIGH.\n"
        "Heuristics (guidance, not rules):\n"
        "  - HIGH   if inside the advisory radius, OR within ~30 mi at Category ≥2.\n"
        "  - MEDIUM if within (radius + ~75 mi), or within ~120 mi at TS/CAT1.\n"
        "  - LOW    otherwise, when there's some activity but limited threat.\n"
        "  - SAFE   when no active advisory nearby or clearly far away.\n\n"
        "OUTPUT:\n"
        "Return ONLY a JSON object that matches the provided schema — no prose, no markdown.\n"
        'Example: {\"risk\":\"MEDIUM\",\"why\":\"Bands likely tomorrow; you are within the extended radius.\"}'
    )

    return LlmAgent(
        name="HurricaneRiskClassifier",
        model="gemini-2.0-flash",
        description="Classifies hurricane risk (SAFE/LOW/MEDIUM/HIGH) and explains why in one sentence.",
        include_contents="none",
        instruction=instruction,
        output_schema=RiskOutput,  # Enforce strict JSON shape
        generate_content_config=types.GenerateContentConfig(
            temperature=0.2,          # keep outputs consistent
            max_output_tokens=120,
        ),
        # Note: we do NOT set transfer flags here to avoid ADK warnings with output_schema.
    )
