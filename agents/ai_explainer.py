# agents/ai_explainer.py
from __future__ import annotations

try:
    from google.adk.agents.llm_agent import LlmAgent
    from google.genai import types
    _ADK_OK = True
except Exception:
    _ADK_OK = False

def build_risk_explainer_agent():
    if not _ADK_OK:
        raise RuntimeError("ADK not available")

    return LlmAgent(
        model="gemini-2.0-flash",
        name="RiskExplainer",
        description="Explains the risk level in one short sentence.",
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=60
        ),
        instruction=(
            "You are a hurricane risk explainer. The user prompt will contain ZIP, risk, "
            "distance to storm center in km, advisory radius in km, and storm category.\n\n"
            "Write ONE short sentence (<= 25 words) explaining *why* that risk level applies.\n"
            "REQUIREMENTS:\n"
            "• **Prefix** your sentence with the exact token: '🧠 AI: '\n"
            "• Plain text only. No markdown, no lists, no extra text.\n"
        ),
    )
