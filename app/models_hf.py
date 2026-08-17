"""MFlux models published on the mflux-community HF org (PRD: /models_hf, /models_hf/update)."""

import json
import os
import tempfile
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "models_hf.json"
HF_ORG = os.environ.get("HF_ORG", "mflux-community")


def _model_entry(api, model_id: str) -> dict:
    info = api.model_info(model_id, files_metadata=True)
    size_bytes = sum(
        sibling.size or 0
        for sibling in info.siblings or []
        if sibling.size is not None
    )
    return {
        "model_name": model_id,
        "size_gb": round(size_bytes / 1_000_000_000, 2),
        "upload_date": info.created_at.date().isoformat() if info.created_at else None,
        "upload_user": info.author,
        "commit_hash": info.sha,
    }


def scan_models_hf(organization: str = HF_ORG) -> dict:
    from huggingface_hub import HfApi

    token = os.environ.get("HF_TOKEN")
    api = HfApi(token=token)
    model_ids = sorted(model.id for model in api.list_models(author=organization))
    models = [_model_entry(api, model_id) for model_id in model_ids]
    return {"hf_models": models}


def write_models_hf(manifest: dict, data_path: Path = DATA_PATH) -> None:
    data_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=data_path.parent,
        delete=False,
    ) as tmp:
        json.dump(manifest, tmp, indent=2)
        tmp_path = Path(tmp.name)
    tmp_path.replace(data_path)


def load_models_hf(data_path: Path = DATA_PATH) -> dict:
    if not data_path.exists():
        return {"hf_models": []}
    with open(data_path, encoding="utf-8") as f:
        return json.load(f)


def update_models_hf(organization: str = HF_ORG, data_path: Path = DATA_PATH) -> dict:
    manifest = scan_models_hf(organization)
    write_models_hf(manifest, data_path)
    return manifest
