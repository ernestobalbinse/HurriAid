# agents/verifier_llm.py
from __future__ import annotations
from typing import List, Dict, Any
import os, time, random, json, concurrent.futures, re

# Google AI Studio SDK (pip install google-genai)
from google import genai

# ---------------- Config ----------------
PRIMARY_MODEL  = os.getenv("HURRIAID_MODEL", "gemini-2.0-flash")
FALLBACK_MODEL = os.getenv("HURRIAID_MODEL_FALLBACK", "")

PER_CALL_TIMEOUT_SEC = float(os.getenv("HURRIAID_LLM_TIMEOUT", "30"))
MAX_TRIES            = int(os.getenv("HURRIAID_LLM_RETRIES", "4"))

# Simple circuit breaker (process-scope)
_CB_FAILS = 0
_CB_OPEN_UNTIL = 0.0

# ---------------- System Prompt ----------------
SYSTEM_PROMPT = """You are “HurriAid Verifier,” a careful fact checker for hurricane preparedness and response claims.
Classify each statement as TRUE, FALSE, MISLEADING, or CAUTION, and provide a short, human explanation (“note”).
Return ONLY the JSON object in the schema below.

Scope:
- Hurricane safety, preparation, shelters, supplies, evacuation, power, water/food, medical basics, communications, generators, windows/doors, flooding, driving, immediate aftermath.
- If outside this scope, use CAUTION with a brief reason.

Ground rules:
- Be conservative with certainty. If guidance is mixed or context-dependent: MISLEADING (say what’s missing) or CAUTION (what’s unknown).
- Keep explanations factual, neutral, actionable. No fear-mongering or medical advice beyond widely accepted public guidance.
- Do NOT restate the verdict in `note` (avoid “False — …” / “True: …”).
- Style: English, sentence case, no ALL CAPS, ≤ 30 words per note.
- Never invent laws, exact locations, or live advisories. If conditions vary locally, say it may vary and advise checking official local sources.

Verdicts:
- TRUE — broadly correct and safe per standard guidance.
- FALSE — incorrect, unsafe, or contradicted by standard guidance.
- MISLEADING — has a grain of truth but missing critical context.
- CAUTION — unclear, unverifiable, or outside scope; needs official confirmation.

Output schema (return ONLY this JSON):
{
  "overall": "SAFE | FALSE | MISLEADING | CAUTION | CLEAR",
  "matches": [
    { "pattern": "<original statement>", "verdict": "TRUE|FALSE|MISLEADING|CAUTION", "note": "<≤30 words, no verdict words>" }
  ]
}

How to set overall:
- If there are no items or no safety concerns found, use "CLEAR" and matches: [].
- If ANY verdict is FALSE → overall = "FALSE".
- Else if ALL verdicts are TRUE → overall = "SAFE".
- Else overall = the most severe among {MISLEADING, CAUTION} present (prefer MISLEADING over CAUTION if both).

Task:
Classify each of these items and produce the JSON exactly as specified.
Items (one per line):
"""

# ---------------- Helpers ----------------
def _retry_call(fn, max_tries=MAX_TRIES, base=0.8, cap=6.0):
    last = None
    for i in range(max_tries):
        try:
            return fn()
        except Exception as e:
            last = e
            sleep_s = min(cap, base * (2 ** i)) + random.uniform(0, 0.35)  # jitter
            time.sleep(sleep_s)
    raise last

def _call_with_timeout(fn, timeout_sec: float):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        return fut.result(timeout=timeout_sec)

def _list_preferred_models(client: genai.Client) -> list[str]:
    try:
        names = [m.name for m in client.models.list()]
        # Prefer flash variants for speed/cost; otherwise anything available
        prefs = [n for n in names if "flash" in n.lower()]
        return prefs or names
    except Exception:
        return []

def _should_open_circuit(err_msg: str) -> bool:
    u = (err_msg or "").upper()
    return ("UNAVAILABLE" in u) or ("OVERLOADED" in u) or ("503" in u) or ("TIMEOUT" in u)

# ---- Note cleaning utilities ----
_VERDICT_WORDS = ("TRUE", "FALSE", "MISLEADING", "CAUTION")
_LEADING_VERDICT_RE = re.compile(r'^\s*(true|false|misleading|caution)\s*(—|-|:|\.)\s*', re.I)

def _limit_words(s: str, max_words: int = 30) -> str:
    words = s.split()
    if len(words) <= max_words:
        return s
    return " ".join(words[:max_words]).rstrip(",.;:") + "…"

def _sentence_case(s: str) -> str:
    """Convert ALL-CAPS to readable sentence case while preserving common acronyms."""
    if not s:
        return s
    s = " ".join(s.split())  # collapse whitespace
    ACRONYMS = ("FEMA", "NOAA", "NWS", "CDC")
    placeholders = {}
    for i, ac in enumerate(ACRONYMS):
        key = f"__ACR_{i}__"
        placeholders[key] = ac
        s = re.sub(rf"\b{ac}\b", key, s, flags=re.IGNORECASE)

    s = s.lower()
    if s:
        s = s[:1].upper() + s[1:]
    s = re.sub(r'([.!?]\s+)([a-z])', lambda m: m.group(1) + m.group(2).upper(), s)

    for key, ac in placeholders.items():
        s = s.replace(key, ac)
    return s

def _strip_verdict_echoes(text: str) -> str:
    """Remove verdict echoes like 'FALSE —', 'True:', and trailing '— FALSE'."""
    if not text:
        return text
    t = text.strip()
    t = _LEADING_VERDICT_RE.sub("", t).strip()
    t = re.sub(rf'\s*(—|-|:|–)\s*(?:{"|".join(_VERDICT_WORDS)})\.?\s*$', "", t, flags=re.IGNORECASE)
    return t.strip()

def _clean_note(raw: str) -> str:
    """Normalize model note: remove verdict words, sentence-case, ≤30 words."""
    s = _strip_verdict_echoes(raw or "")
    s = _sentence_case(s)
    s = _limit_words(s, 30)
    return s

def _merge_overall(matches: List[Dict[str, Any]]) -> str:
    if not matches:
        return "CLEAR"
    verdicts = [str(m.get("verdict", "")).upper() for m in matches]
    if any(v == "FALSE" for v in verdicts):
        return "FALSE"
    if verdicts and all(v == "TRUE" for v in verdicts):
        return "SAFE"
    if any(v == "MISLEADING" for v in verdicts):
        return "MISLEADING"
    return "CAUTION"

def _format_items_block(items: List[str]) -> str:
    return "\n".join(it.strip() for it in items if it.strip())

def _call_gen_content(client: genai.Client, model_id: str, text: str) -> str:
    """Single model call with timeout+retry; returns raw text."""
    def _once():
        resp = client.models.generate_content(model=model_id, contents=text)
        return (resp.text or "").strip()
    def _once_to():
        return _call_with_timeout(_once, PER_CALL_TIMEOUT_SEC)
    return _retry_call(_once_to)

def _parse_or_fallback(raw_text: str, items: List[str]) -> Dict[str, Any]:
    """
    Try to parse strict JSON per schema. If parsing fails, fall back to a
    conservative keyword classification and include a cleaned note.
    """
    txt = (raw_text or "").strip()
    if "```" in txt:  # strip code fences if present
        start, end = txt.find("{"), txt.rfind("}")
        if start != -1 and end != -1 and end > start:
            txt = txt[start:end+1]
    try:
        obj = json.loads(txt)
        # Normalize verdicts + notes
        matches = []
        for m in obj.get("matches", []):
            v = str(m.get("verdict", "")).upper()
            if v not in _VERDICT_WORDS:
                v = "CAUTION"
            note = _clean_note(m.get("note", ""))
            matches.append({"pattern": m.get("pattern", ""), "verdict": v, "note": note})
        overall = (obj.get("overall") or "").upper() or _merge_overall(matches)
        if overall not in ("SAFE", "FALSE", "MISLEADING", "CAUTION", "CLEAR"):
            overall = _merge_overall(matches)
        return {"overall": overall, "matches": matches}
    except Exception:
        # Fallback: naive keyword scan (VERY conservative)
        matches = []
        any_false = any_true = False
        for it in items:
            low = it.lower()
            if "open windows" in low or "drink seawater" in low or "drive through flood" in low:
                verdict, note = "FALSE", "Unsafe guidance that increases risk of injury or damage."
                any_false = True
            elif "taping windows" in low:
                verdict, note = "MISLEADING", "Tape does not strengthen glass; use shutters or impact-rated protection."
            elif "drink water" in low or "bottled water" in low or "three days of water" in low:
                verdict, note = "TRUE", "Storing and drinking clean water is recommended."
                any_true = True
            else:
                verdict, note = "CAUTION", "Outside scope or context dependent; check official local guidance."
            matches.append({"pattern": it, "verdict": verdict, "note": _clean_note(note)})
        overall = "FALSE" if any_false else ("SAFE" if matches and all(m["verdict"]=="TRUE" for m in matches) else ("MISLEADING" if any(m["verdict"]=="MISLEADING" for m in matches) else ("CAUTION" if matches else "CLEAR")))
        return {"overall": overall, "matches": matches}

# ---------------- Public API ----------------
def verify_items_with_llm(items: List[str]) -> Dict[str, Any]:
    """
    Classify user-provided rumor lines with Gemini (AI Studio).
    Returns: {"overall": "...", "matches": [...]} or {"overall":"ERROR", "matches":[], "error":"..."}
    """
    global _CB_FAILS, _CB_OPEN_UNTIL

    # Guard: empty input
    items = [it.strip() for it in (items or []) if it and it.strip()]
    if not items:
        return {"overall": "CLEAR", "matches": []}

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return {"overall": "ERROR", "matches": [], "error": "Missing GOOGLE_API_KEY"}

    now = time.time()
    if now < _CB_OPEN_UNTIL:
        return {"overall": "ERROR", "matches": [], "error": "LLM temporarily unavailable (cooling down); please retry."}

    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        return {"overall": "ERROR", "matches": [], "error": f"Failed to init AI client: {e}"}

    # Candidate models: primary → explicit fallback → discovered list
    candidates: list[str] = [PRIMARY_MODEL]
    if FALLBACK_MODEL:
        candidates.append(FALLBACK_MODEL)
    for n in _list_preferred_models(client):
        if n not in candidates:
            candidates.append(n)

    # Compose one-shot prompt (system + user)
    user_block = _format_items_block(items)
    full_prompt = f"{SYSTEM_PROMPT}\n```\n{user_block}\n```"

    last_error = None
    for model_id in candidates:
        try:
            raw = _call_gen_content(client, model_id, full_prompt)
            parsed = _parse_or_fallback(raw, items)
            # Success -> reset breaker and return
            _CB_FAILS = 0
            _CB_OPEN_UNTIL = 0.0
            return parsed
        except concurrent.futures.TimeoutError:
            last_error = f"LLM call failed on {model_id}: TIMEOUT"
        except Exception as e:
            last_error = f"LLM call failed on {model_id}: {e}"

        # Decide whether to open the circuit for transient overloads
        if _should_open_circuit(str(last_error)):
            _CB_FAILS += 1
            _CB_OPEN_UNTIL = time.time() + min(30, 5 * _CB_FAILS)  # 5s,10s,15s… capped at 30s

    return {"overall": "ERROR", "matches": [], "error": last_error or "All models failed."}
