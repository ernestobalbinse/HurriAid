# core/shelters.py
from __future__ import annotations

import os
import json
import hashlib
from typing import Any, Dict, List, Tuple

__all__ = ["read_shelters", "SheltersError", "is_open"]


class SheltersError(Exception):
    """Raised when shelters.json is unreadable or invalid."""


def _read_text_utf8_sig(path: str) -> str:
    """
    Read a file as text, tolerating UTF-8 BOM if present.
    """
    with open(path, "rb") as f:
        return f.read().decode("utf-8-sig")


def read_shelters(data_dir: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Read <data_dir>/shelters.json and return (shelters_list, debug_info).

    Accepts either:
      - a top-level list: [ {...}, {...} ]
      - or an object with "shelters": { "shelters": [ {...}, ... ] }

    Debug info includes file path, sha256, and mtime.
    Raises SheltersError on any problem.
    """
    path = os.path.abspath(os.path.join(data_dir, "shelters.json"))

    # Read & parse JSON, tolerant of BOM
    try:
        text = _read_text_utf8_sig(path)
    except Exception as e:
        raise SheltersError(f"Cannot read shelters file: {e}")

    try:
        obj = json.loads(text)
    except Exception as e:
        raise SheltersError(f"Invalid shelters JSON: {e}")

    # Normalize to a list
    if isinstance(obj, dict) and "shelters" in obj:
        shelters = obj["shelters"]
    else:
        shelters = obj

    if not isinstance(shelters, list):
        raise SheltersError("Shelters JSON must be a list or an object with a 'shelters' list.")

    # Prepare debug
    dbg: Dict[str, Any] = {
        "shelters_path": path,
        "shelters_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    try:
        dbg["shelters_mtime"] = os.path.getmtime(path)
    except Exception:
        pass

    return shelters, dbg


def is_open(entry: Dict[str, Any]) -> bool:
    """
    Determine whether a shelter entry is 'open'.

    Supported forms:
      - {"open": true/false}
      - {"status": "open"|"closed"|...} (case-insensitive)

    Defaults to False if ambiguous or missing.
    """
    if isinstance(entry.get("open"), bool):
        return entry["open"]

    status = str(entry.get("status", "")).strip().lower()
    return status == "open"
