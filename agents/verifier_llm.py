# agents/verifier_llm.py
from __future__ import annotations
from typing import Dict, Any, List
from core.llm_gemini import generate_text, GeminiNotConfigured

SYSTEM_PROMPT = """You are a safety verifier for hurricane preparation information.
Classify each item as TRUE, MISLEADING, or FALSE, and briefly explain why.
Return a compact JSON list of objects: [{"pattern": "...", "verdict": "...", "note": "..."}].
Only use those keys."""

def verify_items_with_llm(items: List[str]) -> Dict[str, Any]:
    if not items:
        return {"overall": "CLEAR", "matches": []}
    user_prompt = "Check these items:\n" + "\n".join(f"- {it}" for it in items)
    prompt = SYSTEM_PROMPT + "\n\n" + user_prompt
    try:
        raw = generate_text(prompt)
    except GeminiNotConfigured as e:
        # Signal to UI that LLM is not ready
        return {"overall": "ERROR", "matches": [], "error": str(e)}
    # Try to parse JSON from model text
    import json, re
    text = raw.strip()
    # Extract JSON array if model added extra prose
    m = re.search(r"(\[\s*\{.*\}\s*\])", text, re.DOTALL)
    if m:
        text = m.group(1)
    try:
        matches = json.loads(text)
        # Normalize verdicts to upper-case
        for mobj in matches:
            mobj["verdict"] = str(mobj.get("verdict","")).upper()
        # Roll-up: any FALSE → FALSE; all TRUE → SAFE; else CAUTION
        if not matches:
            overall = "CLEAR"
        elif any(mobj["verdict"] == "FALSE" for mobj in matches):
            overall = "FALSE"
        elif all(mobj["verdict"] == "TRUE" for mobj in matches):
            overall = "SAFE"
        else:
            overall = "CAUTION"
        return {"overall": overall, "matches": matches}
    except Exception:
        # If parsing fails, return raw text so you can inspect
        return {"overall": "CAUTION", "matches": [{"pattern": "LLM RAW", "verdict": "MISLEADING", "note": text}]}
