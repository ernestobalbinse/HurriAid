from typing import Dict, List

BASE_ITEMS = [
    "Water (3 days)",
    "Non-perishable food",
    "Medications",
    "Flashlight & batteries",
    "First aid kit",
    "Important documents in a waterproof bag",
]

RISK_EXTRAS = {
    "MEDIUM": [
        "Charge power banks",
        "Refuel vehicle to > 1/2 tank",
        "Check evacuation routes",
    ],
    "HIGH": [
        "Pack go-bag (ID, cash, meds)",
        "Secure windows/doors",
        "Plan to evacuate if officials advise",
    ],
}

def build_checklist(analysis: Dict) -> List[str]:
    risk = (analysis or {}).get("risk", "LOW").upper()
    items = list(BASE_ITEMS)
    if risk in ("MEDIUM", "HIGH"):
        items.extend(RISK_EXTRAS.get("MEDIUM", []))
    if risk == "HIGH":
        items.extend(RISK_EXTRAS.get("HIGH", []))
    # De-duplicate while preserving order
    seen = set()
    ordered = []
    for it in items:
        if it not in seen:
            seen.add(it)
            ordered.append(it)
    return ordered