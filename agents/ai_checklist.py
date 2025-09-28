# agents/ai_checklist.py
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from core.parallel_exec import ADKNotAvailable
from core.adk_helpers import run_llm_agent_text_debug

# ---- Type-safe import pattern ----
try:
    from google.adk.agents.llm_agent import LlmAgent as _RuntimeLlmAgent
    from google.genai import types as genai_types
except Exception:
    _RuntimeLlmAgent = None  # type: ignore[assignment]
    genai_types = None

if TYPE_CHECKING:
    from google.adk.agents.llm_agent import LlmAgent as LlmAgentType
else:
    LlmAgentType = Any  # type: ignore[misc]


CHECKLIST_INSTRUCTION = """\
You are a hurricane readiness assistant creating a SHORT, risk-aware checklist for the next 12–24 hours.

## FACTS
- zip: {zip}
- risk: {risk}
- distance_km: {distance_km}
- radius_km: {radius_km}
- category: {category}
- inside_advisory: {inside}

## RULES
1) Output STRICT JSON with this shape (no extra text):
   {{"items": ["item 1", "item 2", "..."], "rationale": "1–2 sentence reason"}}
2) Number of items (N):
   - LOW   : N = {n_low_min}–{n_low_max} (light actions only)
   - MEDIUM: N = {n_med_min}–{n_med_max}
   - HIGH  : N = {n_high_min}–{n_high_max}
3) For LOW:
   - Prefer quick, low-effort actions: "Check official updates", "Charge phone & power bank", "Secure outdoor items", "Refuel car", "Review route".
   - DO NOT include multi-day stockpiles like "Water (1 gallon/person/day)" or "3-day food stock".
   - Keep it concise and realistic.
4) For MEDIUM:
   - Add core supplies if sheltering is plausible: drinking water (mention gallons), non-perishable food, meds, battery radio, small cash.
5) For HIGH:
   - Include full readiness: 3-day water & food, meds, first aid, flashlights, extra batteries, important documents (waterproof), evac plan, fuel, phone chargers.
6) Never use emojis or prefixes. Do not repeat the word "Checklist". Plain items only.
7) Items must be specific and deduplicated (e.g., "Charge phone and power bank" not two items).
8) Match tone to risk: LOW = calm/brief, HIGH = urgent but clear.

Return ONLY the JSON.
"""


def _build_checklist_agent() -> "LlmAgentType":
    if _RuntimeLlmAgent is None or genai_types is None:
        raise ADKNotAvailable("Google ADK is required (google-adk + google-genai).")
    return _RuntimeLlmAgent(
        name="RiskAwareChecklist",
        model="gemini-2.0-flash",
        description="Produces a concise hurricane readiness checklist sized to the current risk.",
        instruction="(set per call)",
        include_contents="none",
        generate_content_config=genai_types.GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=350,
        ),
    )


def _extract_json_block(text: Optional[str]) -> Dict[str, Any]:
    """
    Be tolerant to code fences or extra chatter; extract the first {...} block.
    """
    if not text:
        return {}
    # Strip markdown fences
    fenced = re.sub(r"^```(?:json)?\s*|```$", "", text.strip(), flags=re.MULTILINE)
    s = fenced.strip()
    # If still not raw JSON, try to slice first {..} region
    if not s.lstrip().startswith("{"):
        m = re.search(r"\{.*\}", s, flags=re.DOTALL)
        s = m.group(0) if m else s
    try:
        return json.loads(s)
    except Exception:
        return {}


def make_checklist_from_state(state: Dict[str, Any], zip_code: str) -> Tuple[List[str], Dict[str, Any], Optional[str]]:
    """
    Returns (items, debug, error)
      - items: list[str] checklist (empty on failure)
      - debug: dict with 'prompt', 'raw', 'events'
      - error: None or error string
    """
    adv = state.get("advisory") or {}
    analysis = state.get("analysis") or {}

    risk = str(analysis.get("risk", "LOW")).upper()
    distance_km = analysis.get("distance_km", None)
    radius_km = adv.get("radius_km", None)
    category = adv.get("category", "") or "TS"

    inside = False
    try:
        if isinstance(distance_km, (int, float)) and isinstance(radius_km, (int, float)):
            inside = float(distance_km) <= float(radius_km)
    except Exception:
        inside = False

    # Size by risk
    limits = {"LOW": (3, 4), "MEDIUM": (5, 7), "HIGH": (8, 12)}
    nmin, nmax = limits.get(risk, limits["LOW"])

    instruction = CHECKLIST_INSTRUCTION.format(
        zip=zip_code,
        risk=risk,
        distance_km=distance_km if distance_km is not None else "unknown",
        radius_km=radius_km if radius_km is not None else "unknown",
        category=category,
        inside=str(inside).lower(),
        n_low_min=limits["LOW"][0], n_low_max=limits["LOW"][1],
        n_med_min=limits["MEDIUM"][0], n_med_max=limits["MEDIUM"][1],
        n_high_min=limits["HIGH"][0], n_high_max=limits["HIGH"][1],
    )

    agent = _build_checklist_agent()
    # IMPORTANT: set instruction on the agent (helper has no 'instruction' kwarg)
    agent.instruction = instruction

    # The helper signature is (agent, prompt, app_name, user_id, session_id)
    text, events, err = run_llm_agent_text_debug(
        agent=agent,
        prompt="",                 # all context is in instruction
        app_name="hurri_aid",
        user_id="ui",
        session_id="sess_checklist",
    )

    dbg = {"prompt": instruction, "events": events, "raw": text}
    if err:
        return [], dbg, f"GENAI_ERROR:{err}"

    obj = _extract_json_block(text)
    items = obj.get("items")
    if not isinstance(items, list) or not all(isinstance(x, str) and x.strip() for x in items):
        return [], dbg, "PARSE_ITEMS_ERROR"

    # enforce upper bound and sanitize
    items = items[: nmax]
    cleaned: List[str] = []
    seen = set()
    for it in items:
        it2 = it.strip().rstrip(".")
        if it2.lower().startswith("checklist"):
            it2 = it2.split(":", 1)[-1].strip()
        if it2 and it2.lower() not in seen:
            cleaned.append(it2)
            seen.add(it2.lower())

    return cleaned, dbg, None
