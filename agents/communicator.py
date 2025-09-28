# agents/communicator.py
from __future__ import annotations
from typing import List

def checklist_for_risk(risk: str) -> List[str]:
    """Deterministic, risk-aware checklist (English only)."""
    risk = (risk or "").upper()

    base = [
        "Water (3 days)",
        "Non-perishable food",
        "Medications",
        "Flashlight & batteries",
        "First aid kit",
        "Important documents in a waterproof bag",
        "Charge power banks",
        "Refuel vehicle to > 1/2 tank",
    ]
    medium = [
        "Check evacuation routes",
        "Secure windows/doors",
        "Pack go-bag (ID, cash, meds)",
    ]
    high = [
        "Plan to evacuate if officials advise",
        "Move to higher ground if flooding risk",
        "Keep radio/alerts on",
    ]

    if risk == "HIGH":
        return base + medium + high
    if risk == "MEDIUM":
        return base + medium
    if risk in ("LOW", "SAFE"):
        return base
    # Unknown = conservative
    return base + medium
