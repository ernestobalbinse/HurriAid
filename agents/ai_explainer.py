# agents/ai_explainer.py
from __future__ import annotations

# We always use Gemini via Google ADK in this project.
# If ADK isn't available, we stop.
from core.parallel_exec import ADKNotAvailable

try:
    from google.adk.agents.llm_agent import LlmAgent
    from google.genai import types
except Exception as e:
    raise ADKNotAvailable(f"Google ADK is required to build the risk explainer: {e}")


def build_risk_explainer_agent() -> "LlmAgent":
    """
    Build a small Gemini agent that turns structured storm facts into
    ONE calm, human-sounding sentence explaining risk.

    Usage pattern:
      agent = build_risk_explainer_agent()
      agent.instruction = RISK_EXPLAINER_INSTR.format(
          zip=zip_code,
          risk=risk_level,              # SAFE | LOW | MEDIUM | HIGH
          distance_mi=distance_mi,      # number or 'unknown'
          radius_mi=radius_mi,          # number or 'unknown'
          category=category,            # e.g., 'TS', 'CAT1'...'CAT5'
          inside=str(inside).lower(),   # 'true' | 'false'
          proof_nonce=nonce,            # echo guard to verify the response
      )

    The model must return ONLY:
      {"why":"<<=25 words, plain English>","proof":"<nonce>"}
    """
    return LlmAgent(
        model="gemini-2.0-flash",
        name="RiskExplainer",
        description="Writes a single-sentence, plain-English hurricane risk explanation.",
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(
            # Slightly warmer than default so the sentence feels natural, not robotic.
            temperature=0.55,
            max_output_tokens=80,
        ),
        # The caller sets the concrete template below via .instruction = ... .format(...)
        instruction=RISK_EXPLAINER_INSTR,
    )


# Templated instruction: fill with real values right before calling the agent.
# Keep it short, natural, and strictly JSON on output.
RISK_EXPLAINER_INSTR = """\
You are helping a resident understand hurricane risk in plain English.

FACTS
- zip: {zip}
- risk: {risk}
- distance_mi: {distance_mi}
- radius_mi: {radius_mi}
- category: {category}
- inside_advisory: {inside}

TASK
Write ONE friendly, skimmable sentence (<=25 words) explaining the situation and why the risk is {risk}.
Prefer everyday language; mention distance and category only if helpful. No jargon.

OUTPUT (STRICT)
Return ONLY this JSON object and nothing else:
{{"why":"<one sentence>","proof":"{proof_nonce}"}}
"""
