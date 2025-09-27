# agents/verifier_llm.py
from typing import List, Dict
import os, time, random, concurrent.futures
from google import genai

PRIMARY_MODEL  = os.getenv("HURRIAID_MODEL", "gemini-2.0-flash")
# Optional hint; will be used only if present. Otherwise we auto-pick.
FALLBACK_MODEL = os.getenv("HURRIAID_MODEL_FALLBACK", "")

# Circuit breaker (shared process memory). You can reset via env or on app restart.
_CB_FAILS = 0
_CB_OPEN_UNTIL = 0.0

PER_CALL_TIMEOUT_SEC = float(os.getenv("HURRIAID_LLM_TIMEOUT", "30"))
MAX_TRIES = int(os.getenv("HURRIAID_LLM_RETRIES", "4"))

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

def _classify_text(client: genai.Client, model_id: str, text: str) -> str:
    def _once():
        resp = client.models.generate_content(model=model_id, contents=text)
        return (resp.text or "").strip()
    def _once_to():
        return _call_with_timeout(_once, PER_CALL_TIMEOUT_SEC)
    return _retry_call(_once_to)

def _list_flash_like_models(client: genai.Client) -> list[str]:
    try:
        names = [m.name for m in client.models.list()]
        # Prefer light/cheap-ish flash variants, then anything else as last resort
        prefs = [n for n in names if "flash" in n.lower()]
        return prefs or names
    except Exception:
        return []

def _should_open_circuit(err_msg: str) -> bool:
    u = err_msg.upper()
    return ("UNAVAILABLE" in u) or ("OVERLOADED" in u) or ("503" in u) or ("TIMEOUT" in u)

def verify_items_with_llm(items: List[str]) -> Dict:
    global _CB_FAILS, _CB_OPEN_UNTIL

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return {"overall": "ERROR", "matches": [], "error": "Missing GOOGLE_API_KEY"}

    now = time.time()
    if now < _CB_OPEN_UNTIL:
        # Fast-fail while the circuit is open
        return {"overall": "ERROR", "matches": [], "error": "LLM temporarily unavailable (cooling down); please retry."}

    client = genai.Client(api_key=api_key)

    # Build candidate model list: primary → explicit fallback → auto-picked flash models
    candidates: list[str] = [PRIMARY_MODEL]
    if FALLBACK_MODEL:
        candidates.append(FALLBACK_MODEL)
    for n in _list_flash_like_models(client):
        if n not in candidates:
            candidates.append(n)

    last_error = None
    for model_id in candidates:
        out = _run_for_model(client, model_id, items)
        if out.get("overall") == "ERROR":
            last_error = out.get("error") or "Unknown LLM error"
            if _should_open_circuit(last_error):
                _CB_FAILS += 1
                # Open circuit for a short cool-off after repeated overloads
                _CB_OPEN_UNTIL = time.time() + min(30, 5 * _CB_FAILS)  # 5s,10s,15s… capped at 30s
                continue  # try next candidate
            else:
                # Non-transient error (e.g., bad key) — stop trying others
                return out
        else:
            # Success: reset breaker
            _CB_FAILS = 0
            _CB_OPEN_UNTIL = 0.0
            return out

    # If all candidates failed, return last error
    return {"overall": "ERROR", "matches": [], "error": last_error or "All models failed."}

def _run_for_model(client: genai.Client, model_id: str, items: List[str]) -> Dict:
    matches = []
    any_false = False
    any_true  = False

    for it in items:
        prompt = (
            "Classify the following hurricane preparation statement as TRUE, FALSE, or MISLEADING. "
            "Return the verdict word first, then a brief 1-sentence note.\n"
            f"Statement: {it}"
        )
        try:
            text = _classify_text(client, model_id, prompt).upper()
        except concurrent.futures.TimeoutError:
            return {"overall": "ERROR", "matches": [], "error": f"LLM call failed on {model_id}: TIMEOUT"}
        except Exception as e:
            return {"overall": "ERROR", "matches": [], "error": f"LLM call failed on {model_id}: {e}"}

        verdict = "MISLEADING"
        if "FALSE" in text:
            verdict = "FALSE"; any_false = True
        elif "TRUE" in text:
            verdict = "TRUE";  any_true  = True

        matches.append({"pattern": it, "verdict": verdict, "note": text})

    if any_false:
        overall = "FALSE"
    elif matches and all(m["verdict"] == "TRUE" for m in matches):
        overall = "SAFE"
    else:
        overall = "CAUTION" if matches else "CLEAR"

    return {"overall": overall, "matches": matches}
