# agents/verifier_llm.py
from typing import List, Dict
import os

# This client is the AI Studio one (no billing required)
from google import genai

MODEL_ID = os.getenv("HURRIAID_MODEL", "gemini-2.0-flash")

def verify_items_with_llm(items: List[str]) -> Dict:
    """
    Ask Gemini to evaluate each item as TRUE/FALSE/MISLEADING for hurricane prep.
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return {"overall": "ERROR", "error": "Missing GOOGLE_API_KEY", "matches": []}

    client = genai.Client(api_key=api_key)

    matches = []
    any_false = False
    any_true = False

    for it in items:
        prompt = (
            "Classify the following hurricane preparation statement as TRUE, FALSE, or MISLEADING. "
            "Return only one of these words and a brief note:\n"
            f"Statement: {it}"
        )
        try:
            resp = client.models.generate_content(model=MODEL_ID, contents=prompt)
            text = (resp.text or "").strip().upper()
            verdict = "MISLEADING"
            if "TRUE" in text and "FALSE" not in text:
                verdict = "TRUE"
                any_true = True
            elif "FALSE" in text:
                verdict = "FALSE"
                any_false = True

            matches.append({"pattern": it, "verdict": verdict, "note": resp.text or ""})
        except Exception as e:
            return {"overall": "ERROR", "error": f"LLM call failed: {e}", "matches": []}

    if any_false:
        overall = "FALSE"
    elif matches and all(m["verdict"] == "TRUE" for m in matches):
        overall = "SAFE"
    else:
        overall = "CAUTION" if matches else "CLEAR"

    return {"overall": overall, "matches": matches}
