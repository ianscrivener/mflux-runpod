"""Post-deploy smoke test for the mflux-runner Flash endpoint.

Looks up the deployed "mflux-runner" serverless endpoint by name via the
RunPod REST API, dispatches a health_check job, and polls for the result.
Exits non-zero (failing the CI step) if the endpoint can't be found, the job
errors, or health_check reports status != "ok" -- e.g. the
"libcublasLt.so.13: cannot open shared object file" class of failure this
was written to catch, without burning a multi-minute quant build to find it.

Usage: RUNPOD_API_KEY=... python scripts/check_runner_health.py
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

RUNPOD_API_BASE = "https://rest.runpod.io/v1"
ENDPOINT_NAME = "mflux-runner-health"
POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 300


def _request(method: str, url: str, api_key: str, body: dict | None = None) -> dict | list:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"RunPod API error {exc.code} for {method} {url}: {exc.read().decode()}")


def find_endpoint_id(api_key: str) -> str:
    data = _request("GET", f"{RUNPOD_API_BASE}/endpoints", api_key)
    # /v1/endpoints returns a bare JSON list, not {"items": [...]}.
    endpoints = data if isinstance(data, list) else data.get("items", [])
    for ep in endpoints:
        if ep.get("name") == ENDPOINT_NAME:
            return ep["id"]
    raise SystemExit(f"No deployed endpoint named {ENDPOINT_NAME!r} found")


def main() -> None:
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        raise SystemExit("RUNPOD_API_KEY not set")

    endpoint_id = find_endpoint_id(api_key)
    print(f"Found endpoint {ENDPOINT_NAME} = {endpoint_id}")

    job = _request(
        "POST",
        f"https://api.runpod.ai/v2/{endpoint_id}/run",
        api_key,
        {"input": {"probe": True}},
    )
    job_id = job["id"]
    print(f"Dispatched health_check job {job_id}, polling...")

    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        status = _request(
            "GET", f"https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}", api_key
        )
        state = status.get("status")
        if state in ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"):
            print(json.dumps(status, indent=2))
            if state != "COMPLETED":
                raise SystemExit(f"health_check job ended in {state}")
            output = status.get("output", {})
            if output.get("status") != "ok":
                raise SystemExit(f"health_check reported failure: {output}")
            if not output.get("hf_token_configured"):
                # health_check's own status can be "ok" (mlx/mflux import and
                # CUDA device detection succeeded) even though the
                # RUNPOD_SECRET_HF_TOKEN reference silently failed to expand
                # -- catch that here rather than at the upload step of a
                # multi-minute build.
                raise SystemExit(
                    f"HF_TOKEN not injected (secret reference not expanding?): {output}"
                )
            print(f"OK: mlx_device={output.get('mlx_device')} "
                  f"hf_token_configured={output.get('hf_token_configured')}")
            return
        time.sleep(POLL_INTERVAL_S)

    raise SystemExit(f"health_check job {job_id} did not finish within {POLL_TIMEOUT_S}s")


if __name__ == "__main__":
    main()
