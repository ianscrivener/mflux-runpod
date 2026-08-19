"""RunPod GPU type/pricing catalog (dataset #7, app.hf_datasets).

Feeds cost-per-model reporting: build/upload durations already land in
logs/conversions.jsonl per quant job; multiplying by the rate here (keyed by
GPU id) turns duration into an actual dollar figure. Refreshed on demand via
refresh_gpu_skus(), not scanned per-request -- pricing doesn't change that often.

Endpoint verified live 2026-08-19: GET https://rest.runpod.io/v1/gpus does NOT
exist (400, wrong path per RunPod's own OpenAPI spec) -- the real path is
GET https://api.runpod.io/v2/catalog/gpus, returning {"gpus": [...]}. This is
the SAME api.runpod.ai/api.runpod.io v2 host used for job dispatch/management
elsewhere in this project, not the rest.runpod.io v1 host app.runpod_volumes.py
uses -- a different RunPod API surface entirely, confirmed by fetching
https://api.runpod.io/v2/openapi.json and finding /v2/catalog/gpus there.
"""

import json
import os
import tempfile
from pathlib import Path

import httpx

RUNPOD_GPU_CATALOG_URL = "https://api.runpod.io/v2/catalog/gpus"
DATA_PATH = Path(__file__).resolve().parent.parent / "data-hf-sync" / "runpod_gpu_skus.json"
REQUEST_TIMEOUT = httpx.Timeout(30.0)


class RunpodSkusConfigError(RuntimeError):
    """Raised when RUNPOD_API_KEY isn't set -- config problem, not a bare 500."""


def _headers() -> dict:
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        raise RunpodSkusConfigError("RUNPOD_API_KEY must be set in the environment")
    return {"Authorization": f"Bearer {api_key}"}


def fetch_gpu_skus(client: httpx.Client | None = None) -> list[dict]:
    owns_client = client is None
    client = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        response = client.get(RUNPOD_GPU_CATALOG_URL, headers=_headers())
        response.raise_for_status()
        return response.json()["gpus"]
    finally:
        if owns_client:
            client.close()


def write_gpu_skus(skus: list[dict], data_path: Path | None = None) -> None:
    data_path = data_path or DATA_PATH
    data_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=data_path.parent, delete=False
    ) as tmp:
        json.dump(skus, tmp, indent=2)
        tmp_path = Path(tmp.name)
    tmp_path.replace(data_path)


def refresh_gpu_skus(client: httpx.Client | None = None, data_path: Path | None = None) -> list[dict]:
    from app.hf_datasets import push

    skus = fetch_gpu_skus(client)
    write_gpu_skus(skus, data_path)
    push("runpod_gpu_skus")
    return skus
