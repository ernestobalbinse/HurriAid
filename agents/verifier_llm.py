# agents/verifier_llm.py
from __future__ import annotations

import os, time, random, json, re, concurrent.futures
from typing import List, Dict, Any

# We call Google GenAI directly here (this verifier is independent of the ADK flows).
from google import genai

# ----------------------------
# Config (env overrides allowed)
# ----------------------------
PRIMARY_MODEL = os.getenv("HURRIAID_MODEL", "gemini-2.0-flash")
PER_CALL_TIMEOUT_SEC = float(os.getenv("HURRIAID_LLM_TIMEOUT", "25"))
MAX_TRIES = int(os.getenv("HURRIAID_LLM_RETRIES", "3"))

# Verdict vocabulary we allow back from the model for each statement.
_VERDICT_WORDS = ("TRUE", "FALSE", "MISLEADING", "CAUTION")

# ----------------------------
# Small reliability helpers
# ----------------------------
def _retry_call(fn, max_tries=MAX_TRIES, base=0.8, cap=6.0):
    """
    Retry a function a few times with gentle backoff.
    This keeps the UI smooth if the model has a transient hiccup.
    """
    last = None
    for i in range(max_tries):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(min(cap, base * (2 ** i)) + random.uniform(0, 0.35))
    raise last

def _call_with_timeout(fn, timeout_sec: float):
    """
    Run `fn` in a tiny thread pool and cut it off if it exceeds `timeout_sec`.
    Streamlit stays responsive and the user sees an actionable error instead of a hang.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        return fut.result(timeout=timeout_sec)

# ----------------------------
# Prompt the model with a strict JSON contract
# ----------------------------
SYSTEM_PROMPT = """You are HurriAid Verifier, checking hurricane preparation/response statements.

Return ONLY JSON with this exact shape:
{
  "overall": "SAFE | FALSE | MISLEADING | CAUTION | CLEAR",
  "matches": [
    { "pattern": "<original statement>", "verdict": "TRUE|FALSE|MISLEADING|CAUTION", "note": "<≤30 words, no verdict words>" }
  ]
}

Rules:
- If a statement is outside hurricane-prep scope, use CAUTION with a short reason.
- Notes should be sentence-case, ≤30 words, no ALL CAPS, and must not repeat the verdict word.
- Overall:
  * If ANY verdict is FALSE -> overall FALSE
  * If ALL verdicts are TRUE -> overall SAFE
  * Else if ANY is MISLEADING -> overall MISLEADING
  * Else -> overall CAUTION
- If there are no items to evaluate -> overall CLEAR.
"""

# ----------------------------
# Light text cleanup + guardrails
# ----------------------------
def _clean_note(s: str) -> str:
    if not s:
        return ""
    s = " ".join(s.strip().split())
    # Strip echoes like "False:" or "MISLEADING -"
    s = re.sub(r'^(true|false|misleading|caution)\s*(—|-|:|\.)\s*', '', s, flags=re.I)
    # Tame shouty text
    if s.isupper():
        s = s.capitalize()
    else:
        s = s[:1].upper() + s[1:] if s else s
    # Keep it tight
    if len(s) > 240:
        s = s[:237].rstrip() + "…"
    return s

def _merge_overall(verdicts: list[str]) -> str:
    ups = [v.upper() for v in verdicts if v]
    if not ups:
        return "CLEAR"
    if any(v == "FALSE" for v in ups):
        return "FALSE"
    if ups and all(v == "TRUE" for v in ups):
        return "SAFE"
    if any(v == "MISLEADING" for v in ups):
        return "MISLEADING"
    return "CAUTION"

def _parse_json_or_fail(raw: str, items: List[str]) -> Dict[str, Any]:
    """
    Parse model text into our JSON shape, with a little resilience for code fences.
    """
    txt = raw.strip()
    if "```" in txt:
        a, b = txt.find("{"), txt.rfind("}")
        if a != -1 and b != -1 and b > a:
            txt = txt[a:b+1]

    obj = json.loads(txt)

    matches_out = []
    for m in obj.get("matches", []):
        pat = m.get("pattern", "")
        v = str(m.get("verdict", "")).upper()
        if v not in _VERDICT_WORDS:
            v = "CAUTION"
        note = _clean_note(m.get("note", ""))
        matches_out.append({"pattern": pat, "verdict": v, "note": note})

    overall = obj.get("overall") or _merge_overall([m["verdict"] for m in matches_out])
    overall = overall.upper()
    if overall not in ("SAFE", "FALSE", "MISLEADING", "CAUTION", "CLEAR"):
        overall = _merge_overall([m["verdict"] for m in matches_out])

    return {"overall": overall, "matches": matches_out}

# ----------------------------
# Public entry point
# ----------------------------
def verify_items_with_llm(items: List[str]) -> Dict[str, Any]:
    """
    Take user-entered lines, ask the LLM to rate each one,
    and return a clean JSON object the UI can render immediately.

    Shape:
      {
        "overall": "...",
        "matches": [
          {"pattern": "...", "verdict": "TRUE|FALSE|MISLEADING|CAUTION", "note": "..."}
        ]
      }
    """
    # Trim and filter up front so we don't pay for empty lines.
    items = [ln.strip() for ln in (items or []) if ln and ln.strip()]
    if not items:
        return {"overall": "CLEAR", "matches": []}

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return {"overall": "ERROR", "matches": [], "error": "Missing GOOGLE_API_KEY"}

    # Spin up a client; keep the error user-friendly.
    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        return {"overall": "ERROR", "matches": [], "error": f"Failed to init AI client: {e}"}

    user_block = "\n".join(items)
    prompt = f"{SYSTEM_PROMPT}\nItems:\n```\n{user_block}\n```"

    # One model, a couple retries, and a firm timeout—simple and predictable.
    def _once() -> str:
        resp = client.models.generate_content(model=PRIMARY_MODEL, contents=prompt)
        return (getattr(resp, "text", None) or "").strip()

    try:
        raw = _call_with_timeout(lambda: _retry_call(_once), PER_CALL_TIMEOUT_SEC)
        if not raw:
            return {"overall": "ERROR", "matches": [], "error": "Empty model response"}
        parsed = _parse_json_or_fail(raw, items)
        return parsed
    except concurrent.futures.TimeoutError:
        return {"overall": "ERROR", "matches": [], "error": f"Model timeout after {PER_CALL_TIMEOUT_SEC}s"}
    except Exception as e:
        msg = str(e)
        if "API key not valid" in msg or "API_KEY_INVALID" in msg:
            return {
                "overall": "ERROR",
                "matches": [],
                "error": "API key not valid. Check GOOGLE_API_KEY and key restrictions."
            }
        return {"overall": "ERROR", "matches": [], "error": f"Verifier failure: {e}"}
