# agents/ai_checklist.py
from __future__ import annotations
import json, re
from typing import Any, Dict, List, Optional, Tuple

from core.parallel_exec import ADKNotAvailable
from core.adk_helpers import run_llm_agent_text_debug

# lazy ADK imports
try:
    from google.adk.agents.llm_agent import LlmAgent as _RuntimeLlmAgent
    from google.genai import types as genai_types
except Exception:
    _RuntimeLlmAgent = None
    genai_types = None

def _build_agent():
    if _RuntimeLlmAgent is None or genai_types is None:
        raise ADKNotAvailable("Google ADK is required.")
    return _RuntimeLlmAgent(
        name="RiskAwareChecklist",
        model="gemini-2.0-flash",
        description="Creates a short hurricane readiness checklist sized to risk.",
        instruction="(set per call)",
        include_contents="none",
        generate_content_config=genai_types.GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=350,
        ),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

INSTR = """\
You are a hurricane readiness assistant creating a SHORT, risk-aware checklist for the next 12–24 hours.

FACTS
- zip: {zip}
- risk: {risk}
- distance_km: {distance_km}
- radius_km: {radius_km}
- category: {category}
- inside_advisory: {inside}

OUTPUT
Return STRICT JSON only:
{{"items": ["item 1", "item 2", "..."], "rationale": "1–2 sentence reason"}}

SIZE by risk:
- SAFE  : 0–2 items (light reminders only)
- LOW   : {n_low_min}-{n_low_max}
- MEDIUM: {n_med_min}-{n_med_max}
- HIGH  : {n_high_min}-{n_high_max}

GUIDANCE
- SAFE: advisory inactive or far; optionally 1–2 gentle reminders (e.g., "Save local shelter link", "Verify emergency contacts"). No supplies.
- LOW : quick, low-effort actions (no multi-day stockpiles).
- MEDIUM: add core supplies (water, food, meds, radio, cash).
- HIGH: full readiness (3-day water/food, meds, first aid, docs waterproofed, evac plan, fuel, chargers).
- No emojis or prefixes. Items must be specific and deduplicated.
"""

def _extract_first_json(s: Optional[str]) -> Dict[str, Any]:
    if not s:
        return {}
    txt = s.strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.DOTALL)
    m = re.search(r"\{.*\}", txt, flags=re.DOTALL)
    if m: txt = m.group(0)
    try:
        return json.loads(txt)
    except Exception:
        return {}

def make_checklist_from_state(state: Dict[str, Any], zip_code: str) -> Tuple[List[str], Dict[str, Any], Optional[str]]:
    adv = state.get("advisory") or {}
    analysis = state.get("analysis") or {}
    risk = str(analysis.get("risk", "LOW")).upper()
    distance_km = analysis.get("distance_km")
    radius_km = adv.get("radius_km")
    category = adv.get("category", "TS")

    inside = False
    try:
        if isinstance(distance_km, (int,float)) and isinstance(radius_km, (int,float)):
            inside = float(distance_km) <= float(radius_km)
    except Exception:
        inside = False

    limits = {"SAFE": (0,2), "LOW": (3,4), "MEDIUM": (5,7), "HIGH": (8,12)}
    nmin, nmax = limits.get(risk, limits["LOW"])

    agent = _build_agent()
    agent.instruction = INSTR.format(
        zip=zip_code,
        risk=risk,
        distance_km=("unknown" if distance_km is None else distance_km),
        radius_km=("unknown" if radius_km is None else radius_km),
        category=category,
        inside=str(inside).lower(),
        n_low_min=limits["LOW"][0], n_low_max=limits["LOW"][1],
        n_med_min=limits["MEDIUM"][0], n_med_max=limits["MEDIUM"][1],
        n_high_min=limits["HIGH"][0], n_high_max=limits["HIGH"][1],
    )

    text, events, err = run_llm_agent_text_debug(
        agent=agent,
        prompt="",
        app_name="hurri_aid",
        user_id="checklist",
        session_id="sess_checklist",
    )

    dbg = {"raw": text, "events": len(events), "risk": risk}
    if err:
        return [], dbg, f"GENAI_ERROR:{err}"

    obj = _extract_first_json(text)
    items = obj.get("items")
    if not isinstance(items, list):
        return [], dbg, "PARSE_ITEMS_ERROR"

    # size & clean
    items = [str(x).strip().rstrip(".") for x in items if isinstance(x, str) and x.strip()]
    out: List[str] = []
    seen = set()
    for it in items:
        k = it.lower()
        if k not in seen:
            out.append(it)
            seen.add(k)
        if len(out) >= nmax:
            break
    return out, dbg, None
