"""Cancel a dispatched RunPod job (PRD gap: dispatch_trigger never exposed a
way to stop a job it started). Same plain-httpx REST pattern as
app.runpod_volumes -- api.runpod.ai's job-control surface
(run/status/cancel), not rest.runpod.io's v1 management API.
"""

import os

import httpx

REQUEST_TIMEOUT = httpx.Timeout(30.0)


def _headers() -> dict:
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        raise RuntimeError("RUNPOD_API_KEY must be set in the environment")
    return {"Authorization": f"Bearer {api_key}"}


def cancel_job(endpoint_id: str, job_id: str, client: httpx.Client | None = None) -> dict:
    owns_client = client is None
    client = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        response = client.post(
            f"https://api.runpod.ai/v2/{endpoint_id}/cancel/{job_id}", headers=_headers()
        )
        response.raise_for_status()
        return response.json()
    finally:
        if owns_client:
            client.close()
