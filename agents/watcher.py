import json
from pathlib import Path


class Watcher:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)

    def get_advisory(self) -> dict:
        with open(self.data_dir / "sample_advisory.json", "r", encoding="utf-8") as f:
            return json.load(f)

    def get_zip_centroids(self) -> dict:
        with open(self.data_dir / "zip_centroids.json", "r", encoding="utf-8") as f:
            return json.load(f)

    def get_shelters(self) -> dict:
        with open(self.data_dir / "shelters.json", "r", encoding="utf-8") as f:
            return json.load(f)