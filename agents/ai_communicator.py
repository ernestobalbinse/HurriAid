# agents/ai_communicator.py
from __future__ import annotations

import json  # (kept in case callers want to log/inspect raw text)
from typing import List, Dict, Any

from pydantic import BaseModel, Field
from core.parallel_exec import ADKNotAvailable

# We require Google ADK + Gemini for this project. If imports fail, we stop.
try:
    from google.adk.agents.llm_agent import LlmAgent
    from google.genai import types
except Exception as e:
    raise ADKNotAvailable(f"Google ADK is required to build AI communicator: {e}")


# ---- Output schema: the model must return exactly this shape ----
class ChecklistOut(BaseModel):
    items: List[str] = Field(description="Concise, deduplicated checklist items in priority order")


def build_checklist_llm_agent() -> "LlmAgent":
    """
    Build a Gemini agent that creates a SHORT, risk-aware hurricane checklist.
    - Always AI-driven (no rule-based backup).
    - Output must be STRICT JSON matching ChecklistOut.
    - We keep the instruction template generic so callers can .format(zip=..., risk=...).
    """
    return LlmAgent(
        model="gemini-2.0-flash",
        name="checklist_agent",
        description="Generates a short, risk-aware hurricane readiness checklist.",
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(
            # A touch of creativity so the items don’t feel robotic, still reliable.
            temperature=0.4,
            max_output_tokens=400,
        ),
        instruction=(
            "You are a hurricane readiness assistant. Create a SHORT, risk-aware checklist "
            "for the next 12–24 hours.\n\n"
            "INPUTS\n"
            f"- zip: {{zip}}\n"
            f"- risk: {{risk}}  (SAFE | LOW | MEDIUM | HIGH)\n\n"
            "OUTPUT\n"
            "Return STRICT JSON only:\n"
            "{\"items\": [\"item 1\", \"item 2\", \"...\"]}\n\n"
            "SIZE by risk (cap the list accordingly):\n"
            "- SAFE  : 0–2 items (light reminders only)\n"
            "- LOW   : 3–4 items (quick, low-effort tasks; avoid multi-day stockpiles)\n"
            "- MEDIUM: 5–7 items (add core supplies: water, food, meds, radio, cash)\n"
            "- HIGH  : 8–12 items (full readiness: 3-day supplies, first aid, docs, evac plan, fuel)\n\n"
            "GUIDANCE\n"
            "- Items must be specific, practical, and deduplicated.\n"
            "- No prose, no explanations, no emojis, no markdown—JSON only.\n"
            "- Keep phrasing tight (bullet-style).\n"
        ),
        output_schema=ChecklistOut,  # Enforces the exact JSON shape we expect
    )
