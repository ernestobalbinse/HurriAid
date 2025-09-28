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
        description="Explains the hurricane risk level in one short sentence.",
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=60,
        ),
        instruction=(
            "You will be asked to explain a hurricane risk concisely (<=25 words). "
            "When instructed, return ONLY a minified JSON object of the form "
            '{"why":"<one sentence>","proof":"<nonce>"} and no extra text.'
        ),
    )
