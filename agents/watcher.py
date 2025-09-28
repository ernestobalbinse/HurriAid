# agents/watcher.py
from __future__ import annotations
import json
import math
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

import pgeocode


class Watcher:
    """
    Data access + geocoding helper.

    Expected advisory JSON schema (example):
    {
      "center": {"lat": 25.76, "lon": -80.19},
      "radius_km": 180.0,
      "issued_at": "2025-09-27T15:10:00Z",
      ... (other fields OK)
    }

    Expected shelters JSON: list of objects with at least lat/lon/name/open flag.
    """

    def __init__(self, data_dir: str = "data", fl_only: bool = True):
        self.data_dir = data_dir
        self.fl_only = fl_only
        self._nom = pgeocode.Nominatim("us")

    # ---------------- Files ----------------
    def _load_json(self, path: str) -> Any:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_advisory(self) -> Dict[str, Any]:
        """
        Load the current advisory JSON. Keep key names the UI expects:
        - center: {lat, lon}
        - radius_km: number
        - issued_at: string (optional but recommended)
        """
        # Try preferred filenames in order; stop at first that exists
        candidates = [
            os.path.join(self.data_dir, "advisory.json"),
            os.path.join(self.data_dir, "sample_advisory.json"),
        ]
        for p in candidates:
            if os.path.exists(p):
                try:
                    adv = self._load_json(p) or {}
                    # sanity: ensure center keys exist if present
                    center = adv.get("center") or {}
                    if not isinstance(center, dict) or "lat" not in center or "lon" not in center:
                        # malformed center; return empty so UI hides circle
                        return {}
                    # radius is optional, but needed for circle
                    if "radius_km" in adv:
                        try:
                            adv["radius_km"] = float(adv["radius_km"])
                        except Exception:
                            adv.pop("radius_km", None)
                    return adv
                except Exception:
                    # If file corrupt, fall through to empty
                    pass
        return {}

    def get_shelters(self) -> List[Dict[str, Any]]:
        """
        Load shelters list. Each item should include at least:
        - name
        - lat, lon
        - open (bool) or similar field your planner uses
        """
        candidates = [
            os.path.join(self.data_dir, "shelters.json"),
            os.path.join(self.data_dir, "sample_shelters.json"),
        ]
        for p in candidates:
            if os.path.exists(p):
                try:
                    data = self._load_json(p)
                    return data if isinstance(data, list) else []
                except Exception:
                    pass
        return []

    # ---------------- Geocoding ----------------
    @staticmethod
    def _norm_zip(z: str) -> str:
        z = str(z).strip()
        if len(z) > 5 and "-" in z:
            z = z.split("-", 1)[0]
        return z.zfill(5) if z.isdigit() else z

    @lru_cache(maxsize=5000)
    def get_zip_centroid(self, zip_code: str) -> Dict[str, Any]:
        """
        pgeocode lookup with robust NaN handling and optional FL-only filter.
        Returns: {"lat": float, "lon": float, "state": "FL", "zip": "33182"}
        Raises ValueError if not found or outside Florida when fl_only=True.
        """
        z = self._norm_zip(zip_code)
        rec = self._nom.query_postal_code(z)

        lat = rec.latitude if rec is not None else float("nan")
        lon = rec.longitude if rec is not None else float("nan")
        state_code = (rec.state_code if rec is not None else "") or ""

        if isinstance(lat, float) and math.isnan(lat):
            raise ValueError(f"Unknown ZIP: {z}")
        if isinstance(lon, float) and math.isnan(lon):
            raise ValueError(f"Unknown ZIP: {z}")

        if self.fl_only and state_code != "FL":
            raise ValueError(f"ZIP {z} is not in Florida (state_code={state_code or 'NA'})")

        return {"lat": float(lat), "lon": float(lon), "state": state_code, "zip": z}

    # Kept for compatibility; the UI no longer needs a big dict.
    def get_zip_centroids(self) -> Dict[str, Any]:
        return {}
