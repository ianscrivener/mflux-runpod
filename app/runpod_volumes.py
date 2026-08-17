"""Ephemeral per-model-series RunPod Network Volumes (PRD: Storage section).

One volume per upstream model series (e.g. "Qwen-Image"), created the first time
a missing quant is found for that series, deleted once every quant for that
series is uploaded and verified on HF. Holds downloaded source weights and
in-progress/completed quantized build artifacts so a crashed Runner job resumes
without rebuilding finished quants.
"""

import os

import httpx

RUNPOD_API_BASE = "https://rest.runpod.io/v1"
DEFAULT_DATA_CENTER_ID = os.environ.get("RUNPOD_DATA_CENTER_ID", "US-CA-2")
REQUEST_TIMEOUT = httpx.Timeout(30.0)


def _default_client() -> httpx.Client:
    return httpx.Client(timeout=REQUEST_TIMEOUT)


def _headers() -> dict:
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        raise RuntimeError("RUNPOD_API_KEY must be set in the environment")
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def volume_name_for_series(model_series: str) -> str:
    """Deterministic, human-readable volume name for a model series."""
    return f"mflux-{model_series}"


def list_volumes(client: httpx.Client | None = None) -> list[dict]:
    client = client or _default_client()
    response = client.get(f"{RUNPOD_API_BASE}/networkvolumes", headers=_headers())
    response.raise_for_status()
    return response.json()


def find_volume_for_series(model_series: str, client: httpx.Client | None = None) -> dict | None:
    name = volume_name_for_series(model_series)
    for volume in list_volumes(client):
        if volume.get("name") == name:
            return volume
    return None


def create_volume(
    model_series: str,
    size_gb: int = 100,
    data_center_id: str = DEFAULT_DATA_CENTER_ID,
    client: httpx.Client | None = None,
) -> dict:
    """Create (or return the existing) ephemeral volume for a model series."""
    existing = find_volume_for_series(model_series, client)
    if existing is not None:
        return existing

    client = client or _default_client()
    payload = {
        "name": volume_name_for_series(model_series),
        "dataCenterId": data_center_id,
        "size": size_gb,
    }
    response = client.post(
        f"{RUNPOD_API_BASE}/networkvolumes", headers=_headers(), json=payload
    )
    response.raise_for_status()
    return response.json()


def get_volume(volume_id: str, client: httpx.Client | None = None) -> dict:
    client = client or _default_client()
    response = client.get(
        f"{RUNPOD_API_BASE}/networkvolumes/{volume_id}", headers=_headers()
    )
    response.raise_for_status()
    return response.json()


def delete_volume(volume_id: str, client: httpx.Client | None = None) -> None:
    """Delete a model series' volume once every quant is uploaded and verified."""
    client = client or _default_client()
    response = client.delete(
        f"{RUNPOD_API_BASE}/networkvolumes/{volume_id}", headers=_headers()
    )
    response.raise_for_status()
