# agents/ai_checklist.py
from __future__ import annotations

from google.adk.agents.llm_agent import LlmAgent
from google.genai import types

def build_checklist_agent() -> LlmAgent:
    """
    Produces a risk-aware checklist as a JSON array of strings.
    """
    instruction = """You are a preparedness assistant.

Read the "Facts" block (zip, risk, distance_km, radius_km, category).
Produce a short, practical checklist tailored to the stated risk and proximity.

OUTPUT FORMAT (STRICT):
Return ONLY a JSON array of 5-10 short strings. No objects. No markdown.
Examples: ["Water (3 days)","Medications","Flashlight & batteries"]

No code fences, no extra text.
"""

    return LlmAgent(
        name="ChecklistMaker",
        model="gemini-2.0-flash",
        include_contents="none",
        instruction=instruction,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=180,
        ),
    )
