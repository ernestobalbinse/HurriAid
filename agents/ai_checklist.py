# agents/ai_checklist.py
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from core.parallel_exec import ADKNotAvailable
from core.adk_helpers import run_llm_agent_text_debug

# We only run with Google ADK + Gemini. If the SDK isn't present, we stop.
try:
    from google.adk.agents.llm_agent import LlmAgent as _RuntimeLlmAgent
    from google.genai import types as genai_types
except Exception:
    _RuntimeLlmAgent = None
    genai_types = None


def _build_agent():
    """
    Build a minimal LLM agent that produces a short, risk-aware checklist.
    Assumes ADK + Gemini are available; no rule-based fallbacks.
    """
    if _RuntimeLlmAgent is None or genai_types is None:
        raise ADKNotAvailable("Google ADK is required to generate the checklist.")

    return _RuntimeLlmAgent(
        name="RiskAwareChecklist",
        model="gemini-2.0-flash",
        description="Creates a short hurricane readiness checklist sized to risk.",
        instruction="(set per call)",  # We set a fresh instruction every request.
        include_contents="none",
        generate_content_config=genai_types.GenerateContentConfig(
            # Slightly higher temperature to avoid robotic phrasing, but still grounded.
            temperature=0.4,
            max_output_tokens=350,
        ),
        # This agent is terminal; it doesn't transfer control to other agents.
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )


# One clear instruction. We give the model just enough context to size the list sensibly.
INSTR = """\
You are a hurricane readiness assistant creating a SHORT, risk-aware checklist for the next 12–24 hours.

FACTS
- zip: {zip}
- risk: {risk}
- distance_mi: {distance_mi}
- radius_mi: {radius_mi}
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
- SAFE: advisory inactive or far; optionally 1–2 gentle reminders (e.g., "Save shelter link", "Verify emergency contacts"). No supplies.
- LOW : quick, low-effort actions only (avoid multi-day stockpiles).
- MEDIUM: add core supplies (water, food, meds, radio, cash).
- HIGH: full readiness (3-day water/food, meds, first aid, docs waterproofed, evac plan, fuel, chargers).
- Keep items specific and deduplicated. No emojis or prefixes.
"""


def _extract_first_json(s: Optional[str]) -> Dict[str, Any]:
    """Tolerate code fences or extra text; return the first valid JSON object."""
    if not s:
        return {}
    txt = s.strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.DOTALL)
    m = re.search(r"\{.*\}", txt, flags=re.DOTALL)
    if m:
        txt = m.group(0)
    try:
        return json.loads(txt)
    except Exception:
        return {}


def _to_miles(value: Optional[float], km_fallback: Optional[float]) -> Optional[float]:
    """Prefer miles if present; otherwise convert km → mi. Returns None if both missing."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(km_fallback, (int, float)):
        return float(km_fallback) * 0.621371
    return None


def make_checklist_from_state(state: Dict[str, Any], zip_code: str) -> Tuple[List[str], Dict[str, Any], Optional[str]]:
    """
    Build a risk-aware checklist using Gemini, based on the current app state.

    Returns:
        items: list of unique, concise checklist items (AI-sourced only)
        dbg:   lightweight debug info (raw text length, events count, risk used)
        err:   error string or None
    """
    adv: Dict[str, Any] = state.get("advisory") or {}
    analysis: Dict[str, Any] = state.get("analysis") or {}
    risk = str(analysis.get("risk", "LOW")).upper()
    category = adv.get("category", "TS")

    # Distance & radius in miles (we'll accept *_mi or convert *_km).
    distance_mi = _to_miles(analysis.get("distance_mi"), analysis.get("distance_km"))
    radius_mi = _to_miles(adv.get("radius_mi"), adv.get("radius_km"))

    # Are we inside the advisory circle? (best-effort; if unknown, treat as False)
    inside = False
    try:
        if isinstance(distance_mi, (int, float)) and isinstance(radius_mi, (int, float)):
            inside = float(distance_mi) <= float(radius_mi)
    except Exception:
        inside = False

    # Cap list size by risk. Tuned to feel practical, not overwhelming.
    limits = {"SAFE": (0, 2), "LOW": (3, 4), "MEDIUM": (5, 7), "HIGH": (8, 12)}
    nmin, nmax = limits.get(risk, limits["LOW"])

    # Build a one-off agent with a fresh instruction that embeds this ZIP/risk snapshot.
    agent = _build_agent()
    agent.instruction = INSTR.format(
        zip=zip_code,
        risk=risk,
        distance_mi=("unknown" if distance_mi is None else round(float(distance_mi), 1)),
        radius_mi=("unknown" if radius_mi is None else round(float(radius_mi), 1)),
        category=category,
        inside=str(inside).lower(),
        n_low_min=limits["LOW"][0],
        n_low_max=limits["LOW"][1],
        n_med_min=limits["MEDIUM"][0],
        n_med_max=limits["MEDIUM"][1],
        n_high_min=limits["HIGH"][0],
        n_high_max=limits["HIGH"][1],
    )

    # Single path: call the model. If it fails, bubble up a crisp error; no heuristics.
    text, events, err = run_llm_agent_text_debug(
        agent=agent,
        prompt="",               # instruction-only pattern
        app_name="hurri_aid",
        user_id="checklist",
        session_id="sess_checklist",
    )

    dbg = {
        "raw_len": len(text or ""),
        "events": len(events or []),
        "risk": risk,
        "units": "mi",
    }
    if err:
        return [], dbg, f"GENAI_ERROR:{err}"

    obj = _extract_first_json(text)
    items = obj.get("items")
    if not isinstance(items, list):
        return [], dbg, "PARSE_ITEMS_ERROR"

    # Clean, dedupe, and respect the nmax cap. Keep them short and to the point.
    cleaned: List[str] = []
    seen = set()
    for it in items:
        if not isinstance(it, str):
            continue
        s = it.strip()
        if not s:
            continue
        s = s.rstrip(".")  # read better as bullets
        k = s.lower()
        if k in seen:
            continue
        cleaned.append(s)
        seen.add(k)
        if len(cleaned) >= nmax:
            break

    return cleaned, dbg, None
