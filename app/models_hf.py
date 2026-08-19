"""MFlux models published on the mflux-community HF org (PRD: /models_hf, /models_hf/update)."""

import json
import os
import tempfile
from pathlib import Path

# Overridable via MODELS_HF_PATH so the deployed Orchestrator can put this on
# its mounted NetworkVolume (/runpod-volume), same as db.py does with
# REPORT_DB_PATH. Without that, the manifest is written to container-local
# disk and silently vanishes every time the worker idles out and scales to
# zero -- /models_hf would then return an empty list until someone re-ran
# /models_hf/update, which is exactly the bug this file used to have.
DATA_PATH = Path(
    os.environ.get(
        "MODELS_HF_PATH",
        Path(__file__).resolve().parent.parent / "data-hf-sync" / "models_hf.json",
    )
)
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


def scan_models_hf(organization: str | None = None) -> dict:
    from huggingface_hub import HfApi

    organization = organization or HF_ORG
    token = os.environ.get("HF_TOKEN")
    api = HfApi(token=token)
    model_ids = sorted(model.id for model in api.list_models(author=organization))
    models = [_model_entry(api, model_id) for model_id in model_ids]
    return {"hf_models": models}


def write_models_hf(manifest: dict, data_path: Path | None = None) -> None:
    # Default resolved at call time, not import time -- a module-level default
    # would capture DATA_PATH once and ignore later monkeypatching in tests
    # (the same late-binding bug previously fixed in app/db.py).
    data_path = data_path or DATA_PATH
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


def load_models_hf(data_path: Path | None = None) -> dict:
    data_path = data_path or DATA_PATH
    if not data_path.exists():
        return {"hf_models": []}
    try:
        with open(data_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        # A truncated/corrupt manifest (e.g. a crash mid-write, or a partially
        # written file on the network volume) should read as "nothing scanned
        # yet" so /models_hf and /models_missing degrade to an empty manifest
        # rather than 500ing the whole endpoint.
        return {"hf_models": []}


def update_models_hf(organization: str | None = None, data_path: Path | None = None) -> dict:
    # organization resolved at call time too, so HF_ORG can be set per-process
    # (e.g. from the Endpoint's env) rather than frozen at import.
    manifest = scan_models_hf(organization or HF_ORG)
    write_models_hf(manifest, data_path)

    from app.hf_datasets import push

    push("models_hf")

    return manifest
