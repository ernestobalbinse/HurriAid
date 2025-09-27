import json
from pathlib import Path
from typing import Dict, Any

class Watcher:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)

    # Offline reads
    def _read_json(self, name: str):
        with open(self.data_dir / name, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_advisory_offline(self) -> Dict[str, Any]:
        return self._read_json("sample_advisory.json")
    
    def get_zip_centroids(self) -> Dict[str, Any]:
        return self._read_json("zip_centroids.json")
    
    def get_shelters(self):
        return self._read_json("shelters.json")
    
    # Online stubs — structure in place for future live fetch
    def get_advisory_online(self) -> Dict[str, Any]:
        # TODO: replace with a real fetch (API / file). For now, reuse offline data.
        return self.get_advisory_offline()
    
    # Facade used by Coordinator
    def get_advisory(self, offline: bool = True) -> Dict[str, Any]:
        return self.get_advisory_offline() if offline else self.get_advisory_online()