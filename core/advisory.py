# core/advisory.py
from __future__ import annotations

import os, json, hashlib, time
from typing import Dict, Any, Tuple

class AdvisoryError(Exception):
    """Raised when the advisory file is unreadable or invalid."""

def _to_float(x) -> float:
    if isinstance(x, (int, float)): return float(x)
    if isinstance(x, str):
        try: return float(x.strip())
        except Exception: pass
    raise AdvisoryError(f"Expected number, got: {x!r}")

def _to_bool(x) -> bool:
    if isinstance(x, bool): return x
    if isinstance(x, (int, float)): return bool(x)
    if isinstance(x, str):
        s = x.strip().lower()
        if s in {"true","1","yes","y","on"}:  return True
        if s in {"false","0","no","n","off"}: return False
    raise AdvisoryError(f"Expected boolean, got: {x!r}")

def read_advisory(data_dir: str) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """
    Read and validate <data_dir>/sample_advisory.json.
    Returns: (raw_json, normalized_advisory, debug_info)
    Raises AdvisoryError on any problem (invalid JSON or missing/invalid fields).
    """
    path = os.path.abspath(os.path.join(data_dir, "sample_advisory.json"))

    # --- read
    try:
        with open(path, "rb") as f:
            raw_bytes = f.read()
    except Exception as e:
        raise AdvisoryError(f"Cannot read advisory file: {e}")

    # --- parse JSON
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except Exception as e:
        raise AdvisoryError(f"Invalid JSON: {e}")

    # --- validate required fields
    if not isinstance(raw, dict):
        raise AdvisoryError("Top-level must be a JSON object.")

    if "center" not in raw or not isinstance(raw["center"], dict):
        raise AdvisoryError("Missing or invalid 'center' object.")

    if "lat" not in raw["center"] or "lon" not in raw["center"]:
        raise AdvisoryError("Missing 'center.lat' or 'center.lon'.")

    if "radius_km" not in raw:
        raise AdvisoryError("Missing 'radius_km'.")

    if "category" not in raw:
        raise AdvisoryError("Missing 'category'.")

    if "active" not in raw:
        raise AdvisoryError("Missing 'active'.")

    # --- normalize
    lat = _to_float(raw["center"]["lat"])
    lon = _to_float(raw["center"]["lon"])
    radius_km = _to_float(raw["radius_km"])
    category = str(raw["category"])
    active = _to_bool(raw["active"])
    issued_at = str(raw.get("issued_at",""))

    adv_norm = {
        "center": {"lat": lat, "lon": lon},
        "radius_km": radius_km,
        "category": category,
        "issued_at": issued_at,
        "active": active,
    }

    # --- debug breadcrumbs
    debug = {
        "advisory_path": path,
        "advisory_sha256": hashlib.sha256(raw_bytes).hexdigest(),
    }
    try:
        debug["advisory_mtime"] = os.path.getmtime(path)
    except Exception:
        pass
    # useful traceability
    debug["advisory_radius_source"] = "raw"
    debug["advisory_radius_raw_value"] = raw.get("radius_km")

    return raw, adv_norm, debug
