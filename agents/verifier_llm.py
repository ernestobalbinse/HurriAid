# agents/verifier_llm.py
from __future__ import annotations
from typing import List, Dict, Any
import os, time, random, json, concurrent.futures, re

# pip install google-genai
from google import genai

PRIMARY_MODEL  = os.getenv("HURRIAID_MODEL", "gemini-2.0-flash")
FALLBACK_MODEL = os.getenv("HURRIAID_MODEL_FALLBACK", "")
PER_CALL_TIMEOUT_SEC = float(os.getenv("HURRIAID_LLM_TIMEOUT", "25"))
MAX_TRIES            = int(os.getenv("HURRIAID_LLM_RETRIES", "3"))

_VERDICT_WORDS = ("TRUE", "FALSE", "MISLEADING", "CAUTION")

def _retry_call(fn, max_tries=MAX_TRIES, base=0.8, cap=6.0):
    last = None
    for i in range(max_tries):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(min(cap, base * (2 ** i)) + random.uniform(0, 0.35))
    raise last

def _call_with_timeout(fn, timeout_sec: float):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        return fut.result(timeout=timeout_sec)

SYSTEM_PROMPT = """You are HurriAid Verifier, checking hurricane preparation/response claims.
Return ONLY valid JSON with this schema:

{
  "overall": "SAFE | FALSE | MISLEADING | CAUTION | CLEAR",
  "matches": [
    { "pattern": "<original statement>", "verdict": "TRUE|FALSE|MISLEADING|CAUTION", "note": "<≤30 words, no verdict words>" }
  ]
}

Rules:
- If outside hurricane-prep scope -> CAUTION with a short reason.
- Keep note sentence-case, ≤30 words, no ALL CAPS, do not repeat the verdict word in the note.
- Overall: ANY FALSE -> FALSE; ALL TRUE -> SAFE; else MISLEADING if any, otherwise CAUTION; if no items -> CLEAR.
"""

def _clean_note(s: str) -> str:
    if not s:
        return ""
    s = " ".join(s.strip().split())
    # remove leading verdict echoes like "FALSE —", "True:", "Misleading -"
    s = re.sub(r'^(true|false|misleading|caution)\s*(—|-|:|\.)\s*', '', s, flags=re.I)
    # basic sentence casing (avoid shouty ALL CAPS)
    if s.isupper():
        s = s.capitalize()
    else:
        s = s[:1].upper() + s[1:] if s else s
    # cap length
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
    txt = raw.strip()
    if "```" in txt:
        a, b = txt.find("{"), txt.rfind("}")
        if a != -1 and b != -1 and b > a:
            txt = txt[a:b+1]
    obj = json.loads(txt)
    matches_out = []
    for m in obj.get("matches", []):
        pat = m.get("pattern", "")
        v   = str(m.get("verdict","")).upper()
        if v not in _VERDICT_WORDS:
            v = "CAUTION"
        note = _clean_note(m.get("note",""))
        matches_out.append({"pattern": pat, "verdict": v, "note": note})
    overall = obj.get("overall") or _merge_overall([m["verdict"] for m in matches_out])
    overall = overall.upper()
    if overall not in ("SAFE","FALSE","MISLEADING","CAUTION","CLEAR"):
        overall = _merge_overall([m["verdict"] for m in matches_out])
    return {"overall": overall, "matches": matches_out}

def _list_candidates(client: genai.Client) -> list[str]:
    names = []
    try:
        names = [m.name for m in client.models.list()]
    except Exception:
        pass
    out = [PRIMARY_MODEL]
    if FALLBACK_MODEL:
        out.append(FALLBACK_MODEL)
    for n in names:
        if ("flash" in n.lower()) and (n not in out):
            out.append(n)
    for n in names:
        if n not in out:
            out.append(n)
    return out

def verify_items_with_llm(items: List[str]) -> Dict[str, Any]:
    # Trim inputs early
    items = [ln.strip() for ln in (items or []) if ln and ln.strip()]
    if not items:
        return {"overall": "CLEAR", "matches": []}

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return {"overall": "ERROR", "matches": [], "error": "Missing GOOGLE_API_KEY"}

    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        return {"overall": "ERROR", "matches": [], "error": f"Failed to init AI client: {e}"}

    user_block = "\n".join(items)
    prompt = f"{SYSTEM_PROMPT}\nItems:\n```\n{user_block}\n```"

    def _one_call(model_id: str) -> str:
        def _once():
            resp = client.models.generate_content(model=model_id, contents=prompt)
            return (getattr(resp, "text", None) or "").strip()
        return _call_with_timeout(lambda: _retry_call(_once), PER_CALL_TIMEOUT_SEC)

    last_err = None
    for model_id in _list_candidates(client):
        try:
            raw = _one_call(model_id)
            if not raw:
                last_err = f"Empty response from {model_id}"
                continue
            try:
                parsed = _parse_json_or_fail(raw, items)
                return parsed
            except Exception as jerr:
                last_err = f"Parse error from {model_id}: {jerr}"
                continue
        except concurrent.futures.TimeoutError:
            last_err = f"Timeout from {model_id}"
        except Exception as e:
            msg = str(e)
            if "API key not valid" in msg or "API_KEY_INVALID" in msg:
                return {"overall": "ERROR", "matches": [], "error": "API key not valid. Check GOOGLE_API_KEY and key restrictions."}
            last_err = f"{model_id} error: {e}"

    return {"overall": "ERROR", "matches": [], "error": last_err or "All models failed"}
