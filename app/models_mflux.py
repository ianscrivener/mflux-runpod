"""Models supported by the MFlux app (PRD: /models_mflux, formerly /models_supported).

Reads data-hf-sync/models_mflux.json, kept current via app.hf_datasets' pull
from the upstream mflux CI bucket (see hf_datasets.yaml) rather than a live
GitHub scan from this module.
"""

import json
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data-hf-sync" / "models_mflux.json"


def load_models_mflux(data_path: Path = DATA_PATH) -> dict:
    with open(data_path, encoding="utf-8") as f:
        return json.load(f)
