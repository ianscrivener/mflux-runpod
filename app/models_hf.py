"""MFlux models published on the mflux-community HF org (PRD: /models_hf, /models_hf/update)."""

import json
import os
import tempfile
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "models_hf.json"
HF_ORG = os.environ.get("HF_ORG", "mflux-community")


def _model_size_bytes(info) -> int:
    return sum(
        sibling.size or 0
        for sibling in info.siblings or []
        if sibling.size is not None
    )


def _model_entry(api, model_id: str) -> dict:
    info = api.model_info(model_id, files_metadata=True)
    return {
        "model_name": model_id,
        "size_gb": round(_model_size_bytes(info) / 1_000_000_000, 2),
        "upload_date": info.created_at.date().isoformat() if info.created_at else None,
        "upload_user": info.author,
        "commit_hash": info.sha,
    }


def hf_repo_size_gb(repo_id: str) -> float:
    """Total size in GB of an arbitrary HF repo's files -- used to size a
    series' ephemeral RunPod volume against its actual upstream source
    weights (e.g. Qwen-Image-Edit's ~63GB vs Fibo's much smaller footprint),
    rather than guessing a flat size for every series.

    Raises if HF reports zero total size -- a silently under-sized volume
    (e.g. from a gated repo returning no file metadata, or files_metadata
    not populating on some siblings) is worse than a loud failure, since it
    would only surface later as a mid-build "no space left on device"."""
    from huggingface_hub import HfApi

    token = os.environ.get("HF_TOKEN")
    api = HfApi(token=token)
    info = api.model_info(repo_id, files_metadata=True)
    size_bytes = _model_size_bytes(info)
    if size_bytes == 0:
        raise ValueError(
            f"HF reported zero total size for {repo_id!r} -- refusing to size "
            "a volume off this (likely missing files_metadata, e.g. a gated "
            "repo without access)"
        )
    return round(size_bytes / 1_000_000_000, 2)


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
