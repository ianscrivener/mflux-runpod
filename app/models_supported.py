"""Models supported by the MFlux app (PRD: /models_supported).

For now this reads the static data/models_mflux.json snapshot. A later task
replaces this with a live scan of the MFlux GitHub repo.
"""

import json
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "models_mflux.json"


def load_models_supported(data_path: Path = DATA_PATH) -> dict:
    with open(data_path, encoding="utf-8") as f:
        return json.load(f)
