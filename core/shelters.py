# core/shelters.py
from __future__ import annotations

import os
import json
import hashlib
from typing import Any, Dict, List, Tuple

__all__ = ["read_shelters", "SheltersError", "is_open"]


class SheltersError(Exception):
    """We couldn't read or parse shelters.json, or its shape wasn't usable."""


def _read_text_utf8_sig(path: str) -> str:
    """
    Read a text file and tolerate a UTF-8 BOM.
    (Some editors add one; we don't want that to break parsing.)
    """
    with open(path, "rb") as f:
        return f.read().decode("utf-8-sig")


def read_shelters(data_dir: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Load <data_dir>/shelters.json and return:
      (shelters_list, debug_info)

    File shape we accept:
      - A top-level list: [ {...}, {...} ]
      - Or an object with a "shelters" list: { "shelters": [ {...}, ... ] }

    The debug dict includes:
      - absolute path
      - sha256 of the raw file text
      - mtime (when available)

    Raises SheltersError with a clear message if anything goes wrong.
    """
    path = os.path.abspath(os.path.join(data_dir, "shelters.json"))

    # Read & parse JSON (be forgiving about BOMs).
    try:
        text = _read_text_utf8_sig(path)
    except Exception as e:
        raise SheltersError(f"Cannot read shelters file at {path}: {e}")

    try:
        obj = json.loads(text)
    except Exception as e:
        raise SheltersError(f"Invalid shelters JSON at {path}: {e}")

    # Normalize to a list.
    shelters = obj.get("shelters") if isinstance(obj, dict) else obj
    if not isinstance(shelters, list):
        raise SheltersError("Expected a list or an object with a 'shelters' list.")

    # Build simple debug info the UI can show.
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
    Decide if a shelter is open.

    Supported forms:
      - {"open": true/false}
      - {"status": "open" | "closed" | ...}  (case-insensitive)

    If it's unclear or missing, we treat it as closed.
    """
    if isinstance(entry.get("open"), bool):
        return entry["open"]

    status = str(entry.get("status", "")).strip().lower()
    return status == "open"
