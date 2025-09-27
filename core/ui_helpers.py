from __future__ import annotations
from datetime import datetime, timezone
from typing import Tuple


# Simple badge helper (uses HTML). Streamlit allows this via unsafe_allow_html=True.
# Colors: green, amber, red, gray
BADGE_COLORS = {
    "green": "#16a34a",
    "amber": "#d97706",
    "red": "#dc2626",
    "gray": "#6b7280",
}


def badge(label: str, color: str = "gray") -> str:
    c = BADGE_COLORS.get(color, BADGE_COLORS["gray"])
    return f"<span style='display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;background:{c};color:white'>{label}</span>"


def compute_freshness(issued_at_iso: str) -> Tuple[str, str]:
    """Return (status, detail). status in {FRESH, STALE, UNKNOWN}."""
    if not issued_at_iso:
        return "UNKNOWN", "No timestamp"
    try:
        t = datetime.fromisoformat(issued_at_iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_min = max(0, int((now - t).total_seconds() // 60))
        if age_min <= 30:
            return "FRESH", f"{age_min} min old"
        elif age_min <= 180:
            return "STALE", f"{age_min} min old"
        else:
            return "STALE", f"{age_min // 60} h old"
    except Exception:
        return "UNKNOWN", "Unparseable timestamp"