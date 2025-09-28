# core/utils.py
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any, Tuple, List

# Where we keep a rolling record of what the app showed the user.
HISTORY_PATH = Path("data/history.json")


def validate_zip(zip_code: str, _zip_centroids: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Quick sanity-check for ZIP input.

    AI-first change:
      We no longer gate on a local ZIP dataset or downgrade to a default risk.
      If the format looks like a U.S. 5-digit ZIP, we let the AI/geo resolver
      take it from there. Otherwise we return a friendly correction message.

    Returns:
      (is_valid, message_for_user)
    """
    if not zip_code or not zip_code.isdigit() or len(zip_code) != 5:
        return False, "Please enter a 5-digit U.S. ZIP code (e.g., 33101)."
    return True, ""


def load_history() -> List[Dict[str, Any]]:
    """
    Read the on-disk history of recent runs.
    If the file is missing or unreadable, just start fresh.
    """
    if HISTORY_PATH.exists():
        try:
            return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def append_history(entry: Dict[str, Any], keep_last: int = 50) -> None:
    """
    Add a new row to history and keep it lean.
    We cap the list so the file stays small and fast to read.
    """
    hist = load_history()
    hist.append(entry)
    hist = hist[-keep_last:]
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(hist, indent=2), encoding="utf-8")
