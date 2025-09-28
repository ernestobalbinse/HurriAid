# agents/ai_communicator.py
from __future__ import annotations
import json
from typing import List, Dict, Any

# Soft dependency so the UI can still run without ADK
try:
    from google.adk.agents.llm_agent import LlmAgent
    from google.genai import types
    _ADK_OK = True
except Exception:
    _ADK_OK = False

from pydantic import BaseModel, Field

# ---- Output schema to force JSON shape ----
class ChecklistOut(BaseModel):
    items: List[str] = Field(description="Checklist items, ordered and concise")

def build_checklist_llm_agent() -> "LlmAgent":
    """
    Builds a low-temperature LlmAgent that outputs STRICT JSON:
      {"items": ["...", "...", ...]}
    Bilingual (EN/ES) and risk-aware.
    """
    if not _ADK_OK:
        raise RuntimeError("ADK not available for LlmAgent")

    return LlmAgent(
        model="gemini-2.0-flash",
        name="checklist_agent",
        description="Generates a hurricane-readiness checklist tailored to risk (EN/ES).",
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(
            temperature=0.2,            # keep it steady for demos
            max_output_tokens=400
        ),
        instruction=(
            "You are a hurricane readiness assistant. Given a U.S. ZIP and a risk level, "
            "produce a concise, actionable 24–48h checklist.\n\n"
            "RULES:\n"
            "- Output ONLY raw JSON exactly like: {\"items\": [\"...\", \"...\", ...]}\n"
            "- 8–14 items total.\n"
            "- Always include: Water (3 days), Non-perishable food, Medications, "
            "Flashlight & batteries, First aid kit, Important documents in waterproof bag, "
            "Charge power banks, Refuel vehicle > 1/2 tank.\n"
            "- If risk is MEDIUM, ALSO add: Check evacuation routes, Secure windows/doors, Pack go-bag.\n"
            "- If risk is HIGH, ALSO add: Plan to evacuate if officials advise, Move to higher ground if flooding risk, Keep radio/alerts on.\n"
            "\"Water (3 days) / Agua (3 días)\".\n"
            "- No markdown, no prose, no explanations—JSON only.\n\n"
            "INPUTS:\n"
            "ZIP: {zip}\n"
            "RISK: {risk}\n"
        ),
        output_schema=ChecklistOut,   # validates shape
        # Note: we don't rely on output_key for retrieval; we parse the final text event
    )
