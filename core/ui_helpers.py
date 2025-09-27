# core/ui_helpers.py
from __future__ import annotations
from datetime import datetime, timezone
from typing import Tuple

# Simple badge helper (HTML string). Use with unsafe_allow_html=True in st.markdown.
BADGE_COLORS = {
    "green": "#16a34a",
    "amber": "#d97706",
    "red": "#dc2626",
    "gray": "#6b7280",
}

def badge(label: str, color: str = "gray") -> str:
    c = BADGE_COLORS.get(color, BADGE_COLORS["gray"])
    return (
        "<span style='display:inline-block;padding:2px 8px;border-radius:999px;"
        "font-size:12px;font-weight:600;background:{bg};color:white'>{label}</span>"
    ).format(bg=c, label=label)

def compute_freshness(issued_at_iso: str) -> Tuple[str, str]:
    """
    Returns (status, detail) where status ∈ {"FRESH","STALE","UNKNOWN"}.
    - FRESH: ≤ 30 minutes old
    - STALE: 31–180 minutes old or older (shows hours)
    - UNKNOWN: missing/unparseable timestamp
    """
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
