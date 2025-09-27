from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any

class Verifier:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)

    def _load_rules(self):
        p = self.data_dir / "rumors.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        # default rules if file missing/corrupt
        return {
            "rules": [
                {"pattern": "drink seawater", "verdict": "FALSE", "note": "Seawater dehydrates you."},
                {"pattern": "open windows during hurricane", "verdict": "FALSE", "note": "Keep windows closed; board if advised."},
                {"pattern": "taping windows", "verdict": "MISLEADING", "note": "Tape is not a substitute for proper shutters."}
            ]
        }
    
    def check(self, text: str) -> Dict[str, Any]:
        rules = self._load_rules().get("rules", [])
        hits = []
        t = (text or "").lower()
        for r in rules:
            if r.get("pattern", "").lower() in t:
                hits.append({"pattern": r["pattern"], "verdict": r["verdict"], "note": r.get("note", "")})
        overall = "CLEAR" if not hits else ("CAUTION" if any(h["verdict"] != "FALSE" for h in hits) else "FALSE")
        return {"overall": overall, "matches": hits}