from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any, Tuple

HISTORY_PATH = Path("data/history.json")

def validate_zip(zip_code: str, zip_centroids: Dict[str, Any]) -> Tuple[bool, str]:
    """Return (is_valid, message). Validates 5-digit format and presence in data."""
    if not zip_code or not zip_code.isdigit() or len(zip_code) != 5:
        return False, "Enter a 5-digit ZIP code (e.g., 33101)."
    if zip_code not in zip_centroids:
        return False, "ZIP not found in local dataset. Using LOW risk by default."
    return True, ""

def load_history() -> list:
    if HISTORY_PATH.exists():
        try:
            return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def append_history(entry: Dict[str, Any], keep_last: int = 50) -> None:
    hist = load_history()
    hist.append(entry)
    hist = hist[-keep_last:]
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(hist, indent=2), encoding="utf-8")