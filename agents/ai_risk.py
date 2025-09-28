# agents/ai_risk.py
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field

from google.adk.agents.llm_agent import LlmAgent
from google.genai import types

class RiskOutput(BaseModel):
    risk: Literal["HIGH", "MEDIUM", "LOW"] = Field(description="Overall hurricane risk.")
    why: str = Field(description="One sentence (<=25 words) plain text reason.")

def build_risk_agent() -> LlmAgent:
    """
    Classifies hurricane risk from facts and returns STRICT JSON via output_schema:
      { "risk": "HIGH|MEDIUM|LOW", "why": "one sentence" }
    """
    instruction = """You are a hurricane risk classifier.

You receive a Facts block (plain key=value lines). Decide a single overall risk:
- HIGH, MEDIUM, or LOW.

Guidance (not mandatory):
- HIGH if inside advisory radius, or within ~50 km at Category 2+.
- MEDIUM if within (radius + ~120 km) or inside at TS/CAT1.
- LOW otherwise.

OUTPUT FORMAT:
Return ONLY a JSON object matching the provided schema.
No markdown, no code fences, no emojis, no prefixes.
"""

    return LlmAgent(
        name="RiskClassifier",
        model="gemini-2.0-flash",
        include_contents="none",
        instruction=instruction,
        output_schema=RiskOutput,  # Enforce strict JSON
        # Silence transfer warnings by explicitly disallowing transfers
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=128,
        ),
    )
