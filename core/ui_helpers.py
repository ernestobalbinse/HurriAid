# core/ui_helpers.py
from __future__ import annotations
from datetime import datetime, timezone
from typing import Tuple

# Color palette for the little "chips" in the UI.
BADGE_COLORS = {
    "green": "#16a34a",
    "amber": "#d97706",
    "red": "#dc2626",
    "gray": "#6b7280",
}

def badge(label: str, color: str = "gray") -> str:
    """
    Build a compact, colored pill as an HTML string.
    Use with Streamlit like:
      st.markdown(badge("RISK: HIGH", "red"), unsafe_allow_html=True)
    """
    bg = BADGE_COLORS.get(color, BADGE_COLORS["gray"])
    return (
        f"<span style='display:inline-block;padding:2px 8px;border-radius:999px;"
        f"font-size:12px;font-weight:600;background:{bg};color:white'>{label}</span>"
    )

def compute_freshness(issued_at_iso: str) -> Tuple[str, str]:
    """
    Convert an ISO timestamp to a quick freshness label for the UI.

    Returns (status, detail):
      - FRESH   : 0–30 min old
      - STALE   : 31–180 min old (min), then hours once >180 min
      - UNKNOWN : missing or unparseable timestamp

    Notes:
      - We treat 'Z' as UTC.
      - Negative ages (clock skew) clamp to zero—no scary negatives.
    """
    if not issued_at_iso:
        return "UNKNOWN", "No timestamp"

    try:
        # Accept both '...Z' and '+00:00' forms.
        t = datetime.fromisoformat(issued_at_iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)

        # Clamp to avoid negative when clocks drift.
        age_min = max(0, int((now - t).total_seconds() // 60))

        if age_min <= 30:
            return "FRESH", f"{age_min} min old"
        if age_min <= 180:
            return "STALE", f"{age_min} min old"
        return "STALE", f"{age_min // 60} h old"
    except Exception:
        return "UNKNOWN", "Unparseable timestamp"
